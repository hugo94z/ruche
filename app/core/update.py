"""Vérification des mises à jour depuis les versions GitHub.

Interroge l'API publique de GitHub pour connaître la dernière version publiée
et la compare à celle de l'application. Aucune donnée n'est envoyée : c'est une
simple lecture, et l'échec réseau est silencieux (on ne bloque jamais
l'utilisateur pour une mise à jour).
"""

from __future__ import annotations

import logging
import re

import aiohttp

from .. import config

log = logging.getLogger("ruche.update")

_VERSION_RE = re.compile(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?")


def parse_version(text: str) -> tuple[int, int, int]:
    """Convertit ``v1.2.3`` en ``(1, 2, 3)`` ; ``(0, 0, 0)`` si illisible."""
    match = _VERSION_RE.search(text or "")
    if not match:
        return (0, 0, 0)
    parts = [int(group) if group else 0 for group in match.groups()]
    return (parts[0], parts[1], parts[2])


def is_newer(candidate: str, current: str) -> bool:
    return parse_version(candidate) > parse_version(current)


def releases_url() -> str:
    return f"https://github.com/{config.GITHUB_REPO}/releases/latest"


async def latest_release(timeout: float = 6.0) -> dict | None:
    """Dernière version publiée, ou ``None`` si indisponible.

    Renvoie ``{"version", "tag", "name", "url", "notes"}``.
    """
    api = f"https://api.github.com/repos/{config.GITHUB_REPO}/releases/latest"
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    try:
        async with aiohttp.ClientSession(timeout=client_timeout) as session:
            headers = {"Accept": "application/vnd.github+json"}
            async with session.get(api, headers=headers) as response:
                if response.status != 200:
                    return None
                data = await response.json()
    except Exception as exc:  # réseau coupé, hors ligne…
        log.info("vérification de mise à jour impossible : %s", exc)
        return None
    tag = str(data.get("tag_name") or "")
    if not tag:
        return None
    return {
        "version": tag.lstrip("v"),
        "tag": tag,
        "name": data.get("name") or tag,
        "url": data.get("html_url") or releases_url(),
        "notes": data.get("body") or "",
    }
