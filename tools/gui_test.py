"""Vérification de l'interface graphique en mode hors écran (sans affichage).

    python tools/gui_test.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
except Exception:
    pass

from app import config  # noqa: E402

config.data_dir = lambda: Path(tempfile.mkdtemp(prefix="ruche-gui-"))  # type: ignore[assignment]

import qasync  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core.identity import load_or_create  # noqa: E402
from app.core.room import RoomManager, dm_key  # noqa: E402
from app.core.storage import Storage  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402


def main() -> int:
    app = QApplication([])
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    storage = Storage(config.db_path())
    identity = load_or_create("Testeur")
    manager = RoomManager(storage, identity)
    window = MainWindow(manager)
    window.show()

    checks: list[tuple[str, bool]] = []

    async def scenario() -> None:
        # Rejoindre un salon local, sans rendez-vous (mode hors ligne).
        window.pseudo_input.setText("Testeur")
        window.room_input.setText("SALON1")
        window.rendezvous_input.setText("")
        window._join()  # type: ignore[attr-defined]
        await asyncio.sleep(0.3)

        checks.append(("Salon affiché dans le titre", "SALON1" in window.windowTitle()))
        checks.append(("Champ de saisie activé", window.message_input.isEnabled()))
        checks.append(("Membres listés (1)", window.members_list.count() == 1))
        checks.append(("Hôte indiqué dans la liste", "hôte" in window.members_list.item(0).text()))
        checks.append(("Barre latérale : un salon", window.rooms_list.count() == 1))

        window.message_input.setText("Bonjour le salon")
        window._send_message()  # type: ignore[attr-defined]
        await asyncio.sleep(0.3)
        checks.append(("Message affiché", "Bonjour le salon" in window.transcript.toPlainText()))

        # Édition et réactions (opérations append-only).
        entry = manager.history.all_views()[0]
        await manager.edit_message(entry["id"], "Bonjour corrigé")
        await asyncio.sleep(0.2)
        text = window.transcript.toPlainText()
        checks.append(("Message édité affiché", "Bonjour corrigé" in text))
        checks.append(("Mention « modifié »", "modifié" in text))
        await manager.toggle_reaction(entry["id"], "👍")
        await asyncio.sleep(0.2)
        checks.append(("Réaction affichée", "👍" in window.transcript.toPlainText()))

        source = config.data_dir() / "note.txt"
        source.write_text("contenu de test", encoding="utf-8")
        await manager.send_file(source)
        await asyncio.sleep(0.3)
        checks.append(
            ("Fichier affiché dans le fil", "note.txt" in window.transcript.toPlainText())
        )

        # Vignette d'une image.
        try:
            from PIL import Image

            photo = config.data_dir() / "photo.png"
            Image.new("RGB", (800, 600), (10, 120, 200)).save(photo)
            await manager.send_file(photo)
            await asyncio.sleep(0.3)
            file_id = manager.files.prepare(photo).id
            thumb = manager.files.thumbnail(file_id)
            checks.append(("Vignette générée par l'interface", thumb is not None))
        except ImportError:
            checks.append(("Pillow disponible", False))

        checks.append(("Bouton cache présent", "Cache" in window.cache_button.text()))
        checks.append(
            ("Fichiers listés dans le cache", len(manager.files.cached_files()) >= 1)
        )

        # Message privé : une session distincte apparaît dans la barre latérale.
        peer = "0123456789abcdef"
        await manager.start_dm(peer, "Bob")
        await asyncio.sleep(0.3)
        checks.append(("Message privé ouvert", window.rooms_list.count() == 2))
        checks.append(("Titre du message privé", "Bob" in window.windowTitle()))
        await manager.leave_room(dm_key(manager.identity.peer_id, peer))
        await asyncio.sleep(0.2)
        checks.append(("Retour au salon après fermeture du MP", window.rooms_list.count() == 1))

        await manager.leave()
        await asyncio.sleep(0.2)
        checks.append(("Retour à l'état hors ligne", not window.message_input.isEnabled()))

        # Hébergement d'un rendez-vous depuis l'application
        await window._start_host_and_show()  # type: ignore[attr-defined]
        checks.append(
            ("Hébergement actif", window.host.hosting and window.host.port > 0)  # type: ignore[attr-defined]
        )
        checks.append(
            (
                "Adresse locale pré-remplie",
                window.rendezvous_input.text().startswith("ws://127.0.0.1:"),  # type: ignore[attr-defined]
            )
        )
        share = window.host.share_urls()  # type: ignore[attr-defined]
        checks.append(
            ("Adresses de partage cohérentes", bool(share) and all(u.startswith("ws://") for u in share))
        )
        await window.host.stop()  # type: ignore[attr-defined]
        checks.append(("Hébergement arrêté", not window.host.hosting))  # type: ignore[attr-defined]

        loop.stop()

    loop.call_soon(lambda: asyncio.ensure_future(scenario()))
    with loop:
        loop.run_forever()

    storage.close()

    print("Vérifications de l'interface :")
    for label, ok in checks:
        print(f"  {'✓' if ok else '✗'} {label}")
    return 0 if all(ok for _, ok in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
