from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.main import (
    Base, Organization, User, UserRole, app, create_access_token, get_session,
    password_hash,
)


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    monkeypatch.setattr(main, "SECRET_KEY", "test-only-secret-key-that-is-longer-than-32-bytes")
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(
        bind=test_engine, autoflush=False, expire_on_commit=False
    )
    Base.metadata.create_all(bind=test_engine)

    with TestingSessionLocal() as session:
        org_a = Organization(name="Company A")
        org_b = Organization(name="Company B")
        session.add_all([org_a, org_b])
        session.flush()
        users = {
            "admin": User(
                organization_id=org_a.id, email="admin.a@example.com", full_name="Admin",
                password_hash=password_hash.hash("test-password-admin-123"),
                role=UserRole.admin,
            ),
            "editor": User(
                organization_id=org_a.id, email="editor.a@example.com", full_name="Editor",
                password_hash=password_hash.hash("test-password-editor-123"),
                role=UserRole.editor,
            ),
            "viewer": User(
                organization_id=org_a.id, email="viewer.a@example.com", full_name="Viewer",
                password_hash=password_hash.hash("test-password-viewer-123"),
                role=UserRole.viewer,
            ),
            "other_admin": User(
                organization_id=org_b.id, email="admin.b@example.com", full_name="Other Admin",
                password_hash=password_hash.hash("test-password-other-123"),
                role=UserRole.admin,
            ),
        }
        session.add_all(users.values())
        session.commit()
        tokens = {name: create_access_token(user) for name, user in users.items()}

    def override_get_session():
        with TestingSessionLocal() as session:
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


def test_health_endpoint(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_password_login_returns_bearer_token(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/token",
        data={"username": "admin.a@example.com", "password": "test-password-admin-123"},
    )
    assert response.status_code == 200
    assert response.json()["token_type"] == "bearer"
    assert response.json()["expires_in"] == 1200


def test_authentication_required_for_assets(client: TestClient) -> None:
    client.headers.pop("Authorization")
    assert client.get("/api/v1/assets").status_code == 401


def test_asset_create_list_update_delete_and_audit(client: TestClient) -> None:
    created = client.post(
        "/api/v1/assets",
        json={
            "name": "Office laptop",
            "asset_tag": "ECC-LT-001",
            "category": "Laptop",
            "manufacturer": "Example",
        },
    )
    assert created.status_code == 201
    asset_id = created.json()["id"]

    listed = client.get("/api/v1/assets")
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    updated = client.patch(
        f"/api/v1/assets/{asset_id}",
        json={"status": "in_repair"},
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "in_repair"

    events = client.get("/api/v1/audit-events")
    assert events.status_code == 200
    assert [event["action"] for event in events.json()] == ["asset.updated", "asset.created"]

    deleted = client.delete(f"/api/v1/assets/{asset_id}")
    assert deleted.status_code == 204
    assert client.get(f"/api/v1/assets/{asset_id}").status_code == 404


def test_duplicate_asset_tag_is_rejected_within_organization(client: TestClient) -> None:
    payload = {"name": "Office laptop", "asset_tag": "ECC-LT-002", "category": "Laptop"}
    assert client.post("/api/v1/assets", json=payload).status_code == 201
    assert client.post("/api/v1/assets", json=payload).status_code == 409


def test_required_asset_fields_cannot_be_cleared(client: TestClient) -> None:
    created = client.post(
        "/api/v1/assets",
        json={"name": "Office laptop", "asset_tag": "ECC-LT-003", "category": "Laptop"},
    )
    asset_id = created.json()["id"]
    response = client.patch(f"/api/v1/assets/{asset_id}", json={"name": None})
    assert response.status_code == 422


def test_viewer_is_read_only(client: TestClient) -> None:
    use_role(client, "viewer")
    assert client.get("/api/v1/assets").status_code == 200
    payload = {"name": "Read only", "asset_tag": "VIEW-001", "category": "Laptop"}
    assert client.post("/api/v1/assets", json=payload).status_code == 403


def test_tenant_isolation_and_tenant_scoped_asset_tags(client: TestClient) -> None:
    payload = {"name": "Company A laptop", "asset_tag": "SHARED-001", "category": "Laptop"}
    created_a = client.post("/api/v1/assets", json=payload)
    assert created_a.status_code == 201
    asset_a_id = created_a.json()["id"]

    use_role(client, "other_admin")
    assert client.get("/api/v1/assets").json() == []
    assert client.get(f"/api/v1/assets/{asset_a_id}").status_code == 404

    payload["name"] = "Company B laptop"
    created_b = client.post("/api/v1/assets", json=payload)
    assert created_b.status_code == 201
    assert created_b.json()["asset_tag"] == "SHARED-001"


def test_admin_can_create_users_and_viewer_cannot(client: TestClient) -> None:
    response = client.post(
        "/api/v1/users",
        json={
            "email": "new.user@example.com",
            "full_name": "New User",
            "password": "long-enough-password",
            "role": "viewer",
        },
    )
    assert response.status_code == 201
    assert response.json()["organization_id"] == 1

    use_role(client, "viewer")
    assert client.get("/api/v1/users").status_code == 403


def test_viewer_cannot_read_audit_log(client: TestClient) -> None:
    use_role(client, "viewer")
    assert client.get("/api/v1/audit-events").status_code == 403


def test_admin_cannot_demote_own_account(client: TestClient) -> None:
    current = client.get("/api/v1/auth/me").json()
    response = client.patch(
        f"/api/v1/users/{current['id']}",
        json={"role": "editor"},
    )
    assert response.status_code == 422


def test_deactivated_user_token_is_rejected(client: TestClient) -> None:
    users = client.get("/api/v1/users").json()
    editor = next(user for user in users if user["email"] == "editor.a@example.com")
    disabled = client.patch(
        f"/api/v1/users/{editor['id']}",
        json={"is_active": False},
    )
    assert disabled.status_code == 200
    use_role(client, "editor")
    assert client.get("/api/v1/assets").status_code == 401


def test_user_listing_is_organization_scoped(client: TestClient) -> None:
    use_role(client, "other_admin")
    users = client.get("/api/v1/users")
    assert users.status_code == 200
    assert [user["email"] for user in users.json()] == ["admin.b@example.com"]


def test_software_license_inventory_crud_and_seat_validation(client: TestClient) -> None:
    payload = {
        "name": "Office Suite",
        "publisher": "Example Publisher",
        "version": "2026",
        "license_type": "subscription",
        "license_reference": "CONTRACT-001",
        "seats_total": 25,
        "seats_used": 8,
        "purchased_at": "2026-01-01",
        "expires_at": "2027-01-01",
    }
    created = client.post("/api/v1/software-licenses", json=payload)
    assert created.status_code == 201
    license_id = created.json()["id"]

    listed = client.get("/api/v1/software-licenses")
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    invalid_update = client.patch(
        f"/api/v1/software-licenses/{license_id}",
        json={"seats_total": 4},
    )
    assert invalid_update.status_code == 422

    updated = client.patch(
        f"/api/v1/software-licenses/{license_id}",
        json={"seats_used": 10},
    )
    assert updated.status_code == 200
    assert updated.json()["seats_used"] == 10

    duplicate = client.post("/api/v1/software-licenses", json=payload)
    assert duplicate.status_code == 409

    assert client.delete(f"/api/v1/software-licenses/{license_id}").status_code == 204


def test_software_license_access_is_tenant_scoped_and_viewer_read_only(client: TestClient) -> None:
    payload = {
        "name": "Design Tool",
        "license_type": "perpetual",
        "license_reference": "DESIGN-REF-001",
        "seats_total": 3,
        "seats_used": 1,
    }
    created = client.post("/api/v1/software-licenses", json=payload)
    assert created.status_code == 201
    license_id = created.json()["id"]

    use_role(client, "viewer")
    assert client.get("/api/v1/software-licenses").status_code == 200
    assert client.post("/api/v1/software-licenses", json=payload).status_code == 403

    use_role(client, "other_admin")
    assert client.get("/api/v1/software-licenses").json() == []
    assert client.get(f"/api/v1/software-licenses/{license_id}").status_code == 404


def test_maintenance_request_creation_assignment_and_resolution(client: TestClient) -> None:
    asset = client.post(
        "/api/v1/assets",
        json={"name": "Server", "asset_tag": "SRV-MAINT-01", "category": "Server"},
    ).json()
    users = client.get("/api/v1/users").json()
    editor = next(user for user in users if user["email"] == "editor.a@example.com")
    viewer = next(user for user in users if user["email"] == "viewer.a@example.com")

    use_role(client, "viewer")
    created = client.post(
        "/api/v1/maintenance-requests",
        json={
            "asset_id": asset["id"],
            "title": "Server overheating",
            "description": "The server reports high temperature.",
            "priority": "high",
        },
    )
    assert created.status_code == 201
    request_id = created.json()["id"]
    assert created.json()["requested_by_user_id"] == viewer["id"]
    assert len(client.get("/api/v1/maintenance-requests").json()) == 1

    use_role(client, "editor")
    assigned = client.patch(
        f"/api/v1/maintenance-requests/{request_id}",
        json={"assigned_to_user_id": editor["id"], "status": "in_progress"},
    )
    assert assigned.status_code == 200
    assert assigned.json()["assigned_to_user_id"] == editor["id"]

    resolved = client.patch(
        f"/api/v1/maintenance-requests/{request_id}",
        json={"status": "resolved"},
    )
    assert resolved.status_code == 200
    assert resolved.json()["resolved_at"] is not None


def test_maintenance_requests_are_private_to_requester_and_organization(client: TestClient) -> None:
    asset = client.post(
        "/api/v1/assets",
        json={"name": "Workstation", "asset_tag": "PC-MAINT-01", "category": "Computer"},
    ).json()
    created = client.post(
        "/api/v1/maintenance-requests",
        json={
            "asset_id": asset["id"],
            "title": "Keyboard failure",
            "description": "Several keys are not responding.",
        },
    )
    assert created.status_code == 201
    request_id = created.json()["id"]

    use_role(client, "viewer")
    assert client.get(f"/api/v1/maintenance-requests/{request_id}").status_code == 404
    assert client.patch(
        f"/api/v1/maintenance-requests/{request_id}",
        json={"status": "resolved"},
    ).status_code == 403

    use_role(client, "other_admin")
    assert client.get("/api/v1/maintenance-requests").json() == []
    assert client.get(f"/api/v1/maintenance-requests/{request_id}").status_code == 404
    assert client.post(
        "/api/v1/maintenance-requests",
        json={
            "asset_id": asset["id"],
            "title": "Cross tenant",
            "description": "This should not be allowed.",
        },
    ).status_code == 404


def test_maintenance_assignment_cannot_cross_organizations(client: TestClient) -> None:
    asset = client.post(
        "/api/v1/assets",
        json={"name": "Network switch", "asset_tag": "SW-MAINT-01", "category": "Network"},
    ).json()
    request_record = client.post(
        "/api/v1/maintenance-requests",
        json={
            "asset_id": asset["id"],
            "title": "Port failure",
            "description": "One switch port is down.",
        },
    ).json()
    other_admin = client.app.state.test_tokens["other_admin"]
    use_role(client, "admin")
    users = client.get("/api/v1/users").json()
    other_id = 4
    assert other_admin
    response = client.patch(
        f"/api/v1/maintenance-requests/{request_record['id']}",
        json={"assigned_to_user_id": other_id},
    )
    assert response.status_code == 422
