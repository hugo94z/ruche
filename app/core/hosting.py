"""Héberger un serveur de rendez-vous depuis l'application.

Permet de devenir le point de rencontre en un clic : les autres participants
saisissent l'adresse affichée. Le serveur ne stocke rien et disparaît à la
fermeture de l'application — les conversations déjà établies continuent, car
le rendez-vous ne sert qu'à se trouver.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket

import aiohttp
from aiohttp import web

from ..rendezvous.server import start_server
from .network.lan import primary_ip

log = logging.getLogger("ruche.hosting")

DEFAULT_PORT = 8765


def _is_private_ipv4(ip: str) -> bool:
    """Vraie adresse de réseau local (RFC 1918) — on écarte VPN et cartes virtuelles."""
    try:
        parts = [int(p) for p in ip.split(".")]
    except ValueError:
        return False
    if len(parts) != 4:
        return False
    a, b = parts[0], parts[1]
    return a == 10 or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168)


def local_ipv4_addresses() -> list[str]:
    """Adresses IPv4 locales utilisables par d'autres machines du réseau."""
    found: list[str] = []
    primary = primary_ip()
    if primary and _is_private_ipv4(primary):
        found.append(primary)
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in found and _is_private_ipv4(ip):
                found.append(ip)
    except OSError:
        pass
    return found


async def detect_public_ip(timeout: float = 4.0) -> str | None:
    """Détecte l'adresse IP publique (facultatif : échoue hors ligne)."""
    services = (
        ("https://api.ipify.org?format=json", "json"),
        ("https://ifconfig.me/ip", "text"),
    )
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    for url, kind in services:
        try:
            async with aiohttp.ClientSession(timeout=client_timeout) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        continue
                    text = (await response.text()).strip()
                    if kind == "json":
                        text = str(json.loads(text).get("ip", ""))
                    if text and len(text) <= 45:
                        return text
        except Exception:
            continue
    return None


class RendezvousHost:
    """Cycle de vie du rendez-vous hébergé localement."""

    def __init__(self) -> None:
        self._runner: web.AppRunner | None = None
        self.port = 0
        self.public_ip: str | None = None
        self._hosting = False

    @property
    def hosting(self) -> bool:
        return self._hosting

    async def start(self, port: int = DEFAULT_PORT) -> int:
        if self._hosting:
            return self.port
        self._runner, self.port = await start_server("0.0.0.0", port)
        self._hosting = True
        return self.port

    async def stop(self) -> None:
        if self._runner is not None:
            try:
                await self._runner.cleanup()
            except Exception:
                pass
        self._runner = None
        self._hosting = False

    async def refresh_public_ip(self) -> str | None:
        self.public_ip = await detect_public_ip()
        return self.public_ip

    # --- Adresses ---------------------------------------------------------
    def loopback_url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/ws"

    def local_urls(self) -> list[str]:
        return [f"ws://{ip}:{self.port}/ws" for ip in local_ipv4_addresses()]

    def public_url(self) -> str | None:
        if not self.public_ip:
            return None
        return f"ws://{self.public_ip}:{self.port}/ws"

    def share_urls(self) -> list[str]:
        """Adresses à transmettre, de la plus probable à la plus incertaine."""
        urls = self.local_urls()
        public = self.public_url()
        if public and public not in urls:
            urls.append(public)
        return urls
