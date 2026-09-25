"""Serveur de rendez-vous Ruche (sans état, sans stockage).

Rôle unique :
  1. tenir la liste des pairs présents dans chaque salon ;
  2. relayer les messages de signalisation WebRTC (offre/réponse/fin de
     négociation) entre ces pairs.

Il ne stocke AUCUN message, ne diffuse aucun chat et ne joue aucun rôle
d'hôte. S'il s'arrête, les pairs déjà connectés continuent de discuter.

Lancement :
    python -m app.rendezvous.server --host 0.0.0.0 --port 8765
"""

from __future__ import annotations

import argparse
import json
import logging

from aiohttp import WSMsgType, web

log = logging.getLogger("ruche.rendezvous.server")

# salon -> { peer_id -> {"ws": WebSocketResponse, "pseudo": str} }
ROOMS: dict[str, dict[str, dict]] = {}


async def health(_request: web.Request) -> web.Response:
    total = sum(len(members) for members in ROOMS.values())
    return web.json_response(
        {"service": "ruche-rendezvous", "rooms": len(ROOMS), "peers": total}
    )


async def ws_handler(request: web.Request) -> web.WebSocketResponse:
    room = (request.query.get("room") or "").strip()
    peer = (request.query.get("peer") or "").strip()
    pseudo = (request.query.get("pseudo") or "").strip() or peer[:8]

    if not room or not peer:
        return web.json_response({"error": "room et peer sont requis"}, status=400)

    ws = web.WebSocketResponse(heartbeat=25)
    await ws.prepare(request)

    members = ROOMS.setdefault(room, {})

    # Si le même identifiant revient (reconnexion), on remplace l'ancienne.
    previous = members.get(peer)
    if previous is not None and previous["ws"] is not ws:
        try:
            await previous["ws"].close(code=4000, message=b"remplace")
        except Exception:
            pass

    members[peer] = {"ws": ws, "pseudo": pseudo}
    log.info("salon %s : + %s (%s) — %d en ligne", room, pseudo, peer[:8], len(members))

    # Liste des pairs déjà présents → au nouvel arrivant.
    others = [
        {"id": pid, "pseudo": info["pseudo"]}
        for pid, info in members.items()
        if pid != peer
    ]
    await ws.send_json({"t": "welcome", "peers": others})

    # Annonce du nouvel arrivant → aux autres.
    await _broadcast(members, {"t": "peer-joined", "id": peer, "pseudo": pseudo}, exclude=peer)

    try:
        async for message in ws:
            if message.type == WSMsgType.TEXT:
                _relay(members, peer, message.data)
            elif message.type == WSMsgType.ERROR:
                break
    finally:
        if members.get(peer, {}).get("ws") is ws:
            members.pop(peer, None)
        await _broadcast(members, {"t": "peer-left", "id": peer})
        log.info("salon %s : - %s (%s) — %d en ligne", room, pseudo, peer[:8], len(members))
        if not members:
            ROOMS.pop(room, None)

    return ws


def _relay(members: dict[str, dict], sender: str, raw: str) -> None:
    try:
        data = json.loads(raw)
    except ValueError:
        return
    if data.get("t") != "signal":
        return
    target = members.get(data.get("to", ""))
    if target is None:
        return
    import asyncio

    asyncio.ensure_future(
        _safe_send(
            target["ws"],
            {"t": "signal", "from": sender, "payload": data.get("payload", {})},
        )
    )


async def _broadcast(members: dict[str, dict], payload: dict, exclude: str | None = None) -> None:
    import asyncio

    tasks = [
        _safe_send(info["ws"], payload)
        for pid, info in list(members.items())
        if pid != exclude
    ]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def _safe_send(ws: web.WebSocketResponse, payload: dict) -> None:
    try:
        if not ws.closed:
            await ws.send_json(payload)
    except Exception:
        pass


def build_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/ws", ws_handler)
    return app


async def start_server(host: str = "0.0.0.0", port: int = 8765):
    """Démarre le rendez-vous dans la boucle d'événements courante.

    Renvoie (runner, port_reel). Le port réel peut différer si celui demandé
    est déjà occupé (on en prend alors un libre). Utilisé par l'application
    pour devenir elle-même le point de rencontre, sans terminal.
    """
    runner = web.AppRunner(build_app())
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    try:
        await site.start()
    except OSError:
        site = web.TCPSite(runner, host, 0)
        await site.start()
    actual = site._server.sockets[0].getsockname()[1]
    log.info("Rendez-vous en cours sur le port %d", actual)
    return runner, actual


def main() -> None:
    parser = argparse.ArgumentParser(description="Serveur de rendez-vous Ruche")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    log.info("Rendez-vous Ruche sur http://%s:%d  (ws → /ws)", args.host, args.port)
    web.run_app(build_app(), host=args.host, port=args.port, print=None)


if __name__ == "__main__":
    main()
