from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
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
    password_hash,
)


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    monkeypatch.setattr(main, "SECRET_KEY", "test-only-secret-key-that-is-longer-than-32-bytes")
    monkeypatch.setattr(main, "login_rate_limiter", main.LoginRateLimiter(10, 60))
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(bind=test_engine)

    with testing_session() as session:
        organization = Organization(name="RBAC Test Organization")
        session.add(organization)
        session.flush()
        users = {
            "admin": User(
                organization_id=organization.id,
                email="rbac-admin@example.com",
                full_name="RBAC Admin",
                password_hash=password_hash.hash("test-password-admin-123"),
                role=UserRole.admin,
            ),
            "editor": User(
                organization_id=organization.id,
                email="rbac-editor@example.com",
                full_name="RBAC Editor",
                password_hash=password_hash.hash("test-password-editor-123"),
                role=UserRole.editor,
            ),
            "viewer": User(
                organization_id=organization.id,
                email="rbac-viewer@example.com",
                full_name="RBAC Viewer",
                password_hash=password_hash.hash("test-password-viewer-123"),
                role=UserRole.viewer,
            ),
        }
        session.add_all(users.values())
        session.commit()
        tokens = {role: create_access_token(user) for role, user in users.items()}

    def override_get_session():
        with testing_session() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    app.state.test_tokens = tokens
    try:
        with TestClient(app) as test_client:
            test_client.headers.update({"Authorization": f"Bearer {tokens['admin']}"})
            yield test_client
    finally:
        app.dependency_overrides.clear()
        app.state.test_tokens = {}
        Base.metadata.drop_all(bind=test_engine)
        test_engine.dispose()


def use_role(client: TestClient, role: str) -> None:
    client.headers.update({"Authorization": f"Bearer {client.app.state.test_tokens[role]}"})


def test_editor_role_matrix_allows_operational_writes_but_not_admin_actions(client: TestClient) -> None:
    asset = client.post(
        "/api/v1/assets",
        json={"name": "RBAC Server", "asset_tag": "RBAC-SRV-01", "category": "Server"},
    ).json()
    department = client.post(
        "/api/v1/departments",
        json={"name": "RBAC Operations", "code": "RBAC-OPS"},
    ).json()
    employee = client.post(
        "/api/v1/employees",
        json={"employee_number": "RBAC-EMP-01", "full_name": "RBAC Employee"},
    ).json()
    license_record = client.post(
        "/api/v1/software-licenses",
        json={
            "name": "RBAC Suite",
            "license_type": "perpetual",
            "license_reference": "RBAC-LIC-01",
            "seats_total": 10,
            "seats_used": 2,
        },
    ).json()
    maintenance = client.post(
        "/api/v1/maintenance-requests",
        json={
            "asset_id": asset["id"],
            "title": "RBAC maintenance",
            "description": "Test editor permissions",
        },
    ).json()

    use_role(client, "editor")

    assert client.patch(
        f"/api/v1/assets/{asset['id']}", json={"name": "Updated by editor"}
    ).status_code == 200
    assert client.patch(
        f"/api/v1/software-licenses/{license_record['id']}", json={"seats_used": 3}
    ).status_code == 200
    assert client.patch(
        f"/api/v1/maintenance-requests/{maintenance['id']}", json={"title": "Editor updated"}
    ).status_code == 200
    assert client.patch(
        f"/api/v1/employees/{employee['id']}", json={"job_title": "Editor-managed"}
    ).status_code == 200
    assert client.post(
        "/api/v1/employees",
        json={"employee_number": "RBAC-EMP-02", "full_name": "Editor Created"},
    ).status_code == 201
    assert client.get("/api/v1/departments").status_code == 200

    user_payload = {
        "email": "blocked-user@example.com",
        "full_name": "Blocked User",
        "password": "long-enough-password",
        "role": "viewer",
    }
    assert client.get("/api/v1/users").status_code == 403
    assert client.post("/api/v1/users", json=user_payload).status_code == 403
    assert client.patch(
        "/api/v1/users/1", json={"full_name": "Forbidden"}
    ).status_code == 403
    assert client.post(
        "/api/v1/departments", json={"name": "Forbidden", "code": "FORBIDDEN"}
    ).status_code == 403
    assert client.patch(
        f"/api/v1/departments/{department['id']}", json={"name": "Forbidden"}
    ).status_code == 403
    assert client.delete(f"/api/v1/departments/{department['id']}").status_code == 403
    assert client.delete(f"/api/v1/employees/{employee['id']}").status_code == 403
    assert client.get("/api/v1/audit-events").status_code == 403


def test_viewer_role_is_read_only_except_own_maintenance_requests(client: TestClient) -> None:
    asset = client.post(
        "/api/v1/assets",
        json={"name": "Viewer Server", "asset_tag": "VIEW-RBAC-01", "category": "Server"},
    ).json()
    license_record = client.post(
        "/api/v1/software-licenses",
        json={
            "name": "Viewer Suite",
            "license_type": "subscription",
            "license_reference": "VIEW-RBAC-LIC",
            "seats_total": 4,
        },
    ).json()
    department = client.post(
        "/api/v1/departments", json={"name": "Viewer Department", "code": "VIEW-DEPT"}
    ).json()
    employee = client.post(
        "/api/v1/employees",
        json={"employee_number": "VIEW-EMP-01", "full_name": "Viewer Employee"},
    ).json()
    other_request = client.post(
        "/api/v1/maintenance-requests",
        json={
            "asset_id": asset["id"],
            "title": "Admin request",
            "description": "Not owned by the viewer",
        },
    ).json()

    use_role(client, "viewer")

    assert client.get("/api/v1/assets").status_code == 200
    assert client.get(f"/api/v1/assets/{asset['id']}/history").status_code == 200
    assert client.get("/api/v1/software-licenses").status_code == 200
    assert client.get(f"/api/v1/software-licenses/{license_record['id']}").status_code == 200
    assert client.get("/api/v1/assets/export").status_code == 200

    own_request = client.post(
        "/api/v1/maintenance-requests",
        json={
            "asset_id": asset["id"],
            "title": "Viewer request",
            "description": "Created by the viewer",
        },
    )
    assert own_request.status_code == 201
    own_request_id = own_request.json()["id"]
    listed_requests = client.get("/api/v1/maintenance-requests").json()
    assert [item["id"] for item in listed_requests] == [own_request_id]
    assert client.get(f"/api/v1/maintenance-requests/{own_request_id}").status_code == 200
    assert client.get(f"/api/v1/maintenance-requests/{other_request['id']}").status_code == 404

    assert client.post(
        "/api/v1/assets",
        json={"name": "Forbidden", "asset_tag": "VIEW-FORBIDDEN", "category": "Laptop"},
    ).status_code == 403
    assert client.patch(
        f"/api/v1/assets/{asset['id']}", json={"name": "Forbidden"}
    ).status_code == 403
    assert client.delete(f"/api/v1/assets/{asset['id']}").status_code == 403
    assert client.patch(
        f"/api/v1/software-licenses/{license_record['id']}", json={"seats_used": 1}
    ).status_code == 403
    assert client.post(
        "/api/v1/software-licenses",
        json={
            "name": "Forbidden",
            "license_type": "perpetual",
            "seats_total": 1,
        },
    ).status_code == 403
    assert client.patch(
        f"/api/v1/maintenance-requests/{own_request_id}", json={"status": "resolved"}
    ).status_code == 403
    assert client.get("/api/v1/departments").status_code == 403
    assert client.get("/api/v1/employees").status_code == 403
    assert client.get("/api/v1/users").status_code == 403
    assert client.get("/api/v1/audit-events").status_code == 403
    assert client.post(
        "/api/v1/assets/import",
        files={"file": ("assets.csv", b"name,asset_tag,category\nNew,VIEW-CSV-01,Laptop\n", "text/csv")},
    ).status_code == 403
