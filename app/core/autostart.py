"""Démarrage automatique de Ruche avec Windows.

On écrit une entrée dans ``HKCU\\...\\Run`` : c'est par utilisateur, donc
aucun droit administrateur n'est requis.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

log = logging.getLogger("ruche.autostart")

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE = "Ruche"


def _launch_command(minimized: bool = True) -> str:
    """Commande à lancer au démarrage (application empaquetée ou sources)."""
    suffix = " --minimized" if minimized else ""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"{suffix}'
    script = Path(__file__).resolve().parents[2] / "run.py"
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    exe = pythonw if pythonw.exists() else Path(sys.executable)
    return f'"{exe}" "{script}"{suffix}'


def is_supported() -> bool:
    return sys.platform == "win32"


def is_enabled() -> bool:
    if not is_supported():
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, _VALUE)
            return bool(value)
    except FileNotFoundError:
        return False
    except OSError:
        return False


def enable(minimized: bool = True) -> bool:
    if not is_supported():
        return False
    try:
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.SetValueEx(key, _VALUE, 0, winreg.REG_SZ, _launch_command(minimized))
        log.info("démarrage automatique activé")
        return True
    except OSError as exc:
        log.warning("démarrage automatique impossible : %s", exc)
        return False


def disable() -> bool:
    if not is_supported():
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, _VALUE)
        log.info("démarrage automatique désactivé")
        return True
    except FileNotFoundError:
        return True
    except OSError as exc:
        log.warning("retrait du démarrage automatique impossible : %s", exc)
        return False


def set_enabled(enabled: bool, minimized: bool = True) -> bool:
    return enable(minimized) if enabled else disable()
