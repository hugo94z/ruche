"""Test des primitives cryptographiques (signature et chiffrement de bout en bout).

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


def make_identity() -> tuple[str, str]:
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
    alice_priv, alice_pub = make_identity()
    bob_priv, bob_pub = make_identity()

    entry = {
        "id": "abc123",
        "ts": 4,
        "origin": crypto.peer_id_for(alice_pub),
        "kind": "text",
        "body": "Bonjour Bob",
        "extra": {"x": 1},
        "at": 1730000000.0,
        "pub": alice_pub,
    }

    sig = crypto.sign(alice_priv, entry)
    entry["sig"] = sig

    results.append(("Signature valide acceptée", crypto.verify(alice_pub, entry, sig)))
    results.append(
        ("Signature d'autrui refusée", not crypto.verify(bob_pub, entry, sig))
    )

    tampered = dict(entry, body="Message modifié")
    results.append(
        ("Message modifié détecté", not crypto.verify(alice_pub, tampered, sig))
    )

    results.append(
        (
            "Identifiant dérivé de la clé",
            crypto.peer_id_for(alice_pub) == entry["origin"],
        )
    )
    results.append(("Empreintes différentes", crypto.fingerprint(alice_pub) != crypto.fingerprint(bob_pub)))
    print("  empreinte d'Alice :", crypto.fingerprint(alice_pub))

    # --- Chiffrement de bout en bout -------------------------------------
    key_a = crypto.derive_room_key("secret123", "SALON1")
    key_b = crypto.derive_room_key("secret123", "SALON1")
    key_wrong = crypto.derive_room_key("autre", "SALON1")
    key_other_room = crypto.derive_room_key("secret123", "SALON2")

    results.append(("Clé de salon déterministe", key_a == key_b))
    results.append(("Autre mot de passe → autre clé", key_a != key_wrong))
    results.append(("Autre salon → autre clé", key_a != key_other_room))

    token = crypto.encrypt(key_a, b"contenu prive")
    results.append(("Déchiffrement correct", crypto.decrypt(key_b, token) == b"contenu prive"))
    results.append(("Mauvais mot de passe refusé", crypto.decrypt(key_wrong, token) is None))
    results.append(("Altération du chiffré détectée", crypto.decrypt(key_a, token[:-4] + "AAAA") is None))
    results.append(("Indice de mot de passe stable", crypto.password_hint("secret123", "SALON1") == crypto.password_hint("secret123", "SALON1")))
    results.append(("Indice différent si mot de passe différent", crypto.password_hint("secret123", "SALON1") != crypto.password_hint("secret124", "SALON1")))

    print("\nRésultats :")
    for label, passed in results:
        print(f"  {'✓' if passed else '✗'} {label}")
    return 0 if all(passed for _, passed in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
