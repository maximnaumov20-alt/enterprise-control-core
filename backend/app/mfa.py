"""RFC 6238 TOTP primitives.

This module intentionally contains no persistence or authentication policy. Callers
must atomically persist and compare the returned time-step to prevent replay.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode


DEFAULT_PERIOD_SECONDS = 30
DEFAULT_DIGITS = 6
DEFAULT_SECRET_BYTES = 20


def generate_totp_secret(entropy_bytes: int = DEFAULT_SECRET_BYTES) -> str:
    """Return a Base32 secret suitable for provisioning an authenticator app."""
    if entropy_bytes < 16:
        raise ValueError("TOTP secrets must contain at least 128 bits of entropy")
    return base64.b32encode(secrets.token_bytes(entropy_bytes)).decode("ascii").rstrip("=")


def _decode_secret(secret: str) -> bytes:
    if not isinstance(secret, str) or not secret:
        raise ValueError("TOTP secret must be a non-empty Base32 string")
    normalized = "".join(secret.split()).upper()
    try:
        return base64.b32decode(normalized + "=" * (-len(normalized) % 8), casefold=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError("TOTP secret is not valid Base32") from exc


def totp_code(
    secret: str,
    timestamp: int | float | None = None,
    *,
    period: int = DEFAULT_PERIOD_SECONDS,
    digits: int = DEFAULT_DIGITS,
) -> str:
    """Calculate an RFC 6238 code using HMAC-SHA1 (authenticator-app default)."""
    if period < 1:
        raise ValueError("TOTP period must be positive")
    if digits not in (6, 8):
        raise ValueError("TOTP digits must be 6 or 8")
    instant = time.time() if timestamp is None else timestamp
    if instant < 0:
        raise ValueError("TOTP timestamp cannot be negative")
    counter = int(instant // period)
    key = _decode_secret(secret)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(binary % (10**digits)).zfill(digits)


def verify_totp(
    secret: str,
    code: str,
    timestamp: int | float | None = None,
    *,
    period: int = DEFAULT_PERIOD_SECONDS,
    digits: int = DEFAULT_DIGITS,
    valid_window: int = 1,
) -> int | None:
    """Return the matched counter, or None; persistence/replay checks belong to caller."""
    if valid_window < 0 or valid_window > 2:
        raise ValueError("TOTP valid_window must be between 0 and 2")
    if not isinstance(code, str) or len(code) != digits or not code.isascii() or not code.isdigit():
        return None
    instant = time.time() if timestamp is None else timestamp
    if instant < 0 or period < 1 or digits not in (6, 8):
        return None
    current_counter = int(instant // period)
    key = _decode_secret(secret)
    for counter in sorted(
        range(max(0, current_counter - valid_window), current_counter + valid_window + 1),
        key=lambda value: (abs(value - current_counter), value),
    ):
        digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
        offset = digest[-1] & 0x0F
        binary = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
        expected = str(binary % (10**digits)).zfill(digits)
        if hmac.compare_digest(expected, code):
            return counter
    return None


def provisioning_uri(secret: str, account_name: str, issuer: str) -> str:
    """Build an otpauth URI; callers must reveal it only during enrollment."""
    _decode_secret(secret)
    account = account_name.strip()
    provider = issuer.strip()
    if not account or not provider:
        raise ValueError("Account name and issuer are required")
    label = quote(f"{provider}:{account}", safe="")
    query = urlencode(
        {
            "secret": secret.replace(" ", "").upper(),
            "issuer": provider,
            "algorithm": "SHA1",
            "digits": DEFAULT_DIGITS,
            "period": DEFAULT_PERIOD_SECONDS,
        }
    )
    return f"otpauth://totp/{label}?{query}"
