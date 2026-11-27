from __future__ import annotations

import base64
import secrets
from datetime import datetime, timedelta, timezone
import time
from typing import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.main import Base, Organization, User, UserRole, app, get_session, password_hash
from app.mfa import totp_code
from app.mfa_crypto import MFAEncryptionKeyring
from app.mfa_models import MFAChallenge, MFAFactor, MFARecoveryCode
from app import mfa_routes


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    monkeypatch.setattr(main, "SECRET_KEY", "test-only-secret-key-that-is-longer-than-32-bytes")
    monkeypatch.setattr(main, "login_rate_limiter", main.LoginRateLimiter(20, 60))
    key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")
    monkeypatch.setenv("ECC_MFA_ENCRYPTION_KEY", key)
    monkeypatch.setattr(main, "redis_client", None)
    monkeypatch.setattr(mfa_routes, "mfa_rate_limiter", main.LoginRateLimiter(20, 60))
    monkeypatch.setattr(mfa_routes, "MFA_ALLOW_LOCAL_RATELIMIT", True)
    test_engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    testing_session = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(bind=test_engine)
    with testing_session() as session:
        org = Organization(name="MFA Test Org")
        session.add(org)
        session.flush()
        session.add(User(
            organization_id=org.id, email="mfa@example.com", full_name="MFA User",
            password_hash=password_hash.hash("correct-password-123"), role=UserRole.admin,
        ))
        session.commit()

    def override_get_session():
        with testing_session() as session:
            yield session
    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=test_engine)
    test_engine.dispose()


def next_totp_code(secret: str) -> str:
    next_counter = int(time.time() // 30) + 1
    return totp_code(secret, timestamp=next_counter * 30)


def login(client: TestClient):
    return client.post("/api/v1/auth/token", data={
        "username": "mfa@example.com", "password": "correct-password-123"
    })


def begin_enrollment(
    client: TestClient,
    headers: dict[str, str],
    current_password: str = "correct-password-123",
):
    return client.post(
        "/api/v1/auth/mfa/enrollment",
        headers=headers,
        json={"current_password": current_password},
    )


def test_enrollment_requires_current_password_and_rejects_invalid_password(client: TestClient):
    token = login(client).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    missing = client.post("/api/v1/auth/mfa/enrollment", headers=headers)
    assert missing.status_code == 422

    rejected = begin_enrollment(client, headers, current_password="incorrect-password")
    assert rejected.status_code == 400
    assert rejected.json()["detail"] == "Current password is incorrect"
    assert client.get("/api/v1/auth/mfa/status", headers=headers).json()["status"] == "disabled"

    accepted = begin_enrollment(client, headers)
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "pending"


def test_enrollment_requires_confirmation_and_returns_recovery_codes_once(client: TestClient):
    token = login(client).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    start = begin_enrollment(client, headers)
    assert start.status_code == 200
    secret = start.json()["secret"]
    assert start.json()["status"] == "pending"

    code = totp_code(secret)
    confirmed = client.post("/api/v1/auth/mfa/enrollment/confirm", headers=headers, json={"code": code})
    assert confirmed.status_code == 200
    codes = confirmed.json()["recovery_codes"]
    assert len(codes) == 10
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401
    challenge = login(client).json()["challenge_token"]
    fresh_login = client.post("/api/v1/auth/mfa/challenge/verify", json={
        "challenge_token": challenge, "code": next_totp_code(secret)
    })
    assert fresh_login.status_code == 200
    fresh_headers = {"Authorization": f"Bearer {fresh_login.json()['access_token']}"}
    assert client.post(
        "/api/v1/auth/mfa/enrollment/confirm", headers=fresh_headers, json={"code": code}
    ).status_code == 409


def test_password_login_does_not_issue_access_token_when_mfa_enabled(client: TestClient):
    token = login(client).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    start = begin_enrollment(client, headers).json()
    client.post("/api/v1/auth/mfa/enrollment/confirm", headers=headers, json={"code": totp_code(start["secret"])})
    response = login(client)
    assert response.status_code == 200
    body = response.json()
    assert body["mfa_required"] is True
    assert body["access_token"] is None
    assert body["challenge_token"]


def test_challenge_verification_issues_session_and_rejects_replay(client: TestClient):
    token = login(client).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    start = begin_enrollment(client, headers).json()
    client.post("/api/v1/auth/mfa/enrollment/confirm", headers=headers, json={"code": totp_code(start["secret"])})
    challenge = login(client).json()["challenge_token"]
    code = next_totp_code(start["secret"])
    verified = client.post("/api/v1/auth/mfa/challenge/verify", json={"challenge_token": challenge, "code": code})
    assert verified.status_code == 200
    assert verified.json()["access_token"]
    replay_same_challenge = client.post(
        "/api/v1/auth/mfa/challenge/verify",
        json={"challenge_token": challenge, "code": code},
    )
    assert replay_same_challenge.status_code == 401

    second_challenge = login(client).json()["challenge_token"]
    replay_new_challenge = client.post(
        "/api/v1/auth/mfa/challenge/verify",
        json={"challenge_token": second_challenge, "code": code},
    )
    assert replay_new_challenge.status_code == 401


def test_recovery_code_is_single_use(client: TestClient):
    token = login(client).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    start = begin_enrollment(client, headers).json()
    codes = client.post(
        "/api/v1/auth/mfa/enrollment/confirm", headers=headers,
        json={"code": totp_code(start["secret"])},
    ).json()["recovery_codes"]
    challenge = login(client).json()["challenge_token"]
    first = client.post("/api/v1/auth/mfa/challenge/verify", json={
        "challenge_token": challenge, "code": codes[0]
    })
    assert first.status_code == 200
    second_challenge = login(client).json()["challenge_token"]
    second = client.post("/api/v1/auth/mfa/challenge/verify", json={
        "challenge_token": second_challenge, "code": codes[0]
    })
    assert second.status_code == 401



def test_disabling_mfa_revokes_existing_sessions(client: TestClient):
    token = login(client).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    start = begin_enrollment(client, headers).json()
    confirmed = client.post(
        "/api/v1/auth/mfa/enrollment/confirm",
        headers=headers,
        json={"code": totp_code(start["secret"])},
    )
    assert confirmed.status_code == 200
    challenge = login(client).json()["challenge_token"]
    authenticated = client.post("/api/v1/auth/mfa/challenge/verify", json={
        "challenge_token": challenge, "code": next_totp_code(start["secret"])
    })
    assert authenticated.status_code == 200
    headers = {"Authorization": f"Bearer {authenticated.json()['access_token']}"}

    disabled = client.post(
        "/api/v1/auth/mfa/disable",
        headers=headers,
        json={
            "current_password": "correct-password-123",
            "code": confirmed.json()["recovery_codes"][0],
        },
    )
    assert disabled.status_code == 200
    assert disabled.json()["sessions_revoked"] is True
    assert client.get("/api/v1/assets", headers=headers).status_code == 401



def test_enabling_mfa_revokes_existing_sessions_and_audits(client: TestClient):
    first_token = login(client).json()["access_token"]
    second_token = login(client).json()["access_token"]
    headers = {"Authorization": f"Bearer {first_token}"}
    start = begin_enrollment(client, headers).json()
    confirmed = client.post(
        "/api/v1/auth/mfa/enrollment/confirm",
        headers=headers,
        json={"code": totp_code(start["secret"])},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["sessions_revoked"] is True
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401
    assert client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {second_token}"}
    ).status_code == 401

    challenge = login(client).json()["challenge_token"]
    verified = client.post("/api/v1/auth/mfa/challenge/verify", json={
        "challenge_token": challenge, "code": next_totp_code(start["secret"])
    })
    assert verified.status_code == 200
    audit = client.get(
        "/api/v1/audit-events",
        headers={"Authorization": f"Bearer {verified.json()['access_token']}"},
    )
    assert audit.status_code == 200
    actions = {event["action"] for event in audit.json()}
    assert "mfa.enabled" in actions
    assert "mfa.sessions_revoked_on_enable" in actions


def test_recovery_login_revokes_other_sessions_and_has_distinct_audit(client: TestClient):
    initial = login(client).json()["access_token"]
    headers = {"Authorization": f"Bearer {initial}"}
    start = begin_enrollment(client, headers).json()
    codes = client.post(
        "/api/v1/auth/mfa/enrollment/confirm",
        headers=headers, json={"code": totp_code(start["secret"])},
    ).json()["recovery_codes"]

    first_challenge = login(client).json()["challenge_token"]
    first_login = client.post("/api/v1/auth/mfa/challenge/verify", json={
        "challenge_token": first_challenge, "code": next_totp_code(start["secret"])
    })
    assert first_login.status_code == 200
    existing_token = first_login.json()["access_token"]

    recovery_challenge = login(client).json()["challenge_token"]
    recovered = client.post("/api/v1/auth/mfa/challenge/verify", json={
        "challenge_token": recovery_challenge, "code": codes[0]
    })
    assert recovered.status_code == 200
    assert client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {existing_token}"}
    ).status_code == 401
    assert client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {recovered.json()['access_token']}"}
    ).status_code == 200

    audit = client.get(
        "/api/v1/audit-events",
        headers={"Authorization": f"Bearer {recovered.json()['access_token']}"},
    )
    actions = {event["action"] for event in audit.json()}
    assert "mfa.recovery_code_used" in actions
    assert "mfa.sessions_revoked_on_recovery" in actions


def test_recovery_code_regeneration_rotates_codes_and_revokes_sessions(client: TestClient):
    token = login(client).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    start = begin_enrollment(client, headers).json()
    confirmed = client.post(
        "/api/v1/auth/mfa/enrollment/confirm",
        headers=headers, json={"code": totp_code(start["secret"])},
    ).json()
    old_code = confirmed["recovery_codes"][0]
    challenge = login(client).json()["challenge_token"]
    authenticated = client.post("/api/v1/auth/mfa/challenge/verify", json={
        "challenge_token": challenge, "code": old_code
    })
    assert authenticated.status_code == 200
    headers = {"Authorization": f"Bearer {authenticated.json()['access_token']}"}

    regenerated = client.post(
        "/api/v1/auth/mfa/recovery-codes/regenerate",
        headers=headers,
        json={
            "current_password": "correct-password-123",
            "code": next_totp_code(start["secret"]),
        },
    )
    assert regenerated.status_code == 200
    new_codes = regenerated.json()["recovery_codes"]
    assert len(new_codes) == 10
    assert old_code not in new_codes
    assert regenerated.json()["sessions_revoked"] is True
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401
