"""Configuration globale et chemins de stockage de Ruche."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "Ruche"

# Serveur de rendez-vous par défaut (à changer dans l'interface).
DEFAULT_RENDEZVOUS_URL = "ws://127.0.0.1:8765/ws"

# --- Réseau temps réel / WebRTC ---
import json

# Serveurs STUN publics : aident à traverser le NAT (pas de relais TURN pour l'instant).
STUN_SERVERS = [
    "stun:stun.l.google.com:19302",
    "stun:stun1.l.google.com:19302",
]

# Taille maximale d'un message de chat (caractères).
MAX_TEXT_LENGTH = 8000


def data_dir() -> Path:
    """Dossier de données de l'utilisateur, créé au besoin.

    `RUCHE_DATA_DIR` permet de forcer un autre dossier, pratique pour lancer
    plusieurs instances sur une même machine.
    """
    override = os.environ.get("RUCHE_DATA_DIR")
    if override:
        path = Path(override)
        path.mkdir(parents=True, exist_ok=True)
        return path
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home())
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    path = base / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def files_dir() -> Path:
    path = data_dir() / "files"
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return data_dir() / "ruche.sqlite3"


def identity_path() -> Path:
    return data_dir() / "identity.json"


def ice_servers() -> list[dict]:
    """Serveurs ICE pour WebRTC : STUN par défaut, TURN si configuré.

    Ordre de priorité :
      1. fichier ``ice_servers.json`` dans le dossier de données ;
      2. variables d'environnement ``RUCHE_TURN_URL`` / ``RUCHE_TURN_USER`` /
         ``RUCHE_TURN_PASS`` ;
      3. STUN publics seuls.
    """
    path = data_dir() / "ice_servers.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list) and data:
                return data
        except (ValueError, OSError):
            pass

    servers: list[dict] = [{"urls": url} for url in STUN_SERVERS]
    turn_url = os.environ.get("RUCHE_TURN_URL")
    if turn_url:
        servers.append(
            {
                "urls": turn_url,
                "username": os.environ.get("RUCHE_TURN_USER", ""),
                "credential": os.environ.get("RUCHE_TURN_PASS", ""),
            }
        )
    return servers
