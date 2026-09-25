"""Test de l'hébergement d'un rendez-vous depuis l'application.

Démarre un rendez-vous en processus (comme le fait le bouton
« Héberger un rendez-vous »), y connecte deux clients et vérifie qu'ils se
découvrent mutuellement.

    python tools/host_test.py
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
except Exception:
    pass

from app.core.hosting import RendezvousHost  # noqa: E402
from app.core.network.rendezvous import RendezvousClient  # noqa: E402


async def _noop(*_args, **_kwargs) -> None:
    return None


async def wait_for(predicate, timeout: float = 15.0, label: str = "") -> bool:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if predicate():
            return True
        await asyncio.sleep(0.1)
    print(f"  ✗ délai dépassé : {label}")
    return False


async def main() -> int:
    results: list[tuple[str, bool]] = []

    host = RendezvousHost()
    port = await host.start()
    print(f"Hébergement démarré sur le port {port}")
    results.append(("Hébergement démarré", host.hosting and port > 0))

    urls = host.share_urls()
    print("Adresses de partage :", urls)
    results.append(
        ("Au moins une adresse de partage", len(urls) >= 1 and urls[0].startswith("ws://"))
    )
    results.append(
        ("Adresse de boucle locale correcte", host.loopback_url() == f"ws://127.0.0.1:{port}/ws")
    )

    a_peers: list[dict] = []
    b_peers: list[dict] = []
    a = RendezvousClient(
        host.loopback_url(), "HT01", "alice0000000000", "Alice",
        on_peers=lambda peers: a_peers.extend(peers),
        on_peer_joined=lambda pid, pseudo: a_peers.append({"id": pid, "pseudo": pseudo}),
        on_peer_left=lambda pid: None,
        on_signal=_noop,
        on_status=lambda _s: None,
    )
    b = RendezvousClient(
        host.loopback_url(), "HT01", "bob00000000000", "Bob",
        on_peers=lambda peers: b_peers.extend(peers),
        on_peer_joined=lambda pid, pseudo: b_peers.append({"id": pid, "pseudo": pseudo}),
        on_peer_left=lambda pid: None,
        on_signal=_noop,
        on_status=lambda _s: None,
    )
    await a.start()
    await asyncio.sleep(0.6)
    await b.start()

    ok = await wait_for(lambda: a_peers and b_peers, label="découverte mutuelle")
    results.append(("Les deux clients se voient", ok))
    if a_peers:
        print(f"  Alice voit : {[p.get('pseudo') for p in a_peers]}")
    if b_peers:
        print(f"  Bob voit   : {[p.get('pseudo') for p in b_peers]}")

    await a.stop()
    await b.stop()

    await host.stop()
    results.append(("Hébergement arrêté", not host.hosting))

    print("\nRésultats :")
    for label, passed in results:
        print(f"  {'✓' if passed else '✗'} {label}")
    return 0 if all(passed for _, passed in results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
