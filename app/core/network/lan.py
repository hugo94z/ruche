"""Découverte locale (mDNS) et signalisation directe sur le réseau local.

Sur un même réseau, les pairs se trouvent via mDNS et échangent leur
signalisation WebRTC par HTTP, **sans aucun serveur de rendez-vous**. Chaque
pair démarre un petit serveur HTTP local (port éphémère) et publie son adresse
sous le service ``_ruche._tcp.local.``.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from typing import Awaitable, Callable

import aiohttp
from aiohttp import web
from zeroconf import ServiceInfo
from zeroconf.asyncio import AsyncServiceBrowser, AsyncZeroconf

log = logging.getLogger("ruche.lan")

SERVICE_TYPE = "_ruche._tcp.local."


def primary_ip() -> str:
    """Adresse IPv4 locale utilisée pour joindre le réseau."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except OSError:
        return "127.0.0.1"


class LanNetwork:
    def __init__(
        self,
        peer_id: str,
        pseudo: str,
        *,
        on_peer_found: Callable[[str, str], None],
        on_peer_lost: Callable[[str], None],
        on_signal: Callable[[str, dict], Awaitable[None]],
        on_status: Callable[[str], None],
    ) -> None:
        self.peer_id = peer_id
        self.pseudo = pseudo
        self.on_peer_found = on_peer_found
        self.on_peer_lost = on_peer_lost
        self.on_signal = on_signal
        self.on_status = on_status

        self.room: str | None = None
        self.port = 0
        self._runner: web.AppRunner | None = None
        self._session: aiohttp.ClientSession | None = None
        self._zc: AsyncZeroconf | None = None
        self._browser: AsyncServiceBrowser | None = None
        self._info: ServiceInfo | None = None
        self._others: dict[str, tuple[str, int]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    # --- Cycle de vie -----------------------------------------------------
    async def start(self, room: str) -> None:
        self._loop = asyncio.get_running_loop()
        self.room = room

        # Serveur de signalisation local
        app = web.Application()
        app.router.add_post("/signal", self._http_signal)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]
        self._session = aiohttp.ClientSession()

        # Annonce mDNS
        ip = primary_ip()
        try:
            self._info = ServiceInfo(
                SERVICE_TYPE,
                f"{self.peer_id}.{SERVICE_TYPE}",
                addresses=[socket.inet_aton(ip)],
                port=self.port,
                properties={
                    "room": room,
                    "peer": self.peer_id,
                    "pseudo": self.pseudo,
                },
                server=f"{socket.gethostname()}.local.",
            )
            self._zc = AsyncZeroconf()
            await self._zc.async_register_service(self._info)
            self._browser = AsyncServiceBrowser(
                self._zc.zeroconf, SERVICE_TYPE, handlers=[self._on_service_event]
            )
            self.on_status("découverte locale active (mDNS)")
        except Exception as exc:  # mDNS indisponible : on continue sans
            log.warning("mDNS indisponible : %s", exc)
            self.on_status("découverte locale indisponible")

    async def stop(self) -> None:
        try:
            if self._zc is not None and self._info is not None:
                await self._zc.async_unregister_service(self._info)
            if self._zc is not None:
                await self._zc.async_close()
        except Exception:
            pass
        try:
            if self._runner is not None:
                await self._runner.cleanup()
        except Exception:
            pass
        if self._session is not None:
            await self._session.close()
        self._zc = None
        self._browser = None
        self._others.clear()

    # --- mDNS -------------------------------------------------------------
    def _on_service_event(self, zeroconf, service_type, name, state_change=None) -> None:
        state = getattr(state_change, "name", str(state_change))
        if state == "Removed":
            peer_id = name.split(".", 1)[0]
            if self._others.pop(peer_id, None) is not None:
                self.on_peer_lost(peer_id)
            return
        if self._loop is not None:
            self._loop.create_task(self._inspect(name))

    async def _inspect(self, name: str) -> None:
        if self._zc is None:
            return
        try:
            info = await self._zc.async_get_service_info(SERVICE_TYPE, name)
        except Exception:
            return
        if info is None:
            return
        props: dict[str, str] = {}
        for key, value in (info.properties or {}).items():
            key_s = key.decode() if isinstance(key, bytes) else str(key)
            value_s = value.decode() if isinstance(value, bytes) else str(value)
            props[key_s] = value_s

        peer = props.get("peer", "")
        if not peer or peer == self.peer_id or props.get("room") != self.room:
            return
        addresses = info.parsed_addresses()
        if not addresses:
            return

        self._others[peer] = (addresses[0], info.port)
        self.on_peer_found(peer, props.get("pseudo", peer[:8]))

    # --- Signalisation ----------------------------------------------------
    async def _http_signal(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "json invalide"}, status=400)
        sender = data.get("from", "")
        payload = data.get("payload", {})
        if sender and self._loop is not None:
            self._loop.create_task(self.on_signal(sender, payload))
        return web.json_response({"ok": True})

    async def send_signal(self, peer_id: str, payload: dict) -> bool:
        target = self._others.get(peer_id)
        if target is None or self._session is None:
            return False
        host, port = target
        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with self._session.post(
                f"http://{host}:{port}/signal",
                json={"from": self.peer_id, "payload": payload},
                timeout=timeout,
            ) as response:
                return response.status == 200
        except Exception:
            return False

    def has_peer(self, peer_id: str) -> bool:
        return peer_id in self._others

    def peers(self) -> list[str]:
        return list(self._others)
