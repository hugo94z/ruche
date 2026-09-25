"""Journal d'historique répliqué, signé et (au besoin) chiffré.

Chaque message est une entrée immuable identifiée par un UUID et horodatée par
une horloge de Lamport. Tous les pairs d'un salon conservent une copie complète
du journal : si l'hôte se déconnecte, l'historique survit chez les autres.

Deux protections s'ajoutent :

* **signature Ed25519** — chaque entrée est signée par son auteur. Comme
  l'identifiant d'un pair est dérivé de sa clé publique, une entrée falsifiée ou
  attribuée à un autre est rejetée à la fusion.
* **chiffrement de bout en bout** — si un mot de passe de salon est défini, le
  contenu (corps, pseudo, type, métadonnées) est chiffré. Le rendez-vous et les
  relais ne voient alors que des métadonnées d'ordonnancement.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Iterable

from . import crypto
from .storage import Storage


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
        self.room_key: bytes | None = None
        # Entrées reçues dont la signature est invalide ou absente.
        self.rejected: list[str] = []

    # --- Configuration ----------------------------------------------------
    def configure(self, private_key: str, public_key: str, room_key: bytes | None) -> None:
        self.private_key = private_key
        self.public_key = public_key
        self.room_key = room_key

    # --- Chiffrement ------------------------------------------------------
    def _encode_payload(self, kind: str, body: str, pseudo: str, extra: dict) -> dict:
        """Renvoie les champs visibles d'une entrée (chiffrés si nécessaire)."""
        if self.room_key is None:
            return {"kind": kind, "body": body, "pseudo": pseudo, "extra": extra}
        payload = json.dumps(
            {"kind": kind, "body": body, "pseudo": pseudo, "extra": extra},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return {"kind": "enc", "body": crypto.encrypt(self.room_key, payload), "pseudo": "", "extra": {}}

    def _decode(self, entry: dict) -> dict | None:
        """Reconstruit les champs lisibles d'une entrée (déchiffre au besoin)."""
        if entry.get("kind") != "enc":
            return entry
        if self.room_key is None:
            return None  # chiffré, mais on n'a pas le mot de passe
        raw = crypto.decrypt(self.room_key, entry.get("body", ""))
        if raw is None:
            return None  # mauvais mot de passe ou altération
        try:
            payload = json.loads(raw)
        except ValueError:
            return None
        view = dict(entry)
        view["kind"] = payload.get("kind", "text")
        view["body"] = payload.get("body", "")
        view["pseudo"] = payload.get("pseudo", "")
        view["extra"] = payload.get("extra") or {}
        return view

    def can_read(self, entry: dict) -> bool:
        return self._decode(entry) is not None

    # --- Vérification -----------------------------------------------------
    def _authentic(self, entry: dict) -> bool:
        """Signature valide et identifiant cohérent avec la clé publique."""
        public = entry.get("pub")
        signature = entry.get("sig")
        if not public or not signature:
            return False  # entrée non signée (ancienne version) : refusée
        if crypto.peer_id_for(public) != entry.get("origin"):
            return False  # identifiant usurpé
        return crypto.verify(public, entry, signature)

    # --- Chargement / rattachement à un salon ----------------------------
    def load_room(self, room: str) -> list[dict]:
        self.room = room
        self._entries.clear()
        self._lamport = 0
        visible: list[dict] = []
        for raw in self._storage.load_messages(room):
            self._entries[raw["id"]] = raw
            self._lamport = max(self._lamport, int(raw["ts"]))
            view = self._decode(raw)
            if view is not None:
                visible.append(view)
        return sorted(visible, key=sort_key)

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
            "at": time.time(),
            "pub": self.public_key,
            **self._encode_payload(kind, body, pseudo, extra or {}),
        }
        if self.private_key:
            entry["sig"] = crypto.sign(self.private_key, entry)

        self._entries[entry["id"]] = entry
        self._storage.add_message(entry, self.room)
        return entry

    def display(self, entry: dict) -> dict | None:
        """Vue lisible d'une entrée (déchiffrée le cas échéant)."""
        return self._decode(entry)

    # --- Fusion (réplication) --------------------------------------------
    def merge(self, entries: Iterable[dict]) -> list[dict]:
        """Intègre des entrées distantes. Renvoie celles réellement nouvelles
        et lisibles (signature valide, déchiffrables)."""
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
            view = self._decode(entry)
            if view is not None:
                added.append(view)
        return added

    # --- Lecture ----------------------------------------------------------
    def all_sorted(self) -> list[dict]:
        views = []
        for raw in self._entries.values():
            view = self._decode(raw)
            if view is not None:
                views.append(view)
        return sorted(views, key=sort_key)

    def raw_entries(self) -> list[dict]:
        """Entrées telles qu'elles circulent (à répliquer telles quelles)."""
        return sorted(self._entries.values(), key=sort_key)

    def known_ids(self) -> set[str]:
        return set(self._entries)
