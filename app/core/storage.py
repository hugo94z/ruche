"""Stockage local (SQLite) : historique des messages et salons récents."""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id         TEXT PRIMARY KEY,
    room       TEXT NOT NULL,
    ts         INTEGER NOT NULL,
    origin     TEXT NOT NULL,
    pseudo     TEXT NOT NULL,
    kind       TEXT NOT NULL,
    body       TEXT NOT NULL,
    extra      TEXT NOT NULL DEFAULT '{}',
    at         REAL NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_room ON messages(room, ts, origin, id);

CREATE TABLE IF NOT EXISTS rooms (
    code      TEXT PRIMARY KEY,
    joined_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS files (
    id       TEXT PRIMARY KEY,
    name     TEXT NOT NULL,
    size     INTEGER NOT NULL DEFAULT 0,
    mime     TEXT NOT NULL DEFAULT '',
    sha256   TEXT NOT NULL DEFAULT '',
    path     TEXT,
    added_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS peers (
    peer_id    TEXT PRIMARY KEY,
    public_key TEXT NOT NULL DEFAULT '',
    pseudo     TEXT NOT NULL DEFAULT '',
    verified   INTEGER NOT NULL DEFAULT 0,
    blocked    INTEGER NOT NULL DEFAULT 0,
    muted      INTEGER NOT NULL DEFAULT 0,
    first_seen REAL NOT NULL,
    last_seen  REAL NOT NULL
);
"""


class Storage:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # --- Messages ---------------------------------------------------------
    def add_message(self, entry: dict, room: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO messages"
                " (id, room, ts, origin, pseudo, kind, body, extra, at, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entry["id"],
                    room,
                    int(entry["ts"]),
                    entry["origin"],
                    entry["pseudo"],
                    entry["kind"],
                    entry["body"],
                    _json(entry.get("extra") or {}),
                    float(entry.get("at") or 0),
                    time.time(),
                ),
            )
            self._conn.commit()

    def load_messages(self, room: str, limit: int = 1000) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, ts, origin, pseudo, kind, body, extra, at FROM messages"
                " WHERE room = ? ORDER BY ts ASC, origin ASC, id ASC LIMIT ?",
                (room, limit),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "ts": row["ts"],
                "origin": row["origin"],
                "pseudo": row["pseudo"],
                "kind": row["kind"],
                "body": row["body"],
                "extra": _json_load(row["extra"]),
                "at": row["at"],
            }
            for row in rows
        ]

    # --- Fichiers ---------------------------------------------------------
    def upsert_file(self, rec: dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO files (id, name, size, mime, sha256, path, added_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(id) DO UPDATE SET"
                "   name=excluded.name, size=excluded.size, mime=excluded.mime,"
                "   sha256=excluded.sha256, path=COALESCE(excluded.path, files.path)",
                (
                    rec["id"],
                    rec["name"],
                    int(rec.get("size", 0)),
                    rec.get("mime", ""),
                    rec.get("sha256", ""),
                    rec.get("path"),
                    time.time(),
                ),
            )
            self._conn.commit()

    def get_file(self, file_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, name, size, mime, sha256, path FROM files WHERE id = ?",
                (file_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "name": row["name"],
            "size": row["size"],
            "mime": row["mime"],
            "sha256": row["sha256"],
            "path": row["path"],
        }

    def set_file_path(self, file_id: str, path: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE files SET path = ? WHERE id = ?", (path, file_id)
            )
            self._conn.commit()

    # --- Pairs de confiance ----------------------------------------------
    def remember_peer(self, peer_id: str, public_key: str, pseudo: str) -> None:
        """Mémorise la clé publique d'un pair. Une clé déjà connue n'est
        **jamais** remplacée : c'est ce qui permet de détecter une usurpation."""
        now = time.time()
        with self._lock:
            row = self._conn.execute(
                "SELECT public_key FROM peers WHERE peer_id = ?", (peer_id,)
            ).fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO peers (peer_id, public_key, pseudo, first_seen, last_seen)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (peer_id, public_key, pseudo, now, now),
                )
            else:
                self._conn.execute(
                    "UPDATE peers SET pseudo = COALESCE(NULLIF(?, ''), pseudo),"
                    " last_seen = ? WHERE peer_id = ?",
                    (pseudo, now, peer_id),
                )
            self._conn.commit()

    def peer(self, peer_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT peer_id, public_key, pseudo, verified, blocked, muted"
                " FROM peers WHERE peer_id = ?",
                (peer_id,),
            ).fetchone()
        return dict(row) if row else None

    def all_peers(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT peer_id, public_key, pseudo, verified, blocked, muted"
                " FROM peers ORDER BY pseudo COLLATE NOCASE"
            ).fetchall()
        return [dict(row) for row in rows]

    def set_peer_flag(self, peer_id: str, field: str, value: bool) -> None:
        if field not in ("verified", "blocked", "muted"):
            raise ValueError(field)
        with self._lock:
            self._conn.execute(
                f"UPDATE peers SET {field} = ? WHERE peer_id = ?",
                (1 if value else 0, peer_id),
            )
            self._conn.commit()

    def forget_peer(self, peer_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM peers WHERE peer_id = ?", (peer_id,))
            self._conn.commit()

    # --- Salons -----------------------------------------------------------
    def remember_room(self, code: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO rooms (code, joined_at) VALUES (?, ?)",
                (code, time.time()),
            )
            self._conn.commit()

    def recent_rooms(self, limit: int = 20) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT code FROM rooms ORDER BY joined_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [row["code"] for row in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _json(value: object) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)


def _json_load(value: str) -> dict:
    import json

    try:
        data = json.loads(value)
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}
