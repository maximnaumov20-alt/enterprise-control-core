"""Authenticated encryption for per-user TOTP secrets.

Key material is supplied through environment configuration and is never logged.
The envelope is versioned and bound to the organization/user identity with AEAD AAD.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import os
import secrets
from dataclasses import dataclass
from typing import Mapping

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .mfa import _decode_secret


_ENVELOPE_VERSION = "v1"
_NONCE_BYTES = 12
_KEY_BYTES = 32
_ACTIVE_KEY_ENV = "ECC_MFA_ENCRYPTION_KEY"
_PREVIOUS_KEYS_ENV = "ECC_MFA_ENCRYPTION_KEY_PREVIOUS"


class MFAEncryptionConfigurationError(RuntimeError):
    """Raised when MFA encryption cannot be configured safely."""


class MFADecryptionError(ValueError):
    """Raised when an encrypted MFA secret cannot be authenticated or decoded."""


def _decode_key(encoded: str) -> bytes:
    if not isinstance(encoded, str) or not encoded:
        raise MFAEncryptionConfigurationError("MFA encryption key is missing")
    value = encoded.strip()
    if not value or any(char.isspace() for char in value):
        raise MFAEncryptionConfigurationError("MFA encryption key must be unpadded base64url")
    try:
        raw = base64.b64decode(
            value + "=" * (-len(value) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, binascii.Error) as exc:
        raise MFAEncryptionConfigurationError("MFA encryption key must be valid base64url") from exc
    if len(raw) != _KEY_BYTES or base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=") != value:
        raise MFAEncryptionConfigurationError("MFA encryption key must encode exactly 32 bytes")
    return raw


def _encode_bytes(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode_bytes(value: str) -> bytes:
    if not isinstance(value, str) or not value or any(char.isspace() for char in value):
        raise MFADecryptionError("Encrypted MFA secret is malformed")
    try:
        decoded = base64.b64decode(
            value + "=" * (-len(value) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, binascii.Error) as exc:
        raise MFADecryptionError("Encrypted MFA secret is malformed") from exc
    if _encode_bytes(decoded) != value:
        raise MFADecryptionError("Encrypted MFA secret is malformed")
    return decoded


def _key_id(key: bytes) -> str:
    return hashlib.sha256(key).hexdigest()[:16]


def _identity_aad(organization_id: int, user_id: int) -> bytes:
    for name, value in (("organization_id", organization_id), ("user_id", user_id)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{name} must be a positive integer")
    return f"enterprise-control-core:mfa:totp:v1:org:{organization_id}:user:{user_id}".encode()


@dataclass(frozen=True, repr=False)
class MFAEncryptionKeyring:
    """Active encryption key plus optional previous keys for controlled rotation."""

    active_key: bytes
    previous_keys: tuple[bytes, ...] = ()

    def __post_init__(self) -> None:
        all_keys = (self.active_key, *self.previous_keys)
        if any(not isinstance(key, bytes) or len(key) != _KEY_BYTES for key in all_keys):
            raise MFAEncryptionConfigurationError("MFA encryption keys must each contain 32 bytes")
        ids = [_key_id(key) for key in all_keys]
        if len(ids) != len(set(ids)):
            raise MFAEncryptionConfigurationError("MFA encryption keyring contains duplicate keys")

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> "MFAEncryptionKeyring":
        source = os.environ if environ is None else environ
        active = _decode_key(source.get(_ACTIVE_KEY_ENV, ""))
        previous_value = source.get(_PREVIOUS_KEYS_ENV, "")
        previous = tuple(
            _decode_key(item.strip())
            for item in previous_value.split(",")
            if item.strip()
        )
        return cls(active_key=active, previous_keys=previous)

    def _key_for_id(self, key_id: str) -> bytes:
        for key in (self.active_key, *self.previous_keys):
            if secrets.compare_digest(_key_id(key), key_id):
                return key
        raise MFADecryptionError("Encrypted MFA secret uses an unavailable key")


def encrypt_totp_secret(
    secret: str,
    *,
    organization_id: int,
    user_id: int,
    keyring: MFAEncryptionKeyring,
) -> str:
    """Encrypt a validated Base32 TOTP secret for one organization/user."""
    normalized_secret = base64.b32encode(_decode_secret(secret)).decode("ascii").rstrip("=")
    aad = _identity_aad(organization_id, user_id)
    nonce = secrets.token_bytes(_NONCE_BYTES)
    ciphertext = AESGCM(keyring.active_key).encrypt(
        nonce, normalized_secret.encode("ascii"), aad
    )
    return f"{_ENVELOPE_VERSION}.{_key_id(keyring.active_key)}.{_encode_bytes(nonce + ciphertext)}"


def decrypt_totp_secret(
    envelope: str,
    *,
    organization_id: int,
    user_id: int,
    keyring: MFAEncryptionKeyring,
) -> str:
    """Authenticate and decrypt a TOTP secret for its original organization/user."""
    if not isinstance(envelope, str):
        raise MFADecryptionError("Encrypted MFA secret is malformed")
    parts = envelope.split(".")
    if len(parts) != 3 or parts[0] != _ENVELOPE_VERSION or len(parts[1]) != 16:
        raise MFADecryptionError("Encrypted MFA secret is malformed")
    aad = _identity_aad(organization_id, user_id)
    packed = _decode_bytes(parts[2])
    if len(packed) < _NONCE_BYTES + 16:
        raise MFADecryptionError("Encrypted MFA secret is malformed")
    nonce, ciphertext = packed[:_NONCE_BYTES], packed[_NONCE_BYTES:]
    key = keyring._key_for_id(parts[1])
    try:
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, aad)
        secret = plaintext.decode("ascii")
        _decode_secret(secret)
        return secret
    except (InvalidTag, UnicodeDecodeError, ValueError) as exc:
        raise MFADecryptionError("Encrypted MFA secret could not be authenticated") from exc
