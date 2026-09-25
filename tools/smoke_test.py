"""Test de bout en bout du socle Ruche (sans interface graphique).

Lance un vrai serveur de rendez-vous en mémoire, connecte deux pairs dans un
salon, vérifie que le chat circule en pair-à-pair, que l'historique se réplique
et que l'hôte bascule quand le pair qui le tenait se déconnecte.

    python tools/smoke_test.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
except Exception:
    pass

from aiohttp import web  # noqa: E402

from app import config  # noqa: E402

# Isole les données de test : n'écrit rien dans le profil réel de l'utilisateur.
_TMP = Path(tempfile.mkdtemp(prefix="ruche-test-"))
config.data_dir = lambda: _TMP  # type: ignore[assignment]

from app.core.identity import Identity, create as create_identity  # noqa: E402
from app.core.room import RoomManager  # noqa: E402
from app.core.storage import Storage  # noqa: E402
from app.rendezvous.server import build_app  # noqa: E402


def make_identity(pseudo: str) -> Identity:
    return create_identity(pseudo)


async def wait_for(predicate, timeout: float = 25.0, label: str = "") -> bool:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if predicate():
            return True
        await asyncio.sleep(0.1)
    print(f"  ✗ délai dépassé : {label}")
    return False


async def start_rendezvous() -> tuple[web.AppRunner, str]:
    runner = web.AppRunner(build_app())
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, f"ws://127.0.0.1:{port}/ws"


async def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("ruche.transport").setLevel(logging.DEBUG)
    runner, url = await start_rendezvous()
    print(f"Rendez-vous de test sur {url}\n")

    store_a = Storage(_TMP / "a.sqlite3")
    store_b = Storage(_TMP / "b.sqlite3")
    alice = make_identity("Alice")
    bob = make_identity("Bob")
    a = RoomManager(store_a, alice)
    b = RoomManager(store_b, bob)

    a.add_listener(lambda e, p: print(f"    [A/{e}] {p if e != 'history' else '<historique>'}"))
    b.add_listener(lambda e, p: print(f"    [B/{e}] {p if e != 'history' else '<historique>'}"))

    room = "TEST01"
    results: list[tuple[str, bool]] = []

    print("1. Connexion des deux pairs au salon…")
    await a.join(room, "Alice", url, use_lan=False)
    await b.join(room, "Bob", url, use_lan=False)

    ok = await wait_for(
        lambda: len(a.members) == 2 and len(b.members) == 2, label="adhésion des membres"
    )
    results.append(("Adhésion des deux pairs", ok))

    ok = await wait_for(
        lambda: bool(a.transport.connected_peers()) and bool(b.transport.connected_peers()),
        label="ouverture du canal de données WebRTC",
    )
    results.append(("Maillage WebRTC établi", ok))
    if not ok:
        print("  (le maillage n'a pas pu s'établir en local)")

    print("2. Élection de l'hôte…")
    expected_host = min(alice.peer_id, bob.peer_id)
    ok = await wait_for(
        lambda: a.host_id == expected_host and b.host_id == expected_host,
        label="hôte commun",
    )
    results.append(("Hôte élu de façon identique des deux côtés", ok))

    print("3. Envoi d'un message d'Alice…")
    await a.send_text("Bonjour depuis Alice !")
    ok = await wait_for(
        lambda: any(
            e["body"] == "Bonjour depuis Alice !" for e in b.history.all_sorted()
        ),
        label="réception du message chez Bob",
    )
    results.append(("Message reçu par Bob", ok))

    print("4. Transfert d'un fichier (200 Ko) d'Alice vers Bob…")
    payload = os.urandom(200 * 1024)
    source = _TMP / "fichier_test.bin"
    source.write_bytes(payload)
    await a.send_file(source)

    ok = await wait_for(
        lambda: any(e["kind"] == "file" for e in b.history.all_sorted()),
        label="annonce du fichier",
    )
    results.append(("Annonce du fichier reçue", ok))

    file_entries = [e for e in b.history.all_sorted() if e["kind"] == "file"]
    file_id = file_entries[-1]["extra"]["file_id"] if file_entries else ""
    await b.request_file(file_id)

    ok = await wait_for(lambda: b.files.is_local(file_id), label="réception du fichier")
    results.append(("Fichier reçu et vérifié (SHA-256)", ok))
    if ok:
        received = b.files.path(file_id)
        results.append(("Contenu du fichier identique", received.read_bytes() == payload))

    print("5. Réplication de l'historique à la (re)connexion…")
    ok = await wait_for(
        lambda: len(b.history.all_sorted()) >= 2, label="historique répliqué"
    )
    results.append(("Historique répliqué chez Bob", ok))

    print("6. Bascule d'hôte : Alice se déconnecte…")
    await a.leave()
    ok = await wait_for(
        lambda: len(b.members) == 1 and b.host_id == bob.peer_id,
        label="bascule de l'hôte vers Bob",
    )
    results.append(("Hôte basculé vers Bob après départ de l'hôte", ok))

    print("\nRésultats :")
    for label, passed in results:
        print(f"  {'✓' if passed else '✗'} {label}")

    await b.leave()
    store_a.close()
    store_b.close()
    await runner.cleanup()

    return 0 if all(passed for _, passed in results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
