"""Magasin de fichiers local et réception des transferts pair-à-pair.

Les fichiers sont adressés par leur contenu : l'identifiant d'un fichier est le
SHA-256 de ses octets. N'importe quel pair qui possède le fichier peut donc le
servir, et l'intégrité est vérifiable à l'arrivée.
"""

from __future__ import annotations

import hashlib
import mimetypes
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from .storage import Storage

CHUNK_SIZE = 32 * 1024
THUMB_MAX = 320  # côté maximum d'une vignette, en pixels

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass
class FileRecord:
    id: str
    name: str
    size: int
    mime: str
    sha256: str
    path: str | None

    def as_extra(self) -> dict:
        return {"file_id": self.id, "name": self.name, "size": self.size, "mime": self.mime}


def _safe_name(name: str) -> str:
    base = _SAFE.sub("_", Path(name).name).strip("._") or "fichier"
    return base[:80]


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class FileStore:
    def __init__(self, storage: Storage, directory: Path) -> None:
        self.storage = storage
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._incoming: dict[str, dict] = {}

    # --- Consultation -----------------------------------------------------
    def get(self, file_id: str) -> FileRecord | None:
        row = self.storage.get_file(file_id)
        return FileRecord(**row) if row else None

    def is_local(self, file_id: str) -> bool:
        rec = self.get(file_id)
        return bool(rec and rec.path and Path(rec.path).exists())

    def path(self, file_id: str) -> Path | None:
        rec = self.get(file_id)
        if rec and rec.path and Path(rec.path).exists():
            return Path(rec.path)
        return None

    def thumbnail(self, file_id: str, max_size: int = THUMB_MAX) -> Path | None:
        """Vignette JPEG d'une image, générée à la demande et mise en cache.

        Renvoie ``None`` si ce n'est pas une image, si le fichier n'est pas
        encore là, ou si la génération est impossible (Pillow absent, format
        exotique) — l'interface retombe alors sur l'image d'origine.
        """
        rec = self.get(file_id)
        if rec is None or not rec.path or not rec.mime.startswith("image/"):
            return None
        source = Path(rec.path)
        if not source.exists():
            return None
        thumb = self.dir / f".thumb-{file_id[:32]}-{max_size}.jpg"
        try:
            if thumb.exists() and thumb.stat().st_mtime >= source.stat().st_mtime:
                return thumb
        except OSError:
            pass
        try:
            from PIL import Image

            with Image.open(source) as image:
                image = image.convert("RGB")
                image.thumbnail((max_size, max_size))
                image.save(thumb, "JPEG", quality=82)
        except Exception:
            return None
        return thumb

    # --- Préparation d'un envoi ------------------------------------------
    def prepare(self, source: Path) -> FileRecord:
        source = Path(source)
        digest = hash_file(source)
        name = source.name
        size = source.stat().st_size
        mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
        target = self.dir / f"{digest[:16]}_{_safe_name(name)}"
        if not target.exists():
            shutil.copyfile(source, target)
        record = FileRecord(digest, name, size, mime, digest, str(target))
        self.storage.upsert_file(
            {
                "id": record.id,
                "name": record.name,
                "size": record.size,
                "mime": record.mime,
                "sha256": record.sha256,
                "path": record.path,
            }
        )
        return record

    # --- Réception --------------------------------------------------------
    def part_path(self, file_id: str) -> Path:
        return self.dir / f".part-{file_id}"

    def received_bytes(self, file_id: str) -> int:
        """Octets déjà reçus pour un transfert, en cours ou interrompu."""
        state = self._incoming.get(file_id)
        if state is not None:
            return int(state["received"])
        partial = self.part_path(file_id)
        try:
            return partial.stat().st_size
        except OSError:
            return 0

    def begin_receive(
        self, file_id: str, name: str, size: int, mime: str, offset: int = 0
    ) -> bool:
        if file_id in self._incoming:
            return False  # transfert déjà en cours
        if self.is_local(file_id):
            return False  # déjà en notre possession
        tmp = self.part_path(file_id)
        existing = 0
        try:
            existing = tmp.stat().st_size
        except OSError:
            existing = 0
        if existing and size and existing > size:
            existing = 0  # reliquat incohérent : on repart de zéro
        start = max(int(offset), existing)
        if start:
            handle = open(tmp, "r+b")
            handle.seek(start)
        else:
            handle = open(tmp, "wb")
        self._incoming[file_id] = {
            "tmp": tmp,
            "handle": handle,
            "received": start,
            "size": size,
            "name": name,
            "mime": mime,
        }
        return True

    def write_chunk(self, file_id: str, data: bytes) -> int:
        state = self._incoming.get(file_id)
        if state is None:
            return -1
        state["handle"].write(data)
        state["received"] += len(data)
        return state["received"]

    def finish_receive(self, file_id: str) -> FileRecord | None:
        state = self._incoming.pop(file_id, None)
        if state is None:
            return None
        state["handle"].close()
        tmp: Path = state["tmp"]
        digest = hash_file(tmp)
        if digest != file_id:
            tmp.unlink(missing_ok=True)
            return None  # intégrité compromise
        target = self.dir / f"{digest[:16]}_{_safe_name(state['name'])}"
        tmp.replace(target)
        record = FileRecord(
            digest, state["name"], state["received"], state["mime"], digest, str(target)
        )
        self.storage.upsert_file(
            {
                "id": record.id,
                "name": record.name,
                "size": record.size,
                "mime": record.mime,
                "sha256": record.sha256,
                "path": record.path,
            }
        )
        return record

    def abort(self, file_id: str) -> None:
        """Abandonne un transfert et **efface** le fichier partiel."""
        state = self._incoming.pop(file_id, None)
        if state is not None:
            try:
                state["handle"].close()
            finally:
                state["tmp"].unlink(missing_ok=True)

    def pause(self, file_id: str) -> None:
        """Interrompt un transfert en **conservant** les octets reçus.

        Le fichier ``.part-…`` reste sur le disque : une reprise ultérieure
        repartira de ``received_bytes`` sans retélécharger le début.
        """
        state = self._incoming.pop(file_id, None)
        if state is not None:
            try:
                state["handle"].close()
            except OSError:
                pass

    def incoming_ids(self) -> list[str]:
        """Transferts en cours ou interrompus (partiels conservés)."""
        ids = set(self._incoming)
        for partial in self.dir.glob(".part-*"):
            ids.add(partial.name[len(".part-"):])
        return sorted(ids)

    def progress(self, file_id: str) -> tuple[int, int] | None:
        state = self._incoming.get(file_id)
        if state is None:
            return None
        return state["received"], state["size"]
