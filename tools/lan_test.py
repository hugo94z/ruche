"""Test du mode local : deux pairs se trouvent via mDNS et discutent SANS
serveur de rendez-vous (signalisation WebRTC par HTTP direct sur le réseau).

    python tools/lan_test.py
"""

from __future__ import annotations

import asyncio
import logging
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

from app import config  # noqa: E402

_TMP = Path(tempfile.mkdtemp(prefix="ruche-lan-"))
config.data_dir = lambda: _TMP  # type: ignore[assignment]

from app.core.identity import Identity  # noqa: E402
from app.core.room import RoomManager  # noqa: E402
from app.core.storage import Storage  # noqa: E402


def make_identity(pseudo: str) -> Identity:
    return Identity(
        peer_id=uuid.uuid4().hex[:16], pseudo=pseudo, public_key="", private_key=""
    )


async def wait_for(predicate, timeout: float = 30.0, label: str = "") -> bool:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if predicate():
            return True
        await asyncio.sleep(0.2)
    print(f"  ✗ délai dépassé : {label}")
    return False


async def main() -> int:
    logging.basicConfig(level=logging.WARNING)

    store_a = Storage(_TMP / "a.sqlite3")
    store_b = Storage(_TMP / "b.sqlite3")
    a = RoomManager(store_a, make_identity("Alice"))
    b = RoomManager(store_b, make_identity("Bob"))

    results: list[tuple[str, bool]] = []

    print("1. Connexion sans serveur de rendez-vous (découverte mDNS)…")
    await a.join("LOCAL1", "Alice", "")
    await b.join("LOCAL1", "Bob", "")

    ok = await wait_for(
        lambda: len(a.members) == 2 and len(b.members) == 2, label="découverte des pairs"
    )
    results.append(("Pairs découverts en local (mDNS)", ok))

    ok = await wait_for(
        lambda: bool(a.transport.connected_peers()) and bool(b.transport.connected_peers()),
        label="maillage sans rendez-vous",
    )
    results.append(("Maillage WebRTC établi sans serveur", ok))

    print("2. Message de bout en bout…")
    await a.send_text("Bonjour en local !")
    ok = await wait_for(
        lambda: any(e["body"] == "Bonjour en local !" for e in b.history.all_sorted()),
        label="réception du message",
    )
    results.append(("Message reçu par Bob", ok))

    ok = await wait_for(
        lambda: a.host_id is not None and a.host_id == b.host_id, label="hôte commun"
    )
    results.append(("Hôte élu de façon identique", ok))

    await a.leave()
    await b.leave()
    store_a.close()
    store_b.close()

    print("\nRésultats :")
    for label, passed in results:
        print(f"  {'✓' if passed else '✗'} {label}")
    return 0 if all(passed for _, passed in results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
