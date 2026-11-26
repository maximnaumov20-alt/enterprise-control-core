"""MFA enrollment, challenge verification, replay protection and recovery."""
from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import or_, select, update

from app.main import (
    ACCESS_TOKEN_MINUTES, AuthSession, CurrentUser, LoginRateLimiter, SessionDep,
    User, create_access_token, login_rate_limiter, password_hash, record_audit,
    redis_client,
)
from app.mfa import provisioning_uri, verify_totp, generate_totp_secret
from app.mfa_crypto import MFAEncryptionKeyring, decrypt_totp_secret, encrypt_totp_secret
from app.mfa_models import MFAChallenge, MFAFactor, MFARecoveryCode

router = APIRouter(prefix="/api/v1/auth/mfa", tags=["authentication", "mfa"])
CHALLENGE_SECONDS = 300
PENDING_SECONDS = 600
MAX_CHALLENGE_ATTEMPTS = 5
RECOVERY_CODE_COUNT = 10
MFA_RATE_LIMIT_MAX = int(os.getenv("MFA_RATE_LIMIT_MAX", "5"))
MFA_RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("MFA_RATE_LIMIT_WINDOW_SECONDS", "300"))
if MFA_RATE_LIMIT_MAX < 1 or MFA_RATE_LIMIT_WINDOW_SECONDS < 1:
    raise RuntimeError("MFA rate-limit settings must be positive integers.")
mfa_rate_limiter = LoginRateLimiter(MFA_RATE_LIMIT_MAX, MFA_RATE_LIMIT_WINDOW_SECONDS, redis_client)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _keyring() -> MFAEncryptionKeyring:
    try:
        return MFAEncryptionKeyring.from_environment(os.environ)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="MFA encryption is not configured") from exc


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _issue_challenge(session, user: User) -> tuple[str, int]:
    factor = session.scalar(select(MFAFactor).where(
        MFAFactor.organization_id == user.organization_id,
        MFAFactor.user_id == user.id,
        MFAFactor.status == "enabled",
    ))
    if factor is None:
        raise RuntimeError("MFA challenge requested without an enabled factor")
    raw = secrets.token_urlsafe(32)
    now = _now()
    session.add(MFAChallenge(
        id=str(uuid4()), organization_id=user.organization_id, user_id=user.id,
        token_hash=_digest(raw), attempts=0, created_at=now,
        expires_at=now + timedelta(seconds=CHALLENGE_SECONDS),
    ))
    session.commit()
    return raw, CHALLENGE_SECONDS


def issue_mfa_challenge(session, user: User) -> tuple[str, int] | None:
    enabled = session.scalar(select(MFAFactor.id).where(
        MFAFactor.organization_id == user.organization_id,
        MFAFactor.user_id == user.id,
        MFAFactor.status == "enabled",
    ))
    return _issue_challenge(session, user) if enabled else None


class EnrollmentConfirm(BaseModel):
    code: str = Field(min_length=6, max_length=6, pattern=r"^[0-9]{6}$")


class ChallengeVerify(BaseModel):
    challenge_token: str = Field(min_length=32, max_length=128)
    code: str = Field(min_length=6, max_length=32)


class MFADisable(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    code: str = Field(min_length=6, max_length=32)


def _enforce_mfa_rate_limit(key: str) -> None:
    try:
        retry_after = mfa_rate_limiter.check(key)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Authentication temporarily unavailable") from exc
    if retry_after is not None:
        raise HTTPException(
            status_code=429, detail="Too many MFA attempts",
            headers={"Retry-After": str(retry_after)},
        )


def _factor_for_user(session, user: User) -> MFAFactor | None:
    return session.scalar(select(MFAFactor).where(
        MFAFactor.organization_id == user.organization_id,
        MFAFactor.user_id == user.id,
    ))


def _normalize_recovery_code(value: str) -> str:
    return value.replace("-", "").replace(" ", "").upper()


def _consume_recovery_code(session, user: User, submitted: str) -> bool:
    normalized = _normalize_recovery_code(submitted)
    if not normalized.isalnum() or not 8 <= len(normalized) <= 32:
        return False
    rows = session.scalars(select(MFARecoveryCode).where(
        MFARecoveryCode.organization_id == user.organization_id,
        MFARecoveryCode.user_id == user.id,
        MFARecoveryCode.used_at.is_(None),
    ).limit(RECOVERY_CODE_COUNT)).all()
    for row in rows:
        if password_hash.verify(normalized, row.code_hash):
            result = session.execute(update(MFARecoveryCode).where(
                MFARecoveryCode.id == row.id,
                MFARecoveryCode.organization_id == user.organization_id,
                MFARecoveryCode.user_id == user.id,
                MFARecoveryCode.used_at.is_(None),
            ).values(used_at=_now()))
            return result.rowcount == 1
    return False


@router.post("/enrollment")
def begin_enrollment(session: SessionDep, user: CurrentUser):
    factor = _factor_for_user(session, user)
    now = _now()
    if factor is not None and factor.status == "enabled":
        raise HTTPException(status_code=409, detail="MFA is already enabled")
    if (
        factor is not None and factor.status == "pending"
        and factor.pending_expires_at is not None
        and (factor.pending_expires_at.replace(tzinfo=timezone.utc)
             if factor.pending_expires_at.tzinfo is None else factor.pending_expires_at) > now
    ):
        secret = decrypt_totp_secret(
            factor.encrypted_secret, organization_id=user.organization_id,
            user_id=user.id, keyring=_keyring(),
        )
        expires_at = factor.pending_expires_at
    else:
        secret = generate_totp_secret()
        encrypted = encrypt_totp_secret(
            secret, organization_id=user.organization_id, user_id=user.id, keyring=_keyring()
        )
        expires_at = now + timedelta(seconds=PENDING_SECONDS)
        if factor is None:
            factor = MFAFactor(
                id=str(uuid4()), organization_id=user.organization_id, user_id=user.id,
                encrypted_secret=encrypted, status="pending", pending_expires_at=expires_at,
                enabled_at=None, last_used_counter=None, created_at=now, updated_at=now,
            )
            session.add(factor)
        else:
            factor.encrypted_secret = encrypted
            factor.status = "pending"
            factor.pending_expires_at = expires_at
            factor.enabled_at = None
            factor.last_used_counter = None
            factor.updated_at = now
        session.commit()
    record_audit(session, user, "mfa.enrollment_started", "mfa_factor", factor.id)
    session.commit()
    return {
        "status": "pending",
        "secret": secret,
        "provisioning_uri": provisioning_uri(secret, user.email, "Enterprise Control Center"),
        "expires_at": expires_at,
    }


@router.post("/enrollment/confirm")
def confirm_enrollment(
    payload: EnrollmentConfirm, request: Request, session: SessionDep, user: CurrentUser
):
    client_ip = request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")
    _enforce_mfa_rate_limit(f"ecc:auth:mfa:enroll:{user.id}:{client_ip}")
    factor = _factor_for_user(session, user)
    now = _now()
    if factor is None or factor.status != "pending" or factor.pending_expires_at is None:
        raise HTTPException(status_code=409, detail="No pending MFA enrollment")
    pending_expiry = factor.pending_expires_at
    if pending_expiry.tzinfo is None:
        pending_expiry = pending_expiry.replace(tzinfo=timezone.utc)
    if pending_expiry <= now:
        raise HTTPException(status_code=410, detail="MFA enrollment has expired; start again")
    secret = decrypt_totp_secret(
        factor.encrypted_secret, organization_id=user.organization_id,
        user_id=user.id, keyring=_keyring(),
    )
    counter = verify_totp(secret, payload.code, now.timestamp(), valid_window=1)
    if counter is None:
        raise HTTPException(status_code=400, detail="Invalid authenticator code")
    result = session.execute(update(MFAFactor).where(
        MFAFactor.id == factor.id,
        MFAFactor.organization_id == user.organization_id,
        MFAFactor.user_id == user.id,
        MFAFactor.status == "pending",
        MFAFactor.pending_expires_at > now,
        or_(MFAFactor.last_used_counter.is_(None), MFAFactor.last_used_counter < counter),
    ).values(
        status="enabled", enabled_at=now, pending_expires_at=None,
        last_used_counter=counter, updated_at=now,
    ))
    if result.rowcount != 1:
        session.rollback()
        raise HTTPException(status_code=409, detail="Enrollment changed; start again")
    session.execute(
        MFARecoveryCode.__table__.delete().where(
            MFARecoveryCode.organization_id == user.organization_id,
            MFARecoveryCode.user_id == user.id,
        )
    )
    codes = [secrets.token_hex(5).upper() for _ in range(RECOVERY_CODE_COUNT)]
    for code in codes:
        session.add(MFARecoveryCode(
            id=str(uuid4()), organization_id=user.organization_id, user_id=user.id,
            code_hash=password_hash.hash(code), created_at=now, used_at=None,
        ))
    record_audit(session, user, "mfa.enabled", "mfa_factor", factor.id)
    session.commit()
    return {"status": "enabled", "recovery_codes": codes}


@router.post("/challenge/verify")
def verify_challenge(payload: ChallengeVerify, request: Request, session: SessionDep):
    challenge = session.scalar(select(MFAChallenge).where(
        MFAChallenge.token_hash == _digest(payload.challenge_token)
    ))
    now = _now()
    if challenge is None or challenge.consumed_at is not None:
        raise HTTPException(status_code=401, detail="Invalid or expired MFA challenge")
    expiry = challenge.expires_at
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    if expiry <= now or challenge.attempts >= MAX_CHALLENGE_ATTEMPTS:
        raise HTTPException(status_code=401, detail="Invalid or expired MFA challenge")
    user = session.scalar(select(User).where(
        User.id == challenge.user_id,
        User.organization_id == challenge.organization_id,
        User.is_active.is_(True),
    ))
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid or expired MFA challenge")
    client_ip = request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")
    _enforce_mfa_rate_limit(f"ecc:auth:mfa:challenge:{user.id}:{client_ip}")

    factor = _factor_for_user(session, user)
    accepted = False
    if factor is not None and factor.status == "enabled" and payload.code.isascii() and payload.code.isdigit():
        secret = decrypt_totp_secret(
            factor.encrypted_secret, organization_id=user.organization_id,
            user_id=user.id, keyring=_keyring(),
        )
        counter = verify_totp(secret, payload.code, now.timestamp(), valid_window=1)
        if counter is not None:
            updated = session.execute(update(MFAFactor).where(
                MFAFactor.id == factor.id,
                MFAFactor.organization_id == user.organization_id,
                MFAFactor.user_id == user.id,
                MFAFactor.status == "enabled",
                or_(MFAFactor.last_used_counter.is_(None), MFAFactor.last_used_counter < counter),
            ).values(last_used_counter=counter, updated_at=now))
            accepted = updated.rowcount == 1
    if not accepted:
        accepted = _consume_recovery_code(session, user, payload.code)
    if not accepted:
        session.execute(update(MFAChallenge).where(
            MFAChallenge.id == challenge.id,
            MFAChallenge.consumed_at.is_(None),
            MFAChallenge.attempts < MAX_CHALLENGE_ATTEMPTS,
        ).values(attempts=MFAChallenge.attempts + 1))
        session.commit()
        raise HTTPException(status_code=401, detail="Invalid or expired MFA challenge")

    consumed = session.execute(update(MFAChallenge).where(
        MFAChallenge.id == challenge.id,
        MFAChallenge.consumed_at.is_(None),
        MFAChallenge.expires_at > now,
        MFAChallenge.attempts < MAX_CHALLENGE_ATTEMPTS,
    ).values(consumed_at=now))
    if consumed.rowcount != 1:
        session.rollback()
        raise HTTPException(status_code=401, detail="Invalid or expired MFA challenge")
    auth_session = AuthSession(
        id=str(uuid4()), organization_id=user.organization_id, user_id=user.id,
        token_version=user.token_version, created_at=now, last_seen_at=now,
        expires_at=now + timedelta(minutes=ACCESS_TOKEN_MINUTES),
    )
    session.add(auth_session)
    record_audit(session, user, "mfa.login_verified", "auth_session", auth_session.id)
    session.commit()
    return {
        "access_token": create_access_token(user, auth_session.id),
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_MINUTES * 60,
    }


@router.post("/disable")
def disable_mfa(
    payload: MFADisable, request: Request, session: SessionDep, user: CurrentUser
):
    client_ip = request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")
    _enforce_mfa_rate_limit(f"ecc:auth:mfa:disable:{user.id}:{client_ip}")
    if not password_hash.verify(payload.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    factor = _factor_for_user(session, user)
    if factor is None or factor.status != "enabled":
        raise HTTPException(status_code=409, detail="MFA is not enabled")
    secret = decrypt_totp_secret(
        factor.encrypted_secret, organization_id=user.organization_id,
        user_id=user.id, keyring=_keyring(),
    )
    counter = verify_totp(secret, payload.code, _now().timestamp(), valid_window=1)
    if counter is None or (
        factor.last_used_counter is not None and counter <= factor.last_used_counter
    ):
        if not _consume_recovery_code(session, user, payload.code):
            raise HTTPException(status_code=400, detail="Invalid authenticator or recovery code")
    now = _now()
    factor.status = "disabled"
    factor.pending_expires_at = None
    factor.updated_at = now
    user.token_version += 1
    session.execute(update(AuthSession).where(
        AuthSession.organization_id == user.organization_id,
        AuthSession.user_id == user.id,
        AuthSession.revoked_at.is_(None),
    ).values(revoked_at=now))
    session.execute(MFARecoveryCode.__table__.delete().where(
        MFARecoveryCode.organization_id == user.organization_id,
        MFARecoveryCode.user_id == user.id,
    ))
    record_audit(session, user, "mfa.disabled", "mfa_factor", factor.id)
    session.commit()
    return {"status": "disabled", "sessions_revoked": True}
