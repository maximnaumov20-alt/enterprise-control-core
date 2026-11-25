import base64
import secrets

import pytest

from app.mfa_crypto import (
    MFADecryptionError,
    MFAEncryptionConfigurationError,
    MFAEncryptionKeyring,
    decrypt_totp_secret,
    encrypt_totp_secret,
)


TOTP_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


def encoded_key():
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")


def keyring(active=None, previous=()):
    active = active or encoded_key()
    return MFAEncryptionKeyring(
        active_key=base64.urlsafe_b64decode(active + "=" * (-len(active) % 4)),
        previous_keys=tuple(
            base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
            for value in previous
        ),
    )


def test_encrypts_and_decrypts_secret_for_same_identity():
    keys = keyring()
    encrypted = encrypt_totp_secret(
        TOTP_SECRET, organization_id=7, user_id=42, keyring=keys
    )
    assert TOTP_SECRET not in encrypted
    assert encrypted.startswith("v1.")
    assert decrypt_totp_secret(
        encrypted, organization_id=7, user_id=42, keyring=keys
    ) == TOTP_SECRET


@pytest.mark.parametrize(
    ("organization_id", "user_id"),
    [(8, 42), (7, 43)],
)
def test_ciphertext_is_bound_to_organization_and_user(organization_id, user_id):
    keys = keyring()
    encrypted = encrypt_totp_secret(
        TOTP_SECRET, organization_id=7, user_id=42, keyring=keys
    )
    with pytest.raises(MFADecryptionError):
        decrypt_totp_secret(
            encrypted,
            organization_id=organization_id,
            user_id=user_id,
            keyring=keys,
        )


def test_ciphertext_tampering_is_rejected():
    keys = keyring()
    encrypted = encrypt_totp_secret(
        TOTP_SECRET, organization_id=7, user_id=42, keyring=keys
    )
    parts = encrypted.split(".")
    payload = parts[2]
    replacement = "A" if payload[0] != "A" else "B"
    parts[2] = replacement + payload[1:]
    with pytest.raises(MFADecryptionError):
        decrypt_totp_secret(
            ".".join(parts), organization_id=7, user_id=42, keyring=keys
        )


def test_key_rotation_decrypts_with_previous_key_and_encrypts_with_active_key():
    old_key = encoded_key()
    old_keyring = keyring(active=old_key)
    encrypted = encrypt_totp_secret(
        TOTP_SECRET, organization_id=7, user_id=42, keyring=old_keyring
    )

    new_key = encoded_key()
    rotated = keyring(active=new_key, previous=(old_key,))
    assert decrypt_totp_secret(
        encrypted, organization_id=7, user_id=42, keyring=rotated
    ) == TOTP_SECRET

    reencrypted = encrypt_totp_secret(
        TOTP_SECRET, organization_id=7, user_id=42, keyring=rotated
    )
    assert reencrypted.split(".")[1] != encrypted.split(".")[1]
    assert decrypt_totp_secret(
        reencrypted, organization_id=7, user_id=42, keyring=rotated
    ) == TOTP_SECRET


def test_missing_or_invalid_environment_key_fails_closed():
    with pytest.raises(MFAEncryptionConfigurationError):
        MFAEncryptionKeyring.from_environment({})
    with pytest.raises(MFAEncryptionConfigurationError):
        MFAEncryptionKeyring.from_environment(
            {"ECC_MFA_ENCRYPTION_KEY": "not-a-key"}
        )


def test_environment_keyring_supports_previous_keys():
    active = encoded_key()
    previous = encoded_key()
    keys = MFAEncryptionKeyring.from_environment(
        {
            "ECC_MFA_ENCRYPTION_KEY": active,
            "ECC_MFA_ENCRYPTION_KEY_PREVIOUS": previous,
        }
    )
    assert len(keys.previous_keys) == 1


@pytest.mark.parametrize(
    ("organization_id", "user_id"),
    [(0, 42), (7, 0), (True, 42), (7, False)],
)
def test_identity_ids_must_be_positive_integers(organization_id, user_id):
    keys = keyring()
    with pytest.raises(ValueError):
        encrypt_totp_secret(
            TOTP_SECRET,
            organization_id=organization_id,
            user_id=user_id,
            keyring=keys,
        )


def test_invalid_totp_secret_is_not_encrypted():
    keys = keyring()
    with pytest.raises(ValueError):
        encrypt_totp_secret(
            "not-a-valid-secret", organization_id=7, user_id=42, keyring=keys
        )
