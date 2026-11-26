from collections.abc import Generator
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.main import (
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

        def override_get_session():
            with testing_session() as session:
                yield session

        app.dependency_overrides[get_session] = override_get_session
        app.state.tenant_test_tokens = tokens
        try:
            with TestClient(app) as client:
                client.headers.update({"Authorization": f"Bearer {tokens['a']}"})
                yield client
        finally:
            app.dependency_overrides.clear()
            app.state.tenant_test_tokens = {}
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

    # Audit history is tenant-scoped; Tenant B must not see Tenant A events.
    assert client.get("/api/v1/audit-events").json() == []
