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


def test_enrollment_requires_confirmation_and_returns_recovery_codes_once(client: TestClient):
    token = login(client).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    start = client.post("/api/v1/auth/mfa/enrollment", headers=headers)
    assert start.status_code == 200
    secret = start.json()["secret"]
    assert start.json()["status"] == "pending"

    code = totp_code(secret)
    confirmed = client.post("/api/v1/auth/mfa/enrollment/confirm", headers=headers, json={"code": code})
    assert confirmed.status_code == 200
    codes = confirmed.json()["recovery_codes"]
    assert len(codes) == 10
    assert client.post("/api/v1/auth/mfa/enrollment/confirm", headers=headers, json={"code": code}).status_code == 409


def test_password_login_does_not_issue_access_token_when_mfa_enabled(client: TestClient):
    token = login(client).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    start = client.post("/api/v1/auth/mfa/enrollment", headers=headers).json()
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
    start = client.post("/api/v1/auth/mfa/enrollment", headers=headers).json()
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
    start = client.post("/api/v1/auth/mfa/enrollment", headers=headers).json()
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
    start = client.post("/api/v1/auth/mfa/enrollment", headers=headers).json()
    confirmed = client.post(
        "/api/v1/auth/mfa/enrollment/confirm",
        headers=headers,
        json={"code": totp_code(start["secret"])},
    )
    assert confirmed.status_code == 200

    disabled = client.post(
        "/api/v1/auth/mfa/disable",
        headers=headers,
        json={
            "current_password": "correct-password-123",
            "code": next_totp_code(start["secret"]),
        },
    )
    assert disabled.status_code == 200
    assert disabled.json()["sessions_revoked"] is True
    assert client.get("/api/v1/assets", headers=headers).status_code == 401
