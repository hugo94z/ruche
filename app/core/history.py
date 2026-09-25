"""Journal d'historique répliqué et signé.

Chaque message est une entrée immuable identifiée par un UUID et horodatée par
une horloge de Lamport. Tous les pairs d'un salon conservent une copie complète
du journal : si l'hôte se déconnecte, l'historique survit chez les autres.

Chaque entrée est **signée** par son auteur (Ed25519). Comme l'identifiant d'un
pair est dérivé de sa clé publique, une entrée falsifiée ou attribuée à un autre
est rejetée à la fusion.

L'édition, la suppression et les réactions n'écrivent jamais dans l'entrée
d'origine : elles ajoutent des **opérations** signées qui la référencent
(``extra.target``). La vue affichée est ensuite recalculée en repliant le journal
dans l'ordre du temps.
"""

from __future__ import annotations

import time
import uuid
from typing import Iterable

from . import crypto
from .storage import Storage

# Opérations qui ne sont pas des messages en soi mais modifient un message.
OP_KINDS = ("edit", "delete", "reaction")


def sort_key(entry: dict) -> tuple:
    return (int(entry["ts"]), entry["origin"], entry["id"])


class HistoryLog:
    def __init__(self, storage: Storage) -> None:
        self._storage = storage
        self._entries: dict[str, dict] = {}
        self._lamport = 0
        self.room: str | None = None
        self.private_key: str = ""
        self.public_key: str = ""
        # Entrées reçues dont la signature est invalide ou absente.
        self.rejected: list[str] = []
        # Vues recalculées (message courant, édité, réactions).
        self._views: dict[str, dict] = {}
        self._reactions: dict[tuple[str, str], set[str]] = {}
        self._pseudo: dict[str, str] = {}

    # --- Configuration ----------------------------------------------------
    def configure(self, private_key: str, public_key: str) -> None:
        self.private_key = private_key
        self.public_key = public_key

    # --- Vérification -----------------------------------------------------
    def _authentic(self, entry: dict) -> bool:
        """Signature valide et identifiant cohérent avec la clé publique."""
        public = entry.get("pub")
        signature = entry.get("sig")
        if not public or not signature:
            return False  # entrée non signée : refusée
        if crypto.peer_id_for(public) != entry.get("origin"):
            return False  # identifiant usurpé
        return crypto.verify(public, entry, signature)

    # --- Chargement / rattachement à un salon ----------------------------
    def load_room(self, room: str) -> list[dict]:
        self.room = room
        self._entries.clear()
        self._lamport = 0
        for entry in self._storage.load_messages(room):
            self._entries[entry["id"]] = entry
            self._lamport = max(self._lamport, int(entry["ts"]))
        self._rebuild()
        return self.all_sorted()

    # --- Écriture locale --------------------------------------------------
    def add_local(
        self,
        kind: str,
        body: str,
        *,
        origin: str,
        pseudo: str,
        extra: dict | None = None,
    ) -> dict:
        if self.room is None:
            raise RuntimeError("Aucun salon chargé dans le journal.")
        if not origin:
            raise ValueError("L'entrée doit porter un 'origin'.")

        self._lamport += 1
        entry = {
            "id": uuid.uuid4().hex,
            "ts": self._lamport,
            "origin": origin,
            "pseudo": pseudo,
            "kind": kind,
            "body": body,
            "extra": extra or {},
            "at": time.time(),
            "pub": self.public_key,
        }
        if self.private_key:
            entry["sig"] = crypto.sign(self.private_key, entry)

        self._entries[entry["id"]] = entry
        self._storage.add_message(entry, self.room)
        self._rebuild()
        return entry

    def display(self, entry: dict) -> dict | None:
        """Vue lisible : pour une opération, la vue du message visé."""
        kind = entry.get("kind")
        if kind in OP_KINDS:
            target = (entry.get("extra") or {}).get("target")
            if not target:
                return None
            view = self._views.get(target)
            if view is None and kind == "delete":
                # Cible inconnue mais supprimée : on signale le retrait.
                return {"id": target, "kind": "delete", "deleted": True, "_op": kind}
            if view is None:
                return None
            view = dict(view)
            view["_op"] = kind
            return view
        return self._views.get(entry.get("id"))

    # --- Fusion (réplication) --------------------------------------------
    def merge(self, entries: Iterable[dict]) -> list[dict]:
        """Intègre des entrées distantes. Renvoie celles réellement nouvelles
        et authentiques."""
        if self.room is None:
            return []
        added: list[dict] = []
        for entry in entries:
            eid = entry.get("id")
            if not eid or eid in self._entries:
                continue
            entry.setdefault("extra", {})
            if not self._authentic(entry):
                self.rejected.append(eid)
                continue
            self._entries[eid] = entry
            self._lamport = max(self._lamport, int(entry.get("ts", 0)))
            self._storage.add_message(entry, self.room)
            added.append(entry)
        if added:
            self._rebuild()
        return added

    # --- Vues (repliement du journal) ------------------------------------
    def _rebuild(self) -> None:
        self._views = {}
        self._reactions = {}
        self._pseudo = {}
        for entry in self.all_sorted():
            self._apply(entry)
        # Attache les réactions aux messages visibles.
        for (target, emoji), origins in self._reactions.items():
            view = self._views.get(target)
            if view is None or not origins:
                continue
            names = sorted(
                self._pseudo.get(origin, origin[:8]) for origin in origins
            )
            view.setdefault("reactions", {})[emoji] = names

    def _apply(self, entry: dict) -> None:
        kind = entry.get("kind")
        origin = entry.get("origin", "")
        if kind == "edit":
            target = (entry.get("extra") or {}).get("target")
            view = self._views.get(target)
            if view is not None and view.get("origin") == origin:
                view["body"] = entry.get("body", "")
                view["edited"] = True
            return
        if kind == "delete":
            target = (entry.get("extra") or {}).get("target")
            view = self._views.get(target)
            if view is not None and view.get("origin") == origin:
                self._views.pop(target, None)
                self._reactions = {
                    key: val for key, val in self._reactions.items() if key[0] != target
                }
            return
        if kind == "reaction":
            target = (entry.get("extra") or {}).get("target")
            emoji = entry.get("body", "")
            if not target or not emoji:
                return
            key = (target, emoji)
            origins = self._reactions.setdefault(key, set())
            if entry.get("pseudo"):
                self._pseudo.setdefault(origin, entry["pseudo"])
            if (entry.get("extra") or {}).get("remove"):
                origins.discard(origin)
            else:
                origins.add(origin)
            return
        # Message ordinaire.
        view = dict(entry)
        view.setdefault("reactions", {})
        self._views[entry["id"]] = view
        self._pseudo[origin] = entry.get("pseudo", "")

    def has_reaction(self, target: str, emoji: str, origin: str) -> bool:
        return origin in self._reactions.get((target, emoji), set())

    # --- Lecture ----------------------------------------------------------
    def all_sorted(self) -> list[dict]:
        return sorted(self._entries.values(), key=sort_key)

    def all_views(self) -> list[dict]:
        """Messages courants (hors opérations et messages supprimés)."""
        return sorted(self._views.values(), key=sort_key)

    def raw_entries(self) -> list[dict]:
        """Entrées telles qu'elles circulent (à répliquer telles quelles)."""
        return self.all_sorted()

    def known_ids(self) -> set[str]:
        return set(self._entries)
