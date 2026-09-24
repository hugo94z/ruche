"""Journal d'historique répliqué.

Chaque message est une entrée immuable identifiée par un UUID et horodatée
avec une horloge de Lamport. Tous les pairs d'un salon conservent une copie
complète du journal : si l'hôte se déconnecte, l'historique survit chez les
autres, et la fusion à la reconnexion est sans doublon ni perte.
"""

from __future__ import annotations

import time
import uuid
from typing import Iterable

from .storage import Storage


def sort_key(entry: dict) -> tuple:
    return (int(entry["ts"]), entry["origin"], entry["id"])


class HistoryLog:
    def __init__(self, storage: Storage) -> None:
        self._storage = storage
        self._entries: dict[str, dict] = {}
        self._lamport = 0
        self.room: str | None = None

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
        }
        self._entries[entry["id"]] = entry
        self._storage.add_message(entry, self.room)
        return entry

    # --- Fusion (réplication) --------------------------------------------
    def merge(self, entries: Iterable[dict]) -> list[dict]:
        """Intègre des entrées distantes. Renvoie celles réellement nouvelles."""
        if self.room is None:
            return []
        added: list[dict] = []
        for entry in entries:
            eid = entry.get("id")
            if not eid or eid in self._entries:
                continue
            entry.setdefault("extra", {})
            self._entries[eid] = entry
            self._lamport = max(self._lamport, int(entry.get("ts", 0)))
            self._storage.add_message(entry, self.room)
            added.append(entry)
        return added

    # --- Lecture ----------------------------------------------------------
    def all_sorted(self) -> list[dict]:
        return sorted(self._entries.values(), key=sort_key)

    def known_ids(self) -> set[str]:
        return set(self._entries)
