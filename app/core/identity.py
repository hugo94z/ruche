"""Identité locale : paire de clés persistante + pseudo.

Il n'y a aucun compte ni serveur d'authentification : l'identité d'un pair
est une clé publique, et son identifiant en est dérivé. Le pseudo est libre.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, asdict

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from .. import config
from . import crypto


@dataclass
class Identity:
    peer_id: str
    pseudo: str
    public_key: str
    private_key: str
    # Clés X25519 de la boîte aux lettres (scellement des messages différés).
    enc_public: str = ""
    enc_private: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _derive_peer_id(public_key_bytes: bytes) -> str:
    return hashlib.sha256(public_key_bytes).hexdigest()[:16]


def _new_keypair() -> tuple[str, str]:
    private_key = Ed25519PrivateKey.generate()
    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return (
        base64.b64encode(public_bytes).decode("ascii"),
        base64.b64encode(private_bytes).decode("ascii"),
    )


def create(pseudo: str) -> Identity:
    """Crée une identité neuve avec de vraies clés (utilisé par les tests)."""
    public_b64, private_b64 = _new_keypair()
    enc_private, enc_public = crypto.generate_enc_keys()
    return Identity(
        peer_id=_derive_peer_id(base64.b64decode(public_b64)),
        pseudo=pseudo,
        public_key=public_b64,
        private_key=private_b64,
        enc_public=enc_public,
        enc_private=enc_private,
    )


def load_or_create(pseudo: str = "") -> Identity:
    """Charge l'identité existante ou en crée une nouvelle."""
    path = config.identity_path()
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            ident = Identity(
                peer_id=raw["peer_id"],
                pseudo=pseudo or raw.get("pseudo", ""),
                public_key=raw["public_key"],
                private_key=raw["private_key"],
                enc_public=raw.get("enc_public", ""),
                enc_private=raw.get("enc_private", ""),
            )
            changed = bool(pseudo and pseudo != raw.get("pseudo", ""))
            if not ident.enc_public or not ident.enc_private:
                # Identité antérieure à la boîte aux lettres : on complète.
                ident.enc_private, ident.enc_public = crypto.generate_enc_keys()
                changed = True
            if changed:
                save(ident)
            return ident
        except (KeyError, ValueError, OSError):
            pass  # fichier corrompu : on en régénère un

    ident = create(pseudo)
    save(ident)
    return ident


def save(ident: Identity) -> None:
    config.identity_path().write_text(
        json.dumps(ident.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def short_id(peer_id: str) -> str:
    return peer_id[:8]
