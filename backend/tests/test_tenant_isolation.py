from collections.abc import Generator
from datetime import datetime, timedelta, timezone
import os
from uuid import uuid4
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.main import (
    AuthSession,
    Base,
    Organization,
    User,
    UserRole,
    app,
    create_access_token,
    get_session,
)


@pytest.fixture(params=["sqlite", "postgres"] if os.getenv("ECC_TEST_POSTGRES_URL") else ["sqlite"])
def tenant_client(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[TestClient, None, None]:
    monkeypatch.setattr(main, "SECRET_KEY", "tenant-isolation-test-secret-key-over-32-bytes")
    monkeypatch.setattr(main, "login_rate_limiter", main.LoginRateLimiter(10, 60))
    monkeypatch.setenv("ECC_MFA_ENCRYPTION_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")

    schema = None
    if request.param == "postgres":
        database_url = os.environ["ECC_TEST_POSTGRES_URL"]
        parsed = make_url(database_url)
        if parsed.get_backend_name() != "postgresql" or parsed.database not in {"ecc_test", "test"}:
            raise RuntimeError("Refusing to run tenant isolation tests outside the dedicated ECC PostgreSQL test database")
        engine = create_engine(database_url, pool_size=5, max_overflow=5, pool_pre_ping=True)
        schema = f"ecc_tenant_test_{uuid4().hex}"
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        test_engine = engine.execution_options(schema_translate_map={None: schema})
    else:
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        test_engine = engine

    testing_session = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)
    try:
        Base.metadata.create_all(bind=test_engine)

        with testing_session() as session:
            first_org = Organization(name="Tenant Isolation Organization A")
            second_org = Organization(name="Tenant Isolation Organization B")
            session.add_all([first_org, second_org])
            session.flush()

            first_admin = User(
                organization_id=first_org.id,
                email="tenant-a-admin@example.com",
                full_name="Tenant A Admin",
                password_hash="test-only-not-used-for-authentication",
                role=UserRole.admin,
            )
            second_admin = User(
                organization_id=second_org.id,
                email="tenant-b-admin@example.com",
                full_name="Tenant B Admin",
                password_hash="test-only-not-used-for-authentication",
                role=UserRole.admin,
            )
            session.add_all([first_admin, second_admin])
            session.commit()

            tokens = {
                "a": create_access_token(first_admin),
                "b": create_access_token(second_admin),
            }
            now = datetime.now(timezone.utc)
            session_ids = {"a": str(uuid4()), "b": str(uuid4())}
            session.add_all([
                AuthSession(
                    id=session_ids["a"],
                    organization_id=first_org.id,
                    user_id=first_admin.id,
                    token_version=first_admin.token_version,
                    created_at=now,
                    last_seen_at=now,
                    expires_at=now + timedelta(minutes=20),
                ),
                AuthSession(
                    id=session_ids["b"],
                    organization_id=second_org.id,
                    user_id=second_admin.id,
                    token_version=second_admin.token_version,
                    created_at=now,
                    last_seen_at=now,
                    expires_at=now + timedelta(minutes=20),
                ),
            ])
            session.commit()

        def override_get_session():
            with testing_session() as session:
                yield session

        app.dependency_overrides[get_session] = override_get_session
        app.state.tenant_test_tokens = tokens
        app.state.tenant_test_session_ids = session_ids
        try:
            with TestClient(app) as client:
                client.headers.update({"Authorization": f"Bearer {tokens['a']}"})
                yield client
        finally:
            app.dependency_overrides.clear()
            app.state.tenant_test_tokens = {}
            app.state.tenant_test_session_ids = {}
    finally:
        Base.metadata.drop_all(bind=test_engine)
        if schema is not None:
            with engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()


def authenticate_as(client: TestClient, tenant: str) -> None:
    token = client.app.state.tenant_test_tokens[tenant]
    client.headers.update({"Authorization": f"Bearer {token}"})


def assert_status(response, expected: int) -> None:
    assert response.status_code == expected, response.text


def test_tenant_owned_resources_cannot_be_read_or_mutated_across_organizations(
    tenant_client: TestClient,
) -> None:
    client = tenant_client

    asset = client.post(
        "/api/v1/assets",
        json={"name": "Tenant A server", "asset_tag": "TENANT-A-SRV-01", "category": "Server"},
    )
    assert_status(asset, 201)
    asset = asset.json()

    department = client.post(
        "/api/v1/departments",
        json={"name": "Tenant A operations", "code": "TENANT-A-OPS"},
    )
    assert_status(department, 201)
    department = department.json()

    employee = client.post(
        "/api/v1/employees",
        json={
            "employee_number": "TENANT-A-EMP-01",
            "full_name": "Tenant A Employee",
            "department_id": department["id"],
        },
    )
    assert_status(employee, 201)
    employee = employee.json()

    license_record = client.post(
        "/api/v1/software-licenses",
        json={
            "name": "Tenant A software",
            "license_type": "subscription",
            "license_reference": "TENANT-A-LIC-01",
            "seats_total": 10,
            "seats_used": 2,
        },
    )
    assert_status(license_record, 201)
    license_record = license_record.json()

    maintenance = client.post(
        "/api/v1/maintenance-requests",
        json={
            "asset_id": asset["id"],
            "title": "Tenant A maintenance",
            "description": "Tenant A private maintenance record",
        },
    )
    assert_status(maintenance, 201)
    maintenance = maintenance.json()

    tenant_user = client.post(
        "/api/v1/users",
        json={
            "email": "tenant-a-created-user@example.com",
            "full_name": "Tenant A Created User",
            "password": "test-only-password-long-enough",
            "role": "viewer",
        },
    )
    assert_status(tenant_user, 201)
    tenant_user = tenant_user.json()

    authenticate_as(client, "b")

    assert client.get("/api/v1/assets").json() == []
    assert_status(client.get(f"/api/v1/assets/{asset['id']}"), 404)
    assert_status(client.get(f"/api/v1/assets/{asset['id']}/history"), 404)
    assert_status(
        client.patch(
            f"/api/v1/assets/{asset['id']}",
            json={"expected_version": asset["version"], "name": "Cross-tenant overwrite"},
        ),
        404,
    )
    assert_status(client.delete(f"/api/v1/assets/{asset['id']}"), 404)

    assert client.get("/api/v1/departments").json() == []
    assert_status(
        client.patch(
            f"/api/v1/departments/{department['id']}",
            json={"expected_version": department["version"], "name": "Cross-tenant overwrite"},
        ),
        404,
    )
    assert_status(client.delete(f"/api/v1/departments/{department['id']}"), 404)

    assert client.get("/api/v1/employees").json() == []
    assert_status(
        client.patch(
            f"/api/v1/employees/{employee['id']}",
            json={"expected_version": employee["version"], "full_name": "Cross-tenant overwrite"},
        ),
        404,
    )
    assert_status(client.delete(f"/api/v1/employees/{employee['id']}"), 404)

    assert client.get("/api/v1/software-licenses").json() == []
    assert_status(client.get(f"/api/v1/software-licenses/{license_record['id']}"), 404)
    assert_status(
        client.patch(
            f"/api/v1/software-licenses/{license_record['id']}",
            json={"expected_version": license_record["record_version"], "name": "Cross-tenant overwrite"},
        ),
        404,
    )
    assert_status(client.delete(f"/api/v1/software-licenses/{license_record['id']}"), 404)

    assert client.get("/api/v1/maintenance-requests").json() == []
    assert_status(client.get(f"/api/v1/maintenance-requests/{maintenance['id']}"), 404)
    assert_status(
        client.patch(
            f"/api/v1/maintenance-requests/{maintenance['id']}",
            json={"expected_version": maintenance["version"], "title": "Cross-tenant overwrite"},
        ),
        404,
    )

    assert_status(
        client.patch(
            f"/api/v1/users/{tenant_user['id']}",
            json={"expected_version": tenant_user["version"], "full_name": "Cross-tenant overwrite"},
        ),
        404,
    )
    listed_users = client.get("/api/v1/users").json()
    assert [item["email"] for item in listed_users] == ["tenant-b-admin@example.com"]

    # Bulk operations and CSV export/import must preserve tenant boundaries too.
    assert_status(
        client.patch(
            "/api/v1/assets/bulk",
            json={
                "asset_ids": [asset["id"]],
                "versions": {str(asset["id"]): asset["version"]},
                "status": "in_repair",
            },
        ),
        404,
    )
    exported = client.get("/api/v1/assets/export")
    assert exported.status_code == 200
    assert "TENANT-A-SRV-01" not in exported.text
    assert "Tenant A server" not in exported.text

    imported = client.post(
        "/api/v1/assets/import",
        files={
            "file": (
                "tenant-b-assets.csv",
                "name,asset_tag,category\nTenant B imported asset,TENANT-B-IMPORTED-01,Server\n",
                "text/csv",
            )
        },
    )
    assert_status(imported, 200)
    assert imported.json()["created"] == 1
    assert imported.json()["skipped"] == 0
    imported_assets = client.get("/api/v1/assets").json()
    assert [item["asset_tag"] for item in imported_assets] == ["TENANT-B-IMPORTED-01"]

    authenticate_as(client, "a")
    first_tenant_assets = client.get("/api/v1/assets").json()
    assert [item["asset_tag"] for item in first_tenant_assets] == ["TENANT-A-SRV-01"]
    assert_status(client.get(f"/api/v1/assets/{imported_assets[0]['id']}"), 404)
    authenticate_as(client, "b")

    # Foreign organization references must be rejected even when the caller
    # knows another tenant's numeric resource IDs.
    assert_status(
        client.post(
            "/api/v1/maintenance-requests",
            json={
                "asset_id": asset["id"],
                "title": "Foreign asset reference",
                "description": "Must not link across organizations",
            },
        ),
        404,
    )
    assert_status(
        client.post(
            "/api/v1/employees",
            json={
                "employee_number": "TENANT-B-EMP-FOREIGN",
                "full_name": "Foreign department reference",
                "department_id": department["id"],
            },
        ),
        422,
    )

    # Audit history is tenant-scoped; Tenant B must not see Tenant A's asset events.
    tenant_b_events = client.get("/api/v1/audit-events").json()
    assert tenant_b_events
    assert all(event["entity_id"] != str(asset["id"]) for event in tenant_b_events)
    assert any(event["action"] == "asset.imported" for event in tenant_b_events)



def test_auth_sessions_are_isolated_between_users_and_organizations(
    tenant_client: TestClient,
) -> None:
    client = tenant_client
    session_ids = client.app.state.tenant_test_session_ids

    # A legacy access token may list only the authenticated user's sessions.
    authenticate_as(client, "b")
    tenant_b_sessions = client.get("/api/v1/auth/sessions")
    assert_status(tenant_b_sessions, 200)
    assert [item["id"] for item in tenant_b_sessions.json()] == [session_ids["b"]]

    # Knowing another tenant's session identifier must not allow revocation.
    assert_status(
        client.delete(f"/api/v1/auth/sessions/{session_ids['a']}"),
        404,
    )

    # The failed cross-tenant attempt must leave Tenant A's session intact.
    authenticate_as(client, "a")
    tenant_a_sessions = client.get("/api/v1/auth/sessions")
    assert_status(tenant_a_sessions, 200)
    assert [item["id"] for item in tenant_a_sessions.json()] == [session_ids["a"]]
    assert tenant_a_sessions.json()[0]["revoked_at"] is None

    # Logout-all for Tenant B must not revoke Tenant A's session.
    authenticate_as(client, "b")
    assert_status(client.post("/api/v1/auth/logout-all"), 200)
    authenticate_as(client, "a")
    after_logout_all = client.get("/api/v1/auth/sessions")
    assert_status(after_logout_all, 200)
    assert [item["id"] for item in after_logout_all.json()] == [session_ids["a"]]
    assert after_logout_all.json()[0]["revoked_at"] is None


def test_mfa_enrollment_state_is_isolated_between_organizations(
    tenant_client: TestClient,
) -> None:
    client = tenant_client

    tenant_a_status = client.get("/api/v1/auth/mfa/status")
    assert_status(tenant_a_status, 200)
    assert tenant_a_status.json() == {
        "status": "disabled",
        "enabled": False,
        "recovery_codes_remaining": 0,
    }

    tenant_a_enrollment = client.post("/api/v1/auth/mfa/enrollment")
    assert_status(tenant_a_enrollment, 200)
    tenant_a_payload = tenant_a_enrollment.json()
    assert tenant_a_payload["status"] == "pending"
    assert "tenant-a-admin@example.com" in unquote(tenant_a_payload["provisioning_uri"])

    # A second organization must not observe or reuse the first organization's
    # pending factor, even though both users have administrator privileges.
    authenticate_as(client, "b")
    tenant_b_status = client.get("/api/v1/auth/mfa/status")
    assert_status(tenant_b_status, 200)
    assert tenant_b_status.json() == {
        "status": "disabled",
        "enabled": False,
        "recovery_codes_remaining": 0,
    }

    tenant_b_enrollment = client.post("/api/v1/auth/mfa/enrollment")
    assert_status(tenant_b_enrollment, 200)
    tenant_b_payload = tenant_b_enrollment.json()
    assert tenant_b_payload["status"] == "pending"
    assert "tenant-b-admin@example.com" in unquote(tenant_b_payload["provisioning_uri"])
    assert tenant_b_payload["secret"] != tenant_a_payload["secret"]

    authenticate_as(client, "a")
    tenant_a_pending_status = client.get("/api/v1/auth/mfa/status")
    assert_status(tenant_a_pending_status, 200)
    assert tenant_a_pending_status.json()["status"] == "pending"
    assert tenant_a_pending_status.json()["enabled"] is False
