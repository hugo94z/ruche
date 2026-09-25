"""Primitives cryptographiques de Ruche : signature et boîte aux lettres.

Chaque message est signé par son auteur avec sa clé Ed25519. Comme
l'identifiant d'un pair est dérivé de sa clé publique, personne ne peut se
faire passer pour un autre : une signature invalide est rejetée, et un pair
qui change de clé pour le même pseudo est détecté.

À côté de la signature (qui garantit l'**authenticité**), une paire de clés
X25519 sert à **sceller** les messages en attente de livraison (boîte aux
lettres) : seul le destinataire peut les ouvrir, même s'ils transitent par un
tiers ou dorment sur un disque.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

# Champs qui sont signés (tout sauf la signature elle-même).
SIGNED_FIELDS = ("id", "ts", "origin", "pseudo", "kind", "body", "extra", "at", "pub")

# Contexte de dérivation de clé pour la boîte aux lettres.
_MAILBOX_INFO = b"ruche-mailbox-v1"


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


# --- Boîte aux lettres : scellement à clé publique -----------------------
def generate_enc_keys() -> tuple[str, str]:
    """Nouvelle paire X25519 ``(clé privée, clé publique)`` en base64."""
    private = X25519PrivateKey.generate()
    private_b64 = base64.b64encode(
        private.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
    ).decode("ascii")
    public_b64 = base64.b64encode(
        private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode("ascii")
    return private_b64, public_b64


def _derive_shared(shared: bytes) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(), length=32, salt=None, info=_MAILBOX_INFO
    ).derive(shared)


def seal(recipient_public_b64: str, plaintext: bytes) -> str:
    """Scelle ``plaintext`` pour le porteur de ``recipient_public_b64``.

    Utilise un échange éphémère X25519 puis un chiffrement authentifié
    (ChaCha20-Poly1305). Renvoie un jeton base64 autonome.
    """
    recipient = X25519PublicKey.from_public_bytes(base64.b64decode(recipient_public_b64))
    ephemeral = X25519PrivateKey.generate()
    key = _derive_shared(ephemeral.exchange(recipient))
    nonce = os.urandom(12)
    ciphertext = ChaCha20Poly1305(key).encrypt(nonce, plaintext, b"")
    ephemeral_public = ephemeral.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    token = {
        "e": base64.b64encode(ephemeral_public).decode("ascii"),
        "n": base64.b64encode(nonce).decode("ascii"),
        "c": base64.b64encode(ciphertext).decode("ascii"),
    }
    return base64.b64encode(
        json.dumps(token, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")


def unseal(private_b64: str, token_b64: str) -> bytes | None:
    """Ouvre un jeton scellé. Renvoie ``None`` si le sceau est invalide."""
    try:
        token = json.loads(base64.b64decode(token_b64))
        ephemeral = X25519PublicKey.from_public_bytes(base64.b64decode(token["e"]))
        private = X25519PrivateKey.from_private_bytes(base64.b64decode(private_b64))
        key = _derive_shared(private.exchange(ephemeral))
        nonce = base64.b64decode(token["n"])
        ciphertext = base64.b64decode(token["c"])
        return ChaCha20Poly1305(key).decrypt(nonce, ciphertext, b"")
    except (InvalidTag, KeyError, ValueError, TypeError):
        return None


__all__ = [
    "SIGNED_FIELDS",
    "canonical_bytes",
    "fingerprint",
    "generate_enc_keys",
    "peer_id_for",
    "public_key_from_base64",
    "private_key_from_base64",
    "seal",
    "sign",
    "unseal",
    "verify",
]
