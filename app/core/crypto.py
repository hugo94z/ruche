"""Primitives cryptographiques de Ruche.

- **Signature Ed25519** : chaque message est signé par son auteur. Comme
  l'identifiant d'un pair est dérivé de sa clé publique, personne ne peut se
  faire passer pour un autre : une signature invalide est rejetée, et un pair
  qui change de clé pour le même pseudo est détecté.
- **Chiffrement de bout en bout** : quand un mot de passe de salon est défini,
  le contenu des messages (corps, pseudo, métadonnées) est chiffré en
  AES-GCM avec une clé dérivée du mot de passe (scrypt). Ni le serveur de
  rendez-vous ni un relais TURN ne peuvent le lire.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

# Champs qui sont signés (tout sauf la signature elle-même).
SIGNED_FIELDS = ("id", "ts", "origin", "pseudo", "kind", "body", "extra", "at", "enc", "pub")

# Paramètres scrypt : coûteux en mémoire, donc difficile à casser par force brute.
_SCRYPT_N = 2**15
_SCRYPT_R = 8
_SCRYPT_P = 1
_KEY_LEN = 32


# --------------------------------------------------------------------------
# Clés
# --------------------------------------------------------------------------
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


# --------------------------------------------------------------------------
# Signature
# --------------------------------------------------------------------------
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


# --------------------------------------------------------------------------
# Chiffrement de bout en bout (mot de passe de salon)
# --------------------------------------------------------------------------
def derive_room_key(password: str, room: str) -> bytes:
    """Dérive la clé du salon. Le sel vient du code du salon, donc tous les
    participants calculent la même clé sans se concerter."""
    salt = hashlib.sha256(("ruche-room:" + room).encode("utf-8")).digest()
    kdf = Scrypt(salt=salt, length=_KEY_LEN, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P)
    return kdf.derive(password.encode("utf-8"))


def encrypt(key: bytes, plaintext: bytes) -> str:
    nonce = os.urandom(12)
    sealed = AESGCM(key).encrypt(nonce, plaintext, None)
    return base64.b64encode(nonce + sealed).decode("ascii")


def decrypt(key: bytes, token: str) -> bytes | None:
    try:
        raw = base64.b64decode(token)
        return AESGCM(key).decrypt(raw[:12], raw[12:], None)
    except Exception:
        return None


def password_hint(password: str, room: str) -> str:
    """Empreinte courte du mot de passe : permet de vérifier que deux
    participants ont bien le même, sans le transmettre."""
    digest = hashlib.sha256(("ruche-hint:" + room + ":" + password).encode("utf-8")).hexdigest()
    return digest[:8].upper()


# Réexport pratique pour les tests et le reste de l'application.
__all__ = [
    "SIGNED_FIELDS",
    "canonical_bytes",
    "decrypt",
    "derive_room_key",
    "encrypt",
    "fingerprint",
    "password_hint",
    "peer_id_for",
    "public_key_from_base64",
    "private_key_from_base64",
    "sign",
    "verify",
]
