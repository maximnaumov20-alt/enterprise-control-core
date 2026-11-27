from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, or_, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.main import Base, Organization, User, UserRole, password_hash
from app.mfa import generate_totp_secret, totp_code, verify_totp
from app.mfa_models import MFAFactor
from app.mfa_routes import _disable_mfa_factor_once


def test_concurrent_totp_counter_claim_has_exactly_one_winner():
    database_url = os.getenv("ECC_TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("ECC_TEST_POSTGRES_URL is required for PostgreSQL concurrency coverage")

    parsed = make_url(database_url)
    if parsed.get_backend_name() != "postgresql" or parsed.database not in {"ecc_test", "test"}:
        raise RuntimeError("Refusing to run outside the dedicated ECC PostgreSQL test database")

    schema = f"ecc_mfa_test_{uuid4().hex}"
    engine = create_engine(database_url, pool_size=5, max_overflow=5, pool_pre_ping=True)
    try:
        # Isolate every run in its own schema. Never drop the shared test database's tables.
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        isolated_engine = engine.execution_options(schema_translate_map={None: schema})
        Base.metadata.create_all(bind=isolated_engine)
        SessionLocal = sessionmaker(bind=isolated_engine, autoflush=False, expire_on_commit=False)
        factor_id = str(uuid4())

        try:
            secret = generate_totp_secret()
            now = datetime.now(timezone.utc)
            with SessionLocal.begin() as session:
                organization = Organization(name=f"MFA concurrency {uuid4().hex[:8]}")
                session.add(organization)
                session.flush()
                user = User(
                    organization_id=organization.id,
                    email=f"{uuid4().hex}@example.test",
                    full_name="Concurrency Test",
                    password_hash=password_hash.hash("test-only-password"),
                    role=UserRole.admin,
                )
                session.add(user)
                session.flush()
                session.add(MFAFactor(
                    id=factor_id,
                    organization_id=organization.id,
                    user_id=user.id,
                    encrypted_secret="test-envelope-not-decrypted-by-this-test",
                    status="enabled",
                    pending_expires_at=None,
                    enabled_at=now,
                    last_used_counter=None,
                    created_at=now,
                    updated_at=now,
                ))

            timestamp = int(now.timestamp())
            code = totp_code(secret, timestamp=timestamp)
            accepted_counter = verify_totp(secret, code, timestamp, valid_window=0)
            assert accepted_counter is not None

            def claim_counter() -> int:
                with SessionLocal.begin() as session:
                    result = session.execute(
                        update(MFAFactor)
                        .where(
                            MFAFactor.id == factor_id,
                            MFAFactor.status == "enabled",
                            or_(
                                MFAFactor.last_used_counter.is_(None),
                                MFAFactor.last_used_counter < accepted_counter,
                            ),
                        )
                        .values(last_used_counter=accepted_counter, updated_at=now)
                        .execution_options(synchronize_session=False)
                    )
                    return result.rowcount or 0

            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(lambda _: claim_counter(), range(2)))

            assert sorted(outcomes) == [0, 1]
            with SessionLocal() as session:
                factor = session.get(MFAFactor, factor_id)
                assert factor is not None
                assert factor.last_used_counter == accepted_counter

            # Reset the fixture counter to model two requests that both validated
            # the same fresh code before either request reached the database update.
            with SessionLocal.begin() as session:
                session.execute(
                    update(MFAFactor)
                    .where(MFAFactor.id == factor_id)
                    .values(status="enabled", last_used_counter=None)
                )

            def disable_factor() -> bool:
                with SessionLocal.begin() as session:
                    return _disable_mfa_factor_once(
                        session,
                        factor_id=factor_id,
                        organization_id=organization.id,
                        user_id=user.id,
                        now=now,
                        counter=accepted_counter,
                    )

            with ThreadPoolExecutor(max_workers=2) as pool:
                disable_outcomes = list(pool.map(lambda _: disable_factor(), range(2)))

            assert sorted(disable_outcomes) == [False, True]
            with SessionLocal() as session:
                factor = session.get(MFAFactor, factor_id)
                assert factor is not None
                assert factor.status == "disabled"
                assert factor.last_used_counter == accepted_counter
        finally:
            Base.metadata.drop_all(bind=isolated_engine)
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()
