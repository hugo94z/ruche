"""Journal d'historique répliqué et signé.

Chaque message est une entrée immuable identifiée par un UUID et horodatée par
une horloge de Lamport. Tous les pairs d'un salon conservent une copie complète
du journal : si l'hôte se déconnecte, l'historique survit chez les autres.

Chaque entrée est **signée** par son auteur (Ed25519). Comme l'identifiant d'un
pair est dérivé de sa clé publique, une entrée falsifiée ou attribuée à un autre
est rejetée à la fusion.
"""

from __future__ import annotations

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
        # Entrées reçues dont la signature est invalide ou absente.
        self.rejected: list[str] = []

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
        return entry

    def display(self, entry: dict) -> dict | None:
        """Vue lisible d'une entrée."""
        return entry

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
        return added

    # --- Lecture ----------------------------------------------------------
    def all_sorted(self) -> list[dict]:
        return sorted(self._entries.values(), key=sort_key)

    def raw_entries(self) -> list[dict]:
        """Entrées telles qu'elles circulent (à répliquer telles quelles)."""
        return self.all_sorted()

    def known_ids(self) -> set[str]:
        return set(self._entries)
