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
from app.core.room import RoomManager  # noqa: E402
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

        window.message_input.setText("Bonjour le salon")
        window._send_message()  # type: ignore[attr-defined]
        await asyncio.sleep(0.3)
        checks.append(("Message affiché", "Bonjour le salon" in window.transcript.toPlainText()))

        source = config.data_dir() / "note.txt"
        source.write_text("contenu de test", encoding="utf-8")
        await manager.send_file(source)
        await asyncio.sleep(0.3)
        checks.append(
            ("Fichier affiché dans le fil", "note.txt" in window.transcript.toPlainText())
        )

        await manager.leave()
        await asyncio.sleep(0.2)
        checks.append(("Retour à l'état hors ligne", not window.message_input.isEnabled()))

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
