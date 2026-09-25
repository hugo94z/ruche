"""Point d'entrée de l'application Ruche.

    python run.py           (ou)   python -m app.main
"""

from __future__ import annotations

import asyncio
import logging
import sys

from PySide6.QtWidgets import QApplication

try:
    import qasync
except ImportError:  # pragma: no cover
    print("Le paquet 'qasync' est requis. Installez les dépendances : "
          "pip install -r requirements.txt", file=sys.stderr)
    raise

from . import config
from .core.identity import load_or_create
from .core.room import RoomManager
from .core.storage import Storage
from .ui.main_window import MainWindow


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Ces bibliothèques sont très bavardes en DEBUG.
    for noisy in ("aioice", "aiohttp", "aiortc", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main() -> int:
    _configure_logging()

    app = QApplication(sys.argv)
    app.setApplicationName(config.APP_NAME)
    app.setApplicationDisplayName(config.APP_NAME)
    # Sans cela, fermer la fenêtre quitterait l'application au lieu de la
    # réduire en zone de notification.
    app.setQuitOnLastWindowClosed(False)

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    storage = Storage(config.db_path())
    identity = load_or_create()
    manager = RoomManager(storage, identity)

    window = MainWindow(manager)
    # Démarré automatiquement avec Windows : on reste en zone de notification.
    if "--minimized" not in sys.argv:
        window.show()

    with loop:
        loop.run_forever()

    storage.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
