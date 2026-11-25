import pytest

from app.mfa import generate_totp_secret, provisioning_uri, totp_code, verify_totp


# RFC 6238 Appendix B, SHA-1 test secret and published timestamps.
RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


@pytest.mark.parametrize(
    ("timestamp", "expected"),
    [
        (59, "94287082"),
        (1111111109, "07081804"),
        (1111111111, "14050471"),
        (1234567890, "89005924"),
        (2000000000, "69279037"),
        (20000000000, "65353130"),
    ],
)
def test_rfc_6238_sha1_known_answers(timestamp, expected):
    assert totp_code(RFC_SECRET, timestamp, digits=8) == expected


def test_six_digit_code_verifies_and_returns_counter():
    code = totp_code(RFC_SECRET, 59)
    assert len(code) == 6
    assert verify_totp(RFC_SECRET, code, 59, valid_window=0) == 1


def test_verification_accepts_one_step_clock_drift():
    previous = totp_code(RFC_SECRET, 30)
    following = totp_code(RFC_SECRET, 90)
    assert verify_totp(RFC_SECRET, previous, 60, valid_window=1) == 1
    assert verify_totp(RFC_SECRET, following, 60, valid_window=1) == 3
    assert verify_totp(RFC_SECRET, previous, 60, valid_window=0) is None


@pytest.mark.parametrize("code", ["12345", "12a456", "１２３４５６", None, 123456])
def test_invalid_codes_are_rejected(code):
    assert verify_totp(RFC_SECRET, code, 59) is None


def test_malformed_or_undersized_secrets_fail_closed_during_verification():
    assert verify_totp("not-a-secret", "123456", 59) is None
    assert verify_totp("MY", "123456", 59) is None


def test_secret_generation_has_sufficient_entropy_and_valid_encoding():
    secret = generate_totp_secret()
    assert len(secret) >= 32
    assert totp_code(secret, 0).isdigit()
    with pytest.raises(ValueError):
        generate_totp_secret(10)
    with pytest.raises(ValueError):
        generate_totp_secret(65)
    with pytest.raises(ValueError):
        generate_totp_secret(True)


def test_provisioning_uri_encodes_label_and_sets_standard_parameters():
    uri = provisioning_uri(RFC_SECRET, "user@example.com", "ECC Example")
    assert uri.startswith("otpauth://totp/ECC%20Example%3Auser%40example.com?")
    assert "algorithm=SHA1" in uri
    assert "digits=6" in uri
    assert "period=30" in uri
    assert "secret=" in uri


def test_provisioning_uri_normalizes_base32_whitespace():
    uri = provisioning_uri("GEZDGNBV GY3TQOJQGEZDGNBVGY3TQOJQ", "user@example.com", "ECC")
    assert "secret=GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ" in uri


def test_invalid_secret_and_parameters_fail_closed():
    with pytest.raises(ValueError):
        totp_code("not-a-secret", 59)
    with pytest.raises(ValueError):
        totp_code(RFC_SECRET, 59, period=0)
    with pytest.raises(ValueError):
        totp_code(RFC_SECRET, 59, period=True)
    with pytest.raises(ValueError):
        totp_code(RFC_SECRET, 59, timestamp=float("inf"))
    with pytest.raises(ValueError):
        totp_code(RFC_SECRET, 59, timestamp=float("nan"))
    with pytest.raises(ValueError):
        totp_code(RFC_SECRET, 59, timestamp=-1)
    with pytest.raises(ValueError):
        verify_totp(RFC_SECRET, "123456", 59, valid_window=3)
    with pytest.raises(ValueError):
        verify_totp(RFC_SECRET, "123456", 59, valid_window=True)
