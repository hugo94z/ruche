"""Test du chantier « Messagerie » (sans interface graphique).

Vérifie, sur de vrais pairs reliés par un rendez-vous en mémoire :

* plusieurs salons **simultanés** dans une même instance, isolés les uns des
  autres (historique, membres, hôte) ;
* l'**édition**, la **suppression** et les **réactions**, modélisées par des
  opérations signées append-only (l'entrée d'origine n'est jamais réécrite) ;
* la **reprise des transferts** (repartir du dernier octet reçu).

    python tools/messaging_test.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
except Exception:
    pass

from aiohttp import web  # noqa: E402

from app import config  # noqa: E402

_TMP = Path(tempfile.mkdtemp(prefix="ruche-msg-"))
config.data_dir = lambda: _TMP  # type: ignore[assignment]

from app.core.files import FileStore  # noqa: E402
from app.core.identity import Identity, create as create_identity  # noqa: E402
from app.core.room import RoomManager, dm_key  # noqa: E402
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


def views(manager: RoomManager, room: str) -> list[dict]:
    session = manager.rooms[room]
    return session.history.all_views()


async def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    runner, url = await start_rendezvous()

    store_a = Storage(_TMP / "a.sqlite3")
    store_b = Storage(_TMP / "b.sqlite3")
    store_c = Storage(_TMP / "c.sqlite3")
    alice = make_identity("Alice")
    bob = make_identity("Bob")
    carol = make_identity("Carol")
    a = RoomManager(store_a, alice)
    b = RoomManager(store_b, bob)
    c = RoomManager(store_c, carol)

    results: list[tuple[str, bool]] = []

    # --- Plusieurs salons simultanés --------------------------------------
    print("1. Deux salons en parallèle sur la même instance (Alice)…")
    await a.join("ROOM01", "Alice", url, use_lan=False)
    await b.join("ROOM01", "Bob", url, use_lan=False)
    await a.join("ROOM02", "Alice", url, use_lan=False)
    await c.join("ROOM02", "Carol", url, use_lan=False)

    ok = await wait_for(
        lambda: len(a.rooms) == 2
        and len(a.rooms["ROOM02"].members) == 2
        and len(b.rooms["ROOM01"].members) == 2,
        label="salons multiples",
    )
    results.append(("Deux salons ouverts simultanément", ok))
    results.append(("Le salon actif est le dernier rejoint", a.room == "ROOM02"))

    print("2. Isolation des salons…")
    await a.switch_room("ROOM01")
    await a.send_text("Message du salon 1")
    ok = await wait_for(
        lambda: any(e["body"] == "Message du salon 1" for e in views(b, "ROOM01")),
        label="réception dans ROOM01",
    )
    results.append(("Message reçu dans ROOM01", ok))
    results.append(
        ("ROOM02 n'a pas vu le message de ROOM01", not views(c, "ROOM02"))
    )
    results.append(("Chaque salon a son propre hôte", a.rooms["ROOM01"].host_id is not None))

    # --- Édition / réactions / suppression --------------------------------
    print("3. Édition d'un message…")
    await a.send_text("Texte original")
    ok = await wait_for(
        lambda: any(e["body"] == "Texte original" for e in views(a, "ROOM01")),
        label="message initial",
    )
    original = next(e for e in views(a, "ROOM01") if e["body"] == "Texte original")
    await a.edit_message(original["id"], "Texte corrigé")
    ok = await wait_for(
        lambda: any(e["body"] == "Texte corrigé" for e in views(b, "ROOM01")),
        label="édition répliquée",
    )
    results.append(("Édition répliquée chez Bob", ok))
    edited = next(e for e in views(b, "ROOM01") if e["id"] == original["id"])
    results.append(("Le message est marqué édité", bool(edited.get("edited"))))
    results.append(
        ("L'entrée d'origine n'est pas réécrite",
         raw_body(a, "ROOM01", original["id"]) == "Texte original")
    )

    print("4. Un non-auteur ne peut pas éditer…")
    await b.edit_message(original["id"], "Sabotage")
    await asyncio.sleep(0.3)
    results.append(
        ("Édition d'autrui ignorée",
         next(e for e in views(a, "ROOM01") if e["id"] == original["id"])["body"]
         == "Texte corrigé")
    )

    print("5. Réactions…")
    await b.toggle_reaction(original["id"], "👍")
    ok = await wait_for(
        lambda: next((e for e in views(a, "ROOM01") if e["id"] == original["id"]),
                     {}).get("reactions", {}).get("👍") == ["Bob"],
        label="réaction propagée",
    )
    results.append(("Réaction propagée", ok))
    await b.toggle_reaction(original["id"], "👍")
    ok = await wait_for(
        lambda: not next((e for e in views(a, "ROOM01") if e["id"] == original["id"]),
                         {}).get("reactions", {}).get("👍"),
        label="réaction retirée",
    )
    results.append(("Réaction retirée par bascule", ok))

    print("6. Suppression…")
    await a.delete_message(original["id"])
    ok = await wait_for(
        lambda: all(e["id"] != original["id"] for e in views(b, "ROOM01")),
        label="suppression propagée",
    )
    results.append(("Message supprimé masqué partout", ok))
    results.append(
        ("Le message reste dans le journal brut (réplication)",
         any(e["id"] == original["id"] for e in b.rooms["ROOM01"].history.all_sorted()))
    )

    # --- Reprise des transferts -------------------------------------------
    print("7. Reprise d'un transfert interrompu…")
    store = Storage(_TMP / "files.sqlite3")
    files = FileStore(store, _TMP / "store")
    payload = os.urandom(300 * 1024)
    digest = __import__("hashlib").sha256(payload).hexdigest()

    files.begin_receive(digest, "gros.bin", len(payload), "application/octet-stream")
    files.write_chunk(digest, payload[:100 * 1024])
    files.pause(digest)  # coupure simulée : le .part reste sur le disque
    results.append(("Octets partiels conservés", files.received_bytes(digest) == 100 * 1024))

    resumed = files.begin_receive(
        digest, "gros.bin", len(payload), "application/octet-stream",
        offset=files.received_bytes(digest),
    )
    files.write_chunk(digest, payload[100 * 1024:])
    record = files.finish_receive(digest)
    results.append(("Reprise acceptée", resumed))
    results.append(("Fichier complet après reprise", record is not None))
    results.append(("Intégrité vérifiée après reprise", record is not None and record.sha256 == digest))

    print("8. Vignettes…")
    try:
        from PIL import Image

        photo = _TMP / "photo.png"
        Image.new("RGB", (900, 600), (200, 30, 30)).save(photo)
        photo_record = files.prepare(photo)
        thumb = files.thumbnail(photo_record.id)
        results.append(("Vignette générée", thumb is not None and thumb.exists()))
        if thumb is not None:
            with Image.open(thumb) as image:
                results.append(("Vignette réduite (≤ 320 px)", max(image.size) <= 320))
        plain = _TMP / "note.bin"
        plain.write_bytes(b"pas une image")
        results.append(("Pas de vignette pour un non-image", files.thumbnail(files.prepare(plain).id) is None))
    except ImportError:
        results.append(("Pillow disponible", False))

    print("9. Messages privés persistants…")
    alice_id = a.identity.peer_id
    bob_id = b.identity.peer_id
    await a.start_dm(bob_id, "Bob", url, use_lan=False)
    await b.start_dm(alice_id, "Alice", url, use_lan=False)
    dmcode = dm_key(alice_id, bob_id)
    ok = await wait_for(
        lambda: bool(a.rooms[dmcode].transport.connected_peers())
        and bool(b.rooms[dmcode].transport.connected_peers()),
        label="connexion du message privé",
    )
    results.append(("Conversation privée connectée", ok))
    await a.send_text("Coucou en privé")
    ok = await wait_for(
        lambda: any(e["body"] == "Coucou en privé" for e in views(b, dmcode)),
        label="réception du message privé",
    )
    results.append(("Message privé reçu", ok))
    results.append(("Le MP est une session distincte", a.rooms[dmcode].is_dm and a.rooms[dmcode].peer_id == bob_id))
    results.append(
        ("Le MP n'apparaît pas dans le salon public",
         not any(e["body"] == "Coucou en privé" for e in views(a, "ROOM01")))
    )

    # Persistance : on ferme puis rouvre la conversation.
    await a.leave_room(dmcode)
    await asyncio.sleep(0.2)
    await a.start_dm(bob_id, "Bob", url, use_lan=False)
    results.append(
        ("Message privé toujours là après réouverture",
         any(e["body"] == "Coucou en privé" for e in views(a, dmcode)))
    )
    store.close()

    await a.close()
    await b.close()
    await c.close()
    store_a.close()
    store_b.close()
    store_c.close()
    await runner.cleanup()

    print("\nRésultats :")
    for label, passed in results:
        print(f"  {'✓' if passed else '✗'} {label}")
    return 0 if all(passed for _, passed in results) else 1


def raw_body(manager: RoomManager, room: str, entry_id: str) -> str:
    """Corps de l'entrée brute (sans repliement des opérations)."""
    for entry in manager.rooms[room].history.all_sorted():
        if entry["id"] == entry_id:
            return entry["body"]
    return ""


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
