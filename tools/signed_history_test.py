"""Test du journal signé : authentification des messages répliqués.

    python tools/signed_history_test.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
except Exception:
    pass

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
import base64  # noqa: E402

from app.core import crypto  # noqa: E402
from app.core.history import HistoryLog  # noqa: E402
from app.core.storage import Storage  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="ruche-signed-"))
ROOM = "SIGNED1"


def make_keys() -> tuple[str, str, str]:
    private = Ed25519PrivateKey.generate()
    priv = base64.b64encode(
        private.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
    ).decode()
    pub = base64.b64encode(
        private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode()
    return priv, pub, crypto.peer_id_for(pub)


def build_log(name: str) -> tuple[HistoryLog, str]:
    priv, pub, pid = make_keys()
    log = HistoryLog(Storage(TMP / f"{name}.sqlite3"))
    log.configure(priv, pub)
    log.load_room(ROOM)
    return log, pid


def main() -> int:
    results: list[tuple[str, bool]] = []

    alice, alice_id = build_log("alice")
    bob, bob_id = build_log("bob")
    carol, carol_id = build_log("carol")

    raw = alice.add_local("text", "Bonjour à tous", origin=alice_id, pseudo="Alice")
    results.append(("L'entrée est signée", bool(raw.get("sig"))))
    results.append(
        ("Clé publique embarquée", crypto.peer_id_for(raw.get("pub", "")) == alice_id)
    )

    added = bob.merge([raw])
    results.append(("Bob accepte le message signé", len(added) == 1))
    results.append(("Contenu correct", bool(added) and added[0]["body"] == "Bonjour à tous"))
    results.append(("Pseudo correct", bool(added) and added[0]["pseudo"] == "Alice"))

    forged = dict(raw)
    forged["body"] = "Message falsifié"
    fresh, _ = build_log("fresh")
    results.append(("Message falsifié rejeté", fresh.merge([forged]) == []))

    imposter_priv, imposter_pub, _ = make_keys()
    stolen = dict(raw, pub=imposter_pub)
    stolen["sig"] = crypto.sign(imposter_priv, stolen)
    impostor_log, _ = build_log("impostor")
    results.append(("Usurpation d'identité rejetée", impostor_log.merge([stolen]) == []))

    unsigned = dict(raw)
    unsigned.pop("sig")
    unsigned_log, _ = build_log("unsigned")
    results.append(("Entrée non signée refusée", unsigned_log.merge([unsigned]) == []))

    second = alice.add_local("text", "Deuxième", origin=alice_id, pseudo="Alice")
    bob.merge([second])
    results.append(("Pas de doublon à la resynchronisation", bob.merge([raw, second]) == []))
    results.append(("Bob a bien les deux messages", len(bob.all_sorted()) == 2))

    # Un troisième pair récupère tout depuis Bob (relais d'historique).
    relayed = carol.merge(bob.raw_entries())
    results.append(("Historique relayé et vérifié", len(relayed) == 2))
    results.append(("Tous les auteurs authentifiés", all(carol._authentic(e) for e in carol.raw_entries())))

    print("Résultats :")
    for label, passed in results:
        print(f"  {'✓' if passed else '✗'} {label}")
    return 0 if all(passed for _, passed in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
