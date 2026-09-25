"""Test du chantier « Hors ligne » : boîte aux lettres chiffrée.

Vérifie, sur de vrais pairs reliés par un rendez-vous en mémoire :

* les messages privés destinés à un pair **hors ligne** sont scellés (X25519 +
  ChaCha20-Poly1305) et mis en attente, jamais stockés en clair ;
* ils sont **livrés à la reconnexion** du pair, dès qu'un lien s'ouvre — même
  si la conversation privée n'est pas ouverte ;
* un **accusé de réception** vide la boîte ;
* un jeton falsifié ou scellé pour un autre destinataire est **rejeté**.

    python tools/offline_test.py
"""

from __future__ import annotations

import asyncio
import logging
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

_TMP = Path(tempfile.mkdtemp(prefix="ruche-offline-"))
config.data_dir = lambda: _TMP  # type: ignore[assignment]

from app.core import crypto  # noqa: E402
from app.core.identity import Identity, create as create_identity  # noqa: E402
from app.core.room import RoomManager, dm_key  # noqa: E402
from app.core.storage import Storage  # noqa: E402
from app.rendezvous.server import build_app  # noqa: E402


def make_identity(pseudo: str) -> Identity:
    return create_identity(pseudo)


async def wait_for(predicate, timeout: float = 40.0, label: str = "") -> bool:
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


def dm_bodies(store: Storage, code: str) -> list[str]:
    return [row["body"] for row in store.load_messages(code)]


async def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    runner, url = await start_rendezvous()

    store_a = Storage(_TMP / "a.sqlite3")
    store_b = Storage(_TMP / "b.sqlite3")
    alice = make_identity("Alice")
    bob = make_identity("Bob")
    a = RoomManager(store_a, alice)
    b = RoomManager(store_b, bob)

    results: list[tuple[str, bool]] = []

    # --- Primitives -------------------------------------------------------
    print("1. Scellement à clé publique…")
    priv, pub = crypto.generate_enc_keys()
    token = crypto.seal(pub, b"secret")
    results.append(("Déchiffrement correct", crypto.unseal(priv, token) == b"secret"))
    other_priv, _ = crypto.generate_enc_keys()
    results.append(("Mauvais destinataire rejeté", crypto.unseal(other_priv, token) is None))
    results.append(("Jeton corrompu rejeté", crypto.unseal(priv, "pas-un-jeton") is None))
    results.append(("Clés de chiffrement générées", bool(alice.enc_public and alice.enc_private)))

    # --- Échange des clés de chiffrement ---------------------------------
    print("2. Échange des clés dans le salon public…")
    await a.join("ROOM01", "Alice", url, use_lan=False)
    await b.join("ROOM01", "Bob", url, use_lan=False)
    alice_id = a.identity.peer_id
    bob_id = b.identity.peer_id
    ok = await wait_for(
        lambda: (store_a.peer(bob_id) or {}).get("enc_key") == bob.enc_public,
        label="clé de chiffrement reçue",
    )
    results.append(("Alice connaît la clé de chiffrement de Bob", ok))

    # --- Bob passe hors ligne --------------------------------------------
    print("3. Bob se déconnecte, Alice lui écrit en privé…")
    await b.leave()
    await asyncio.sleep(0.3)
    dmcode = dm_key(alice_id, bob_id)
    await a.start_dm(bob_id, "Bob", url, use_lan=False)
    await a.send_text("Message différé un")
    await a.send_text("Message différé deux")

    ok = await wait_for(lambda: store_a.mail_count() == 2, label="mise en boîte")
    results.append(("Deux messages mis en attente", ok))
    mails = store_a.mails_for(bob_id)
    results.append(
        ("La boîte ne contient pas le texte en clair",
         bool(mails) and all("Message différé" not in m["blob"] for m in mails))
    )
    results.append(
        ("Les messages scellés ne sont pas lisibles par un tiers",
         all(crypto.unseal(crypto.generate_enc_keys()[0], m["blob"]) is None for m in mails))
    )

    # --- Bob revient : livraison différée --------------------------------
    print("4. Bob se reconnecte au salon : livraison automatique…")
    await b.join("ROOM01", "Bob", url, use_lan=False)
    ok = await wait_for(
        lambda: "Message différé un" in dm_bodies(store_b, dmcode),
        label="livraison différée",
    )
    results.append(("Message différé livré à la reconnexion", ok))
    results.append(
        ("Les deux messages sont arrivés dans l'ordre",
         dm_bodies(store_b, dmcode) == ["Message différé un", "Message différé deux"])
    )
    ok = await wait_for(lambda: store_a.mail_count() == 0, label="accusé de réception")
    results.append(("Boîte vidée après accusé de réception", ok))

    # --- Ouverture de la conversation ------------------------------------
    print("5. Bob ouvre la conversation privée…")
    await b.start_dm(alice_id, "Alice", url, use_lan=False)
    session = b.rooms.get(dmcode)
    results.append(
        ("Les messages différés sont dans la conversation",
         session is not None
         and [e["body"] for e in session.history.all_views()] == [
             "Message différé un", "Message différé deux",
         ])
    )

    # --- En ligne : pas de mise en boîte ---------------------------------
    print("6. Les deux pairs en ligne : livraison directe…")
    await wait_for(
        lambda: bool(a.rooms[dmcode].transport.connected_peers())
        and bool(b.rooms[dmcode].transport.connected_peers()),
        label="lien privé",
    )
    before = store_a.mail_count()
    await a.send_text("En direct")
    ok = await wait_for(
        lambda: any(e["body"] == "En direct" for e in session.history.all_views()),
        label="réception directe",
    )
    results.append(("Message privé livré en direct", ok))
    results.append(("Aucune mise en boîte quand le pair est là", store_a.mail_count() == before))

    # --- Pair joignable par un salon commun, MP fermé --------------------
    print("7. Pair en ligne ailleurs, conversation privée fermée…")
    await b.leave_room(dmcode)
    ok = await wait_for(
        lambda: not a.rooms[dmcode].transport.links.get(bob_id),
        label="fermeture du lien privé",
    )
    before = store_a.mail_count()
    await a.send_text("Livré via le salon commun")
    ok = await wait_for(
        lambda: "Livré via le salon commun" in dm_bodies(store_b, dmcode),
        label="livraison via un autre salon",
    )
    results.append(("Livré via un salon commun, sans reconnexion", ok))
    results.append(("Boîte refermée après livraison", store_a.mail_count() == before))

    # --- Sceau altéré -----------------------------------------------------
    print("8. Un message différé falsifié est ignoré…")
    b.receive_mail(alice_id, "faux", "jeton-invalide")
    results.append(("Sceau invalide sans effet", "jeton-invalide" not in dm_bodies(store_b, dmcode)))

    await a.close()
    await b.close()
    store_a.close()
    store_b.close()
    await runner.cleanup()

    print("\nRésultats :")
    for label, passed in results:
        print(f"  {'✓' if passed else '✗'} {label}")
    return 0 if all(passed for _, passed in results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
