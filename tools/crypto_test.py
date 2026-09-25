"""Test des primitives cryptographiques : signature Ed25519.

    python tools/crypto_test.py
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
except Exception:
    pass

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from app.core import crypto  # noqa: E402


def make_keys() -> tuple[str, str]:
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
    return priv, pub


def main() -> int:
    results: list[tuple[str, bool]] = []
    alice_priv, alice_pub = make_keys()
    bob_priv, bob_pub = make_keys()

    entry = {
        "id": "abc123",
        "ts": 4,
        "origin": crypto.peer_id_for(alice_pub),
        "pseudo": "Alice",
        "kind": "text",
        "body": "Bonjour Bob",
        "extra": {"x": 1},
        "at": 1730000000.0,
        "pub": alice_pub,
    }

    sig = crypto.sign(alice_priv, entry)
    entry["sig"] = sig

    results.append(("Signature valide acceptée", crypto.verify(alice_pub, entry, sig)))
    results.append(("Signature d'autrui refusée", not crypto.verify(bob_pub, entry, sig)))

    tampered = dict(entry, body="Message modifié")
    results.append(("Message modifié détecté", not crypto.verify(alice_pub, tampered, sig)))

    tampered_pseudo = dict(entry, pseudo="Bob")
    results.append(
        ("Pseudo modifié détecté", not crypto.verify(alice_pub, tampered_pseudo, sig))
    )

    results.append(
        ("Identifiant dérivé de la clé", crypto.peer_id_for(alice_pub) == entry["origin"])
    )
    results.append(
        ("Empreintes différentes", crypto.fingerprint(alice_pub) != crypto.fingerprint(bob_pub))
    )
    print("  empreinte d'Alice :", crypto.fingerprint(alice_pub))

    results.append(
        ("Empreinte stable", crypto.fingerprint(alice_pub) == crypto.fingerprint(alice_pub))
    )

    print("\nRésultats :")
    for label, passed in results:
        print(f"  {'✓' if passed else '✗'} {label}")
    return 0 if all(passed for _, passed in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
