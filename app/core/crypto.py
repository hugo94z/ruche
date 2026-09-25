"""Primitives cryptographiques de Ruche : signature des messages.

Chaque message est signé par son auteur avec sa clé Ed25519. Comme
l'identifiant d'un pair est dérivé de sa clé publique, personne ne peut se
faire passer pour un autre : une signature invalide est rejetée, et un pair
qui change de clé pour le même pseudo est détecté.
"""

from __future__ import annotations

import base64
import hashlib
import json

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

# Champs qui sont signés (tout sauf la signature elle-même).
SIGNED_FIELDS = ("id", "ts", "origin", "pseudo", "kind", "body", "extra", "at", "pub")


def public_key_from_base64(value: str) -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(base64.b64decode(value))


def private_key_from_base64(value: str) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(base64.b64decode(value))


def peer_id_for(public_key_b64: str) -> str:
    """L'identifiant d'un pair est le début du SHA-256 de sa clé publique."""
    return hashlib.sha256(base64.b64decode(public_key_b64)).hexdigest()[:16]


def fingerprint(public_key_b64: str) -> str:
    """Empreinte lisible, à comparer de vive voix (comme Signal)."""
    digest = hashlib.sha256(base64.b64decode(public_key_b64)).hexdigest()
    groups = [digest[i : i + 4] for i in range(0, 32, 4)]
    return " ".join(groups).upper()


def canonical_bytes(entry: dict) -> bytes:
    """Représentation déterministe d'une entrée, pour la signature."""
    subset = {key: entry.get(key) for key in SIGNED_FIELDS if key in entry}
    return json.dumps(subset, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def sign(private_key_b64: str, entry: dict) -> str:
    signature = private_key_from_base64(private_key_b64).sign(canonical_bytes(entry))
    return base64.b64encode(signature).decode("ascii")


def verify(public_key_b64: str, entry: dict, signature_b64: str) -> bool:
    try:
        public_key_from_base64(public_key_b64).verify(
            base64.b64decode(signature_b64), canonical_bytes(entry)
        )
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


__all__ = [
    "SIGNED_FIELDS",
    "canonical_bytes",
    "fingerprint",
    "peer_id_for",
    "public_key_from_base64",
    "private_key_from_base64",
    "sign",
    "verify",
]
