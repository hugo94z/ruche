"""Test du chantier « Distribution » : purge du cache et mises à jour.

    python tools/distribution_test.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
except Exception:
    pass

from app import config  # noqa: E402

_TMP = Path(tempfile.mkdtemp(prefix="ruche-dist-"))
config.data_dir = lambda: _TMP  # type: ignore[assignment]

from app.core import update  # noqa: E402
from app.core.files import FileStore  # noqa: E402
from app.core.storage import Storage  # noqa: E402


class _FakeResponse:
    def __init__(self, status: int, data: dict) -> None:
        self.status = status
        self._data = data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self) -> dict:
        return self._data


class _FakeSession:
    """Session aiohttp factice : renvoie une release GitHub simulée."""

    def __init__(self, status: int = 200, data: dict | None = None, **kwargs) -> None:
        self._status = status
        self._data = data or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, url, headers=None):
        return _FakeResponse(self._status, self._data)


class _OfflineSession:
    def __init__(self, **kwargs) -> None:
        raise OSError("réseau indisponible")


class _FakeAiohttp:
    def __init__(self, session_cls) -> None:
        self.ClientSession = session_cls

    @staticmethod
    def ClientTimeout(**kwargs):
        return None


def main() -> int:
    results: list[tuple[str, bool]] = []
    store = Storage(_TMP / "dist.sqlite3")
    files = FileStore(store, _TMP / "cache")

    # --- Purge du cache ---------------------------------------------------
    print("1. Cache : inventaire et purge sélective…")
    small = _TMP / "petit.txt"
    small.write_bytes(b"a" * 100)
    big = _TMP / "gros.bin"
    big.write_bytes(b"b" * 5000)
    rec_small = files.prepare(small)
    rec_big = files.prepare(big)

    try:
        from PIL import Image

        photo = _TMP / "photo.png"
        Image.new("RGB", (600, 400), (0, 100, 200)).save(photo)
        rec_photo = files.prepare(photo)
        thumb = files.thumbnail(rec_photo.id)
    except ImportError:
        rec_photo = None
        thumb = None

    cached = files.cached_files()
    results.append(("Les fichiers apparaissent dans le cache", len(cached) >= 2))
    results.append(("Leur taille est connue", files.cache_size() >= 5100))
    results.append(("Tous sont présents sur le disque", all(r["present"] for r in cached)))

    count, freed = files.purge([rec_small.id, rec_big.id])
    results.append(("Deux fichiers purgés", count == 2 and freed >= 5100))
    results.append(("Le fichier est effacé du disque", not Path(rec_small.path).exists()))
    results.append(("L'entrée est effacée de la base", store.get_file(rec_small.id) is None))
    results.append(("Les autres fichiers restent", files.is_local(rec_photo.id) if rec_photo else True))

    if rec_photo and thumb:
        files.purge([rec_photo.id])
        results.append(("La vignette est purgée avec l'image", not thumb.exists()))

    print("2. Nettoyage des transferts inachevés…")
    partial = files.dir / ".part-deadbeef"
    partial.write_bytes(b"morceau")
    removed = files.purge_partials()
    results.append(("Transfert inachevé supprimé", removed == 1 and not partial.exists()))

    # --- Mises à jour -----------------------------------------------------
    print("3. Comparaison de versions…")
    results.append(("Version simple lue", update.parse_version("v1.2.3") == (1, 2, 3)))
    results.append(("Version partielle lue", update.parse_version("0.2") == (0, 2, 0)))
    results.append(("Version illisible tolérée", update.parse_version("bêta") == (0, 0, 0)))
    results.append(("1.0.0 plus récente que 0.9.9", update.is_newer("1.0.0", "0.9.9")))
    results.append(("Même version non plus récente", not update.is_newer("1.0.0", "1.0.0")))
    results.append(("Version antérieure ignorée", not update.is_newer("0.2.0", "1.0.0")))
    results.append(("Lien de releases cohérent", config.GITHUB_REPO in update.releases_url()))

    print("4. Interrogation de l'API (réseau simulé)…")
    real_aiohttp = update.aiohttp
    try:
        update.aiohttp = _FakeAiohttp(
            lambda **kw: _FakeSession(
                data={"tag_name": "v9.9.9", "name": "Ruche 9.9.9",
                      "html_url": "https://exemple/9.9.9", "body": "notes"}
            )
        )
        info = asyncio.run(update.latest_release())
        results.append(("Release analysée", bool(info) and info["version"] == "9.9.9"))
        results.append(("Mise à jour détectée", bool(info) and update.is_newer(info["version"], config.APP_VERSION)))

        update.aiohttp = _FakeAiohttp(_OfflineSession)
        results.append(("Réseau coupé : pas d'erreur", asyncio.run(update.latest_release()) is None))
    finally:
        update.aiohttp = real_aiohttp

    store.close()

    print("\nRésultats :")
    for label, passed in results:
        print(f"  {'✓' if passed else '✗'} {label}")
    return 0 if all(passed for _, passed in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
