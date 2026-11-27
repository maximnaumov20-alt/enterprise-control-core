# MFA TOTP secret encryption key management

The MFA secret-storage module uses AES-256-GCM. It does not fall back to the JWT signing key or to an implicit development key.

## Required configuration

- `ECC_MFA_ENCRYPTION_KEY`: active key, exactly 32 random bytes encoded as unpadded base64url.
- `ECC_MFA_ENCRYPTION_KEY_PREVIOUS`: optional comma-separated list of previous keys in the same format. These keys are accepted for decryption only; new ciphertext is always written with the active key.

Generate a key with Python:

```bash
python -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode('ascii').rstrip('='))"
```

Generate keys in a trusted environment and store them in the deployment secret manager. Do not commit them to the repository, put them in container images, paste them into issue comments, or write them to application logs.

The application must fail closed when the active key is missing, malformed, or not exactly 32 bytes. Do not reuse `ECC_SECRET_KEY`, database credentials, or a human password.

## Ciphertext properties

- AES-256-GCM with a fresh 96-bit nonce for every encryption.
- Versioned envelope: `v1.<key-id>.<base64url(nonce+ciphertext+tag)>`.
- Associated authenticated data binds each secret to its organization ID and user ID.
- Base32 TOTP values are validated and canonicalized before encryption.
- Authentication failures and identity mismatches are rejected; callers must not fall back to plaintext.

## Rotation procedure

1. Generate a new random key and deploy it as `ECC_MFA_ENCRYPTION_KEY`.
2. Move the old active key into `ECC_MFA_ENCRYPTION_KEY_PREVIOUS`.
3. Keep previous keys available until every ciphertext encrypted with them has been re-encrypted and verified.
4. Remove a previous key only after a controlled migration confirms no stored record still references its key ID and backups requiring that key have passed their retention window.

The current module supports reading ciphertext with configured previous keys and writing with the active key. It does **not** yet perform a database-wide re-encryption job or prove backup-retention completion. Do not remove an old key based only on elapsed time.

## Operational restrictions

- Never log plaintext secrets, provisioning URIs, ciphertext payloads, TOTP codes, recovery codes, or encryption key material.
- Restrict secret-manager access to the deployment identity and authorized operators.
- Back up encryption keys separately from database backups. A database backup without its required historical encryption keys cannot restore MFA credentials.
- Test restore and key-rotation procedures before enabling MFA for production accounts.
- Do not describe MFA as production-ready until enrollment, login challenge, replay prevention, recovery codes, throttling, session invalidation, full restore tests, and independent security review are complete.
