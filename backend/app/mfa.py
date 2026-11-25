"""RFC 6238 TOTP primitives.

This module intentionally contains no persistence or authentication policy. Callers
must atomically persist and compare the returned time-step to prevent replay.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import math
import secrets
import struct
import time
from urllib.parse import quote, urlencode


DEFAULT_PERIOD_SECONDS = 30
DEFAULT_DIGITS = 6
DEFAULT_SECRET_BYTES = 20
MAX_SECRET_BYTES = 64
MAX_COUNTER = (1 << 64) - 1


def _validate_parameters(period: int, digits: int) -> None:
    if isinstance(period, bool) or not isinstance(period, int) or period < 1:
        raise ValueError("TOTP period must be a positive integer")
    if isinstance(digits, bool) or digits not in (6, 8):
        raise ValueError("TOTP digits must be 6 or 8")


def _counter_at(timestamp: int | float, period: int) -> int:
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
        raise ValueError("TOTP timestamp must be a finite number")
    try:
        if not math.isfinite(timestamp) or timestamp < 0:
            raise ValueError("TOTP timestamp must be finite and non-negative")
        counter = int(timestamp // period)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError("TOTP timestamp is outside the supported range") from exc
    if counter > MAX_COUNTER:
        raise ValueError("TOTP timestamp is outside the supported range")
    return counter


def generate_totp_secret(entropy_bytes: int = DEFAULT_SECRET_BYTES) -> str:
    """Return a Base32 secret with 128–512 bits of cryptographic entropy."""
    if (
        isinstance(entropy_bytes, bool)
        or not isinstance(entropy_bytes, int)
        or not 16 <= entropy_bytes <= MAX_SECRET_BYTES
    ):
        raise ValueError("TOTP secret entropy must be between 128 and 512 bits")
    return base64.b32encode(secrets.token_bytes(entropy_bytes)).decode("ascii").rstrip("=")


def _decode_secret(secret: str) -> bytes:
    if not isinstance(secret, str) or not secret:
        raise ValueError("TOTP secret must be a non-empty Base32 string")
    normalized = "".join(secret.split()).upper()
    try:
        key = base64.b32decode(
            normalized + "=" * (-len(normalized) % 8),
            casefold=True,
        )
    except (ValueError, binascii.Error) as exc:
        raise ValueError("TOTP secret is not valid Base32") from exc
    if len(key) < 16:
        raise ValueError("TOTP secret must contain at least 128 bits of entropy")
    return key


def _hotp_code(key: bytes, counter: int, digits: int) -> str:
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(binary % (10**digits)).zfill(digits)


def totp_code(
    secret: str,
    timestamp: int | float | None = None,
    *,
    period: int = DEFAULT_PERIOD_SECONDS,
    digits: int = DEFAULT_DIGITS,
) -> str:
    """Calculate an RFC 6238 code using HMAC-SHA1 (authenticator-app default)."""
    _validate_parameters(period, digits)
    instant = time.time() if timestamp is None else timestamp
    counter = _counter_at(instant, period)
    return _hotp_code(_decode_secret(secret), counter, digits)


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
    if (
        isinstance(valid_window, bool)
        or not isinstance(valid_window, int)
        or not 0 <= valid_window <= 2
    ):
        raise ValueError("TOTP valid_window must be an integer between 0 and 2")
    try:
        _validate_parameters(period, digits)
        instant = time.time() if timestamp is None else timestamp
        current_counter = _counter_at(instant, period)
        key = _decode_secret(secret)
    except ValueError:
        return None

    if (
        not isinstance(code, str)
        or len(code) != digits
        or not code.isascii()
        or not code.isdigit()
    ):
        return None

    counters = range(
        max(0, current_counter - valid_window),
        min(MAX_COUNTER, current_counter + valid_window) + 1,
    )
    for counter in sorted(counters, key=lambda value: (abs(value - current_counter), value)):
        if hmac.compare_digest(_hotp_code(key, counter, digits), code):
            return counter
    return None


def provisioning_uri(secret: str, account_name: str, issuer: str) -> str:
    """Build an otpauth URI; callers must reveal it only during enrollment."""
    _decode_secret(secret)
    normalized_secret = "".join(secret.split()).upper()
    account = account_name.strip()
    provider = issuer.strip()
    if not account or not provider:
        raise ValueError("Account name and issuer are required")
    label = quote(f"{provider}:{account}", safe="")
    query = urlencode(
        {
            "secret": normalized_secret,
            "issuer": provider,
            "algorithm": "SHA1",
            "digits": DEFAULT_DIGITS,
            "period": DEFAULT_PERIOD_SECONDS,
        }
    )
    return f"otpauth://totp/{label}?{query}"
