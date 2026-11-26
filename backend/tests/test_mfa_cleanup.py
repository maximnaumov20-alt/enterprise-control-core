from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import Base, Organization, User, UserRole, password_hash
from app.mfa_models import MFAChallenge, MFAFactor
from app.cleanup_expired_auth import cleanup_expired_auth_records


def test_cleanup_removes_only_expired_challenges_and_pending_factors():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    try:
        with SessionLocal.begin() as session:
            org = Organization(name=f"Cleanup test {uuid4().hex}")
            session.add(org)
            session.flush()
            expired_user = User(
                organization_id=org.id,
                email=f"expired-{uuid4().hex}@example.test",
                full_name="Expired enrollment",
                password_hash=password_hash.hash("test-only-password"),
                role=UserRole.admin,
            )
            enabled_user = User(
                organization_id=org.id,
                email=f"enabled-{uuid4().hex}@example.test",
                full_name="Enabled factor",
                password_hash=password_hash.hash("test-only-password"),
                role=UserRole.admin,
            )
            session.add_all([expired_user, enabled_user])
            session.flush()
            session.add_all([
                MFAFactor(
                    id=str(uuid4()), organization_id=org.id, user_id=expired_user.id,
                    encrypted_secret="test-only", status="pending",
                    pending_expires_at=now - timedelta(minutes=1), enabled_at=None,
                    last_used_counter=None, created_at=now - timedelta(minutes=11),
                    updated_at=now - timedelta(minutes=11),
                ),
                MFAFactor(
                    id=str(uuid4()), organization_id=org.id, user_id=enabled_user.id,
                    encrypted_secret="test-only", status="enabled",
                    pending_expires_at=None, enabled_at=now - timedelta(days=1),
                    last_used_counter=1, created_at=now - timedelta(days=1),
                    updated_at=now - timedelta(days=1),
                ),
                MFAChallenge(
                    id=str(uuid4()), organization_id=org.id, user_id=enabled_user.id,
                    token_hash=uuid4().hex + uuid4().hex, attempts=0,
                    created_at=now - timedelta(minutes=6), expires_at=now - timedelta(seconds=1),
                    consumed_at=None,
                ),
                MFAChallenge(
                    id=str(uuid4()), organization_id=org.id, user_id=enabled_user.id,
                    token_hash=uuid4().hex + uuid4().hex, attempts=0,
                    created_at=now, expires_at=now + timedelta(minutes=5), consumed_at=None,
                ),
            ])

        with SessionLocal.begin() as session:
            result = cleanup_expired_auth_records(session, now=now)
            assert result == {
                "expired_challenges": 1,
                "expired_pending_enrollments": 1,
            }

        with SessionLocal() as session:
            factors = session.scalars(select(MFAFactor)).all()
            challenges = session.scalars(select(MFAChallenge)).all()
            assert len(factors) == 1
            assert factors[0].status == "enabled"
            assert len(challenges) == 1
            expires_at = challenges[0].expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            assert expires_at > now

        with SessionLocal.begin() as session:
            assert cleanup_expired_auth_records(session, now=now) == {
                "expired_challenges": 0,
                "expired_pending_enrollments": 0,
            }
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
