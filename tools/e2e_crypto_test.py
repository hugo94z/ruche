"""Test du journal signé et chiffré : signature, vérification, E2E.

    python tools/e2e_crypto_test.py
"""

from __future__ import annotations

import base64
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

from app.core import crypto  # noqa: E402
from app.core.history import HistoryLog  # noqa: E402
from app.core.storage import Storage  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="ruche-crypto-"))
ROOM = "CRYPTO1"
PASSWORD = "motdepasse-salon"


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


def build_log(name: str, password: str | None = PASSWORD) -> tuple[HistoryLog, str, str]:
    priv, pub, pid = make_keys()
    log = HistoryLog(Storage(TMP / f"{name}.sqlite3"))
    key = crypto.derive_room_key(password, ROOM) if password else None
    log.configure(priv, pub, key)
    log.load_room(ROOM)
    return log, pid, pub


def main() -> int:
    results: list[tuple[str, bool]] = []

    alice, alice_id, alice_pub = build_log("alice")
    bob, bob_id, bob_pub = build_log("bob")
    eve, _, _ = build_log("eve", password="mauvais")

    raw = alice.add_local("text", "Message secret", origin=alice_id, pseudo="Alice")

    results.append(("Le message est chiffré sur le fil", raw.get("kind") == "enc"))
    results.append(
        ("Le texte en clair n'apparaît pas", "Message secret" not in str(raw))
    )
    results.append(("L'entrée est signée", bool(raw.get("sig"))))
    results.append(
        ("Clé publique embarquée", crypto.peer_id_for(raw.get("pub", "")) == alice_id)
    )

    added = bob.merge([raw])
    results.append(("Bob déchiffre le message", len(added) == 1))
    results.append(
        ("Contenu correct chez Bob", bool(added) and added[0]["body"] == "Message secret")
    )
    results.append(
        ("Pseudo correct chez Bob", bool(added) and added[0]["pseudo"] == "Alice")
    )

    # Eve a le bon salon mais le mauvais mot de passe.
    eve_added = eve.merge([raw])
    results.append(("Mauvais mot de passe : rien de lisible", eve_added == []))
    results.append(("L'entrée reste stockée mais illisible", len(eve.raw_entries()) == 1))

    # Falsification du texte chiffré.
    forged = dict(raw)
    forged["body"] = raw["body"][:-6] + "AAAAAA"
    other, _, _ = build_log("carol")
    results.append(("Entrée falsifiée rejetée", other.merge([forged]) == []))

    # Usurpation : quelqu'un signe avec sa clé mais se déclare Alice.
    imposter_priv, imposter_pub, _ = make_keys()
    stolen = {
        "id": "deadbeef",
        "ts": 99,
        "origin": alice_id,
        "at": 1.0,
        "pub": imposter_pub,
        "kind": "enc",
        "body": raw["body"],
        "pseudo": "",
        "extra": {},
    }
    stolen["sig"] = crypto.sign(imposter_priv, stolen)
    dave, _, _ = build_log("dave")
    results.append(("Usurpation d'identité rejetée", dave.merge([stolen]) == []))

    # Message non signé (ancienne version) : refusé.
    unsigned = dict(raw)
    unsigned.pop("sig")
    erin, _, _ = build_log("erin")
    results.append(("Entrée non signée refusée", erin.merge([unsigned]) == []))

    # Deux messages puis relance : pas de doublon.
    second = alice.add_local("text", "Deuxième", origin=alice_id, pseudo="Alice")
    bob.merge([second])
    again = bob.merge([raw, second])
    results.append(("Pas de doublon à la resynchronisation", again == []))
    results.append(("Bob a bien les deux messages", len(bob.all_sorted()) == 2))

    print("Résultats :")
    for label, passed in results:
        print(f"  {'✓' if passed else '✗'} {label}")
    return 0 if all(passed for _, passed in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
