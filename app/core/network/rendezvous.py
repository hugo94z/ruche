"""Client du serveur de rendez-vous.

Le rendez-vous ne stocke rien et ne fait pas tourner le chat : il sert
uniquement à (1) lister les pairs présents dans un salon et (2) relayer les
messages de signalisation WebRTC. Si le rendez-vous disparaît, les pairs déjà
connectés continuent de discuter.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Awaitable, Callable
from urllib.parse import urlencode, urlsplit, urlunsplit

import aiohttp

log = logging.getLogger("ruche.rendezvous")


class RendezvousClient:
    def __init__(
        self,
        url: str,
        room: str,
        peer_id: str,
        pseudo: str,
        *,
        on_peers: Callable[[list[dict]], None],
        on_peer_joined: Callable[[str, str], None],
        on_peer_left: Callable[[str], None],
        on_signal: Callable[[str, dict], Awaitable[None]],
        on_status: Callable[[str], None],
    ) -> None:
        self.base_url = url
        self.room = room
        self.peer_id = peer_id
        self.pseudo = pseudo
        self.on_peers = on_peers
        self.on_peer_joined = on_peer_joined
        self.on_peer_left = on_peer_left
        self.on_signal = on_signal
        self.on_status = on_status

        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._task: asyncio.Task | None = None
        self._stopping = False
        self.connected = False

    # --- Cycle de vie -----------------------------------------------------
    async def start(self) -> None:
        self._session = aiohttp.ClientSession()
        self._task = asyncio.create_task(self._run(), name="rendezvous")

    async def stop(self) -> None:
        self._stopping = True
        if self._ws is not None:
            await self._ws.close()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        if self._session is not None:
            await self._session.close()
        self.connected = False

    # --- Boucle de connexion ---------------------------------------------
    def _url(self) -> str:
        parts = urlsplit(self.base_url)
        query = urlencode(
            {"room": self.room, "peer": self.peer_id, "pseudo": self.pseudo}
        )
        return urlunsplit((parts.scheme, parts.netloc, parts.path or "/ws", query, ""))

    async def _run(self) -> None:
        assert self._session is not None
        while not self._stopping:
            try:
                async with self._session.ws_connect(self._url(), heartbeat=25) as ws:
                    self._ws = ws
                    self.connected = True
                    self.on_status("connecté au rendez-vous")
                    async for message in ws:
                        if message.type == aiohttp.WSMsgType.TEXT:
                            self._handle(json.loads(message.data))
                        elif message.type in (
                            aiohttp.WSMsgType.CLOSE,
                            aiohttp.WSMsgType.CLOSED,
                            aiohttp.WSMsgType.ERROR,
                        ):
                            break
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # réseau coupé, serveur éteint…
                if self.connected:
                    self.on_status(f"rendez-vous interrompu ({exc})")
            finally:
                self.connected = False
                self._ws = None
            if not self._stopping:
                self.on_status("reconnexion au rendez-vous…")
                await asyncio.sleep(3)

    def _handle(self, data: dict) -> None:
        kind = data.get("t")
        if kind == "welcome":
            self.on_peers(list(data.get("peers", [])))
        elif kind == "peer-joined":
            self.on_peer_joined(data["id"], data.get("pseudo", ""))
        elif kind == "peer-left":
            self.on_peer_left(data["id"])
        elif kind == "signal":
            asyncio.create_task(self.on_signal(data["from"], data.get("payload", {})))

    # --- Envoi ------------------------------------------------------------
    async def send_signal(self, to: str, payload: dict) -> bool:
        ws = self._ws
        if ws is None or ws.closed:
            return False
        try:
            await ws.send_json({"t": "signal", "to": to, "payload": payload})
            return True
        except Exception:
            return False
