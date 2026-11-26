import base64

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.mfa_crypto import MFAEncryptionConfigurationError


def _encoded_key(first_byte: int = 0) -> str:
    return base64.urlsafe_b64encode(bytes([first_byte]) + bytes(31)).decode("ascii").rstrip("=")


def test_application_startup_rejects_missing_mfa_encryption_key(monkeypatch):
    monkeypatch.delenv("ECC_MFA_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("ECC_MFA_ENCRYPTION_KEY_PREVIOUS", raising=False)
    with pytest.raises(MFAEncryptionConfigurationError):
        with TestClient(app):
            pass


def test_application_startup_rejects_invalid_previous_mfa_key(monkeypatch):
    monkeypatch.setenv("ECC_MFA_ENCRYPTION_KEY", _encoded_key())
    monkeypatch.setenv("ECC_MFA_ENCRYPTION_KEY_PREVIOUS", "not-a-valid-key")
    with pytest.raises(MFAEncryptionConfigurationError):
        with TestClient(app):
            pass


def test_application_startup_accepts_valid_active_and_previous_keys(monkeypatch):
    monkeypatch.setenv("ECC_MFA_ENCRYPTION_KEY", _encoded_key())
    monkeypatch.setenv("ECC_MFA_ENCRYPTION_KEY_PREVIOUS", _encoded_key(1))
    with TestClient(app):
        pass
