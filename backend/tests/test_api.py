from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool

from app import main
from app.main import (
    Base, Organization, User, UserRole, app, create_access_token, get_session,
    password_hash,
)


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    monkeypatch.setattr(main, "SECRET_KEY", "test-only-secret-key-that-is-longer-than-32-bytes")
    monkeypatch.setattr(main, "login_rate_limiter", main.LoginRateLimiter(max_attempts=10, window_seconds=60))
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


def test_login_rate_limit_returns_429_after_ten_attempts(client: TestClient) -> None:
    for attempt in range(10):
        response = client.post(
            "/api/v1/auth/token",
            data={"username": "missing@example.com", "password": "wrong-password"},
        )
        assert response.status_code == 401, f"unexpected status on attempt {attempt + 1}"

    limited = client.post(
        "/api/v1/auth/token",
        data={"username": "admin.a@example.com", "password": "test-password-admin-123"},
    )
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) > 0
    assert "Too many login attempts" in limited.json()["detail"]


def test_login_rate_limit_cannot_be_bypassed_with_forwarded_header(client: TestClient) -> None:
    for _ in range(10):
        client.post(
            "/api/v1/auth/token",
            data={"username": "missing@example.com", "password": "wrong-password"},
        )

    other_ip = client.post(
        "/api/v1/auth/token",
        data={"username": "admin.a@example.com", "password": "test-password-admin-123"},
        headers={"X-Forwarded-For": "203.0.113.10"},
    )
    # Forwarded headers are intentionally not trusted by the application.
    assert other_ip.status_code == 429


def test_login_rate_limit_uses_nginx_client_ip_header(client: TestClient) -> None:
    for _ in range(10):
        client.post(
            "/api/v1/auth/token",
            data={"username": "missing@example.com", "password": "wrong-password"},
        )

    response = client.post(
        "/api/v1/auth/token",
        data={"username": "admin.a@example.com", "password": "test-password-admin-123"},
        headers={"X-Real-IP": "203.0.113.10"},
    )
    assert response.status_code == 200


def test_in_memory_login_rate_limiter_expires_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    limiter = main.LoginRateLimiter(max_attempts=1, window_seconds=60)
    clock = {"now": 100.0}
    monkeypatch.setattr(main.time, "monotonic", lambda: clock["now"])

    assert limiter.check("client-a") is None
    assert limiter.check("client-a") == 60
    clock["now"] += 61
    assert limiter.check("client-a") is None


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

    history = client.get(f"/api/v1/assets/{asset_id}/history")
    assert history.status_code == 200
    assert history.json()[0]["action"] == "asset.updated"
    assert history.json()[0]["details"]["changes"]["status"] == {
        "old": "active",
        "new": "in_repair",
    }

    events = client.get("/api/v1/audit-events")
    assert events.status_code == 200
    assert [event["action"] for event in events.json()] == ["asset.updated", "asset.created"]

    deleted = client.delete(f"/api/v1/assets/{asset_id}")
    assert deleted.status_code == 204
    assert client.get(f"/api/v1/assets/{asset_id}").status_code == 404


def test_asset_delete_referenced_by_maintenance_returns_conflict_without_audit(client: TestClient) -> None:
    created = client.post(
        "/api/v1/assets",
        json={"name": "Managed server", "asset_tag": "DEL-REF-001", "category": "Server"},
    )
    assert created.status_code == 201
    asset_id = created.json()["id"]

    maintenance = client.post(
        "/api/v1/maintenance-requests",
        json={
            "asset_id": asset_id,
            "title": "Keep server online",
            "description": "The asset is still referenced by an open maintenance request",
        },
    )
    assert maintenance.status_code == 201

    deleted = client.delete(f"/api/v1/assets/{asset_id}")
    assert deleted.status_code == 409
    assert deleted.json()["detail"] == (
        "Asset is referenced by maintenance requests and cannot be deleted"
    )
    assert client.get(f"/api/v1/assets/{asset_id}").status_code == 200

    actions = [event["action"] for event in client.get("/api/v1/audit-events").json()]
    assert actions.count("asset.deleted") == 0
    assert actions.count("asset.created") == 1
    assert actions.count("maintenance_request.created") == 1


def test_asset_history_is_tenant_scoped_and_readable_by_viewer(client: TestClient) -> None:
    created = client.post(
        "/api/v1/assets",
        json={"name": "History laptop", "asset_tag": "HIST-001", "category": "Laptop"},
    )
    asset_id = created.json()["id"]
    assert client.patch(f"/api/v1/assets/{asset_id}", json={"name": "Renamed laptop"}).status_code == 200

    use_role(client, "viewer")
    history = client.get(f"/api/v1/assets/{asset_id}/history")
    assert history.status_code == 200
    assert history.json()[0]["details"]["changes"]["name"] == {
        "old": "History laptop",
        "new": "Renamed laptop",
    }

    use_role(client, "other_admin")
    assert client.get(f"/api/v1/assets/{asset_id}/history").status_code == 404


def test_bulk_asset_update_changes_assets_and_records_each_history(client: TestClient) -> None:
    ids = []
    for index in range(2):
        created = client.post(
            "/api/v1/assets",
            json={"name": f"Bulk laptop {index}", "asset_tag": f"BULK-{index}", "category": "Laptop"},
        )
        assert created.status_code == 201
        ids.append(created.json()["id"])

    response = client.patch(
        "/api/v1/assets/bulk",
        json={"asset_ids": ids, "status": "in_repair", "assigned_to": "Alex Smith"},
    )
    assert response.status_code == 200
    result = response.json()
    assert result["selected"] == 2
    assert result["updated"] == 2
    assert all(item["status"] == "in_repair" for item in result["assets"])
    assert all(item["assigned_to"] == "Alex Smith" for item in result["assets"])

    for asset_id in ids:
        history = client.get(f"/api/v1/assets/{asset_id}/history")
        assert history.status_code == 200
        event = history.json()[0]
        assert event["action"] == "asset.updated"
        assert event["details"]["source"] == "bulk"
        assert event["details"]["changes"]["status"] == {"old": "active", "new": "in_repair"}
        assert event["details"]["changes"]["assigned_to"] == {"old": None, "new": "Alex Smith"}


def test_bulk_asset_update_unchanged_values_are_noop(client: TestClient) -> None:
    created = client.post(
        "/api/v1/assets",
        json={"name": "No-op laptop", "asset_tag": "BULK-NOOP", "category": "Laptop"},
    )
    asset_id = created.json()["id"]
    response = client.patch(
        "/api/v1/assets/bulk",
        json={"asset_ids": [asset_id], "status": "active", "assigned_to": None},
    )
    assert response.status_code == 200
    assert response.json()["updated"] == 0
    history = client.get(f"/api/v1/assets/{asset_id}/history")
    assert not any(event["action"] == "asset.updated" for event in history.json())


def test_bulk_asset_update_validates_ids_and_fields(client: TestClient) -> None:
    for payload in (
        {"asset_ids": [] , "status": "active"},
        {"asset_ids": [1, 1], "status": "active"},
        {"asset_ids": [1], "status": None, "assigned_to": "A"},
        {"asset_ids": [1]},
        {"asset_ids": list(range(1, 102)), "status": "active"},
    ):
        assert client.patch("/api/v1/assets/bulk", json=payload).status_code == 422


def test_bulk_asset_update_requires_editor_or_admin(client: TestClient) -> None:
    use_role(client, "viewer")
    response = client.patch(
        "/api/v1/assets/bulk",
        json={"asset_ids": [1], "status": "retired"},
    )
    assert response.status_code == 403


def test_bulk_asset_update_rejects_foreign_tenant_without_partial_changes(client: TestClient) -> None:
    first = client.post(
        "/api/v1/assets",
        json={"name": "Tenant A laptop", "asset_tag": "TENANT-A-1", "category": "Laptop"},
    ).json()
    use_role(client, "other_admin")
    foreign = client.post(
        "/api/v1/assets",
        json={"name": "Tenant B laptop", "asset_tag": "TENANT-B-1", "category": "Laptop"},
    ).json()
    use_role(client, "admin")

    response = client.patch(
        "/api/v1/assets/bulk",
        json={"asset_ids": [first["id"], foreign["id"]], "status": "retired"},
    )
    assert response.status_code == 404
    unchanged = client.get(f"/api/v1/assets/{first['id']}")
    assert unchanged.status_code == 200
    assert unchanged.json()["status"] == "active"
    assert not any(event["action"] == "asset.updated" for event in client.get(f"/api/v1/assets/{first['id']}/history").json())


def test_asset_csv_import_export_and_formula_safety(client: TestClient) -> None:
    csv_data = (
        "name,asset_tag,category,manufacturer,model,serial_number,assigned_to,status\n"
        "Imported laptop,CSV-001,Laptop,Example,Model A,SN-CSV-001,Alex,active\n"
        'Formula device,CSV-002,Laptop,"=HYPERLINK(""https://example.invalid"")",,,,active\n'
    )
    imported = client.post(
        "/api/v1/assets/import",
        files={"file": ("assets.csv", csv_data.encode("utf-8"), "text/csv")},
    )
    assert imported.status_code == 200
    assert imported.json()["created"] == 2
    assert imported.json()["skipped"] == 0

    duplicate = client.post(
        "/api/v1/assets/import",
        files={"file": ("assets.csv", csv_data.encode("utf-8"), "text/csv")},
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["created"] == 0
    assert duplicate.json()["skipped"] == 2

    exported = client.get("/api/v1/assets/export")
    assert exported.status_code == 200
    assert "CSV-001" in exported.text
    assert "'=HYPERLINK" in exported.text


def test_asset_csv_import_is_role_and_tenant_scoped(client: TestClient) -> None:
    payload = "name,asset_tag,category\nScoped laptop,CSV-SCOPE,Laptop\n"
    use_role(client, "viewer")
    denied = client.post(
        "/api/v1/assets/import",
        files={"file": ("assets.csv", payload.encode(), "text/csv")},
    )
    assert denied.status_code == 403

    use_role(client, "other_admin")
    imported = client.post(
        "/api/v1/assets/import",
        files={"file": ("assets.csv", payload.encode(), "text/csv")},
    )
    assert imported.status_code == 200
    assert imported.json()["created"] == 1
    assert client.get("/api/v1/assets").json()[0]["asset_tag"] == "CSV-SCOPE"


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


def test_user_create_integrity_conflict_returns_409_without_audit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy.exc import IntegrityError

    def fail_flush(self, objects=None):
        raise IntegrityError("insert user", {}, Exception("unique constraint"))

    monkeypatch.setattr(main.Session, "flush", fail_flush)
    response = client.post(
        "/api/v1/users",
        json={
            "email": "race.user@example.com",
            "full_name": "Race User",
            "password": "long-enough-password",
            "role": "viewer",
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "Email is already registered"

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



def test_software_license_update_integrity_error_rolls_back_without_audit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = client.post(
        "/api/v1/software-licenses",
        json={
            "name": "Original License",
            "license_type": "subscription",
            "license_reference": "UPDATE-ROLLBACK-LICENSE-001",
            "seats_total": 10,
            "seats_used": 2,
        },
    )
    assert created.status_code == 201
    license_id = created.json()["id"]
    existing_audit_ids = {
        event["id"] for event in client.get("/api/v1/audit-events").json()
    }
    original_record_audit = main.record_audit

    def fail_license_update_audit(session, user, action, entity_type, entity_id, details=None):
        if action == "software_license.updated":
            raise IntegrityError("audit insert", {}, Exception("simulated integrity conflict"))
        return original_record_audit(session, user, action, entity_type, entity_id, details)

    monkeypatch.setattr(main, "record_audit", fail_license_update_audit)
    response = client.patch(
        f"/api/v1/software-licenses/{license_id}",
        json={"name": "Partially Updated License"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Software license update conflicts with current organization records"
    )
    stored = client.get(f"/api/v1/software-licenses/{license_id}")
    assert stored.status_code == 200
    assert stored.json()["name"] == "Original License"
    audit_ids = {
        event["id"] for event in client.get("/api/v1/audit-events").json()
    }
    assert audit_ids == existing_audit_ids


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
    use_role(client, "other_admin")
    other_id = client.get("/api/v1/auth/me").json()["id"]
    use_role(client, "admin")
    response = client.patch(
        f"/api/v1/maintenance-requests/{request_record['id']}",
        json={"assigned_to_user_id": other_id},
    )
    assert response.status_code == 422


def test_department_and_employee_directory_crud(client: TestClient) -> None:
    department = client.post(
        "/api/v1/departments",
        json={"name": "Operations", "code": "ops"},
    )
    assert department.status_code == 201
    department_id = department.json()["id"]
    assert department.json()["code"] == "OPS"

    employee = client.post(
        "/api/v1/employees",
        json={
            "employee_number": "EMP-001",
            "full_name": "Alex Employee",
            "department_id": department_id,
            "email": "alex.employee@example.com",
            "job_title": "Systems Engineer",
        },
    )
    assert employee.status_code == 201
    employee_id = employee.json()["id"]

    use_role(client, "editor")
    assert len(client.get("/api/v1/departments").json()) == 1
    assert len(client.get("/api/v1/employees?search=Alex").json()) == 1
    assert client.post(
        "/api/v1/departments",
        json={"name": "Finance", "code": "FIN"},
    ).status_code == 403

    updated = client.patch(
        f"/api/v1/employees/{employee_id}",
        json={"job_title": "Senior Systems Engineer"},
    )
    assert updated.status_code == 200
    assert updated.json()["job_title"] == "Senior Systems Engineer"

    use_role(client, "viewer")
    assert client.get("/api/v1/employees").status_code == 403
    assert client.get("/api/v1/departments").status_code == 403


def test_department_and_employee_tenant_isolation(client: TestClient) -> None:
    department = client.post(
        "/api/v1/departments",
        json={"name": "Engineering", "code": "ENG"},
    ).json()

    use_role(client, "other_admin")
    assert client.get("/api/v1/departments").json() == []
    assert client.get("/api/v1/employees").json() == []
    other_department = client.post(
        "/api/v1/departments",
        json={"name": "Engineering", "code": "ENG"},
    )
    assert other_department.status_code == 201
    other_department_id = other_department.json()["id"]

    use_role(client, "admin")
    cross_org_employee = client.post(
        "/api/v1/employees",
        json={
            "employee_number": "EMP-X",
            "full_name": "Cross Organization",
            "department_id": other_department_id,
        },
    )
    assert cross_org_employee.status_code == 422



def test_password_change_revokes_existing_access_token(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/change-password",
        json={
            "current_password": "test-password-admin-123",
            "new_password": "replacement-password-456",
        },
    )
    assert response.status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 401


def test_disabling_and_reenabling_user_revokes_old_access_token(client: TestClient) -> None:
    editor_token = client.app.state.test_tokens["editor"]
    editor_id = next(user["id"] for user in client.get("/api/v1/users").json() if user["email"] == "editor.a@example.com")
    response = client.patch(
        f"/api/v1/users/{editor_id}",
        json={"is_active": False},
    )
    assert response.status_code == 200
    response = client.patch(
        f"/api/v1/users/{editor_id}",
        json={"is_active": True},
    )
    assert response.status_code == 200

    client.headers.update({"Authorization": f"Bearer {editor_token}"})
    assert client.get("/api/v1/auth/me").status_code == 401


def test_cross_tenant_write_endpoints_and_audit_are_isolated(client: TestClient) -> None:
    # Create related records in Company A.
    department_a = client.post(
        "/api/v1/departments", json={"name": "A Operations", "code": "A-OPS"}
    ).json()
    employee_a = client.post(
        "/api/v1/employees",
        json={"employee_number": "A-EMP-01", "full_name": "Employee A",
              "department_id": department_a["id"]},
    ).json()
    license_a = client.post(
        "/api/v1/software-licenses",
        json={"name": "A License", "license_type": "perpetual",
              "license_reference": "A-LIC-01", "seats_total": 5, "seats_used": 1},
    ).json()
    asset_a = client.post(
        "/api/v1/assets",
        json={"name": "A Server", "asset_tag": "A-SRV-01", "category": "Server"},
    ).json()
    maintenance_a = client.post(
        "/api/v1/maintenance-requests",
        json={"asset_id": asset_a["id"], "title": "A maintenance",
              "description": "Company A maintenance request"},
    ).json()

    # Create equivalent records in Company B.
    use_role(client, "other_admin")
    user_b_id = client.get("/api/v1/auth/me").json()["id"]
    department_b = client.post(
        "/api/v1/departments", json={"name": "B Operations", "code": "B-OPS"}
    ).json()
    employee_b = client.post(
        "/api/v1/employees",
        json={"employee_number": "B-EMP-01", "full_name": "Employee B",
              "department_id": department_b["id"]},
    ).json()
    license_b = client.post(
        "/api/v1/software-licenses",
        json={"name": "B License", "license_type": "perpetual",
              "license_reference": "B-LIC-01", "seats_total": 7, "seats_used": 2},
    ).json()
    asset_b = client.post(
        "/api/v1/assets",
        json={"name": "B Server", "asset_tag": "B-SRV-01", "category": "Server"},
    ).json()
    maintenance_b = client.post(
        "/api/v1/maintenance-requests",
        json={"asset_id": asset_b["id"], "title": "B maintenance",
              "description": "Company B maintenance request"},
    ).json()

    # Company A must not read, modify, or delete Company B records by guessing IDs.
    use_role(client, "admin")
    assert client.patch(
        f"/api/v1/departments/{department_b['id']}", json={"name": "Hijacked"}
    ).status_code == 404
    assert client.delete(f"/api/v1/departments/{department_b['id']}").status_code == 404
    assert client.patch(
        f"/api/v1/employees/{employee_b['id']}", json={"full_name": "Hijacked"}
    ).status_code == 404
    assert client.delete(f"/api/v1/employees/{employee_b['id']}").status_code == 404
    assert client.get(f"/api/v1/software-licenses/{license_b['id']}").status_code == 404
    assert client.patch(
        f"/api/v1/software-licenses/{license_b['id']}", json={"name": "Hijacked"}
    ).status_code == 404
    assert client.delete(f"/api/v1/software-licenses/{license_b['id']}").status_code == 404
    assert client.get(
        f"/api/v1/maintenance-requests/{maintenance_b['id']}"
    ).status_code == 404
    assert client.patch(
        f"/api/v1/maintenance-requests/{maintenance_b['id']}",
        json={"title": "Hijacked"},
    ).status_code == 404

    # Verify Company B data was not changed or removed.
    use_role(client, "other_admin")
    assert client.get(f"/api/v1/departments").json()[0]["name"] == "B Operations"
    assert client.get(f"/api/v1/employees").json()[0]["full_name"] == "Employee B"
    assert client.get(f"/api/v1/software-licenses/{license_b['id']}").json()["name"] == "B License"
    assert client.get(
        f"/api/v1/maintenance-requests/{maintenance_b['id']}"
    ).json()["title"] == "B maintenance"

    # Audit results must contain only events attributed to the current organization.
    use_role(client, "admin")
    events_a = client.get("/api/v1/audit-events").json()
    assert events_a
    assert all(event["actor_user_id"] != user_b_id for event in events_a)
    # Only assert entity types that currently emit creation audit events.
    assert any(event["entity_id"] == str(asset_a["id"]) for event in events_a)
    assert any(event["entity_id"] == str(maintenance_a["id"]) for event in events_a)



def test_asset_csv_export_never_includes_other_tenant_assets(client: TestClient) -> None:
    asset_a = client.post(
        "/api/v1/assets",
        json={"name": "Company A export", "asset_tag": "EXPORT-A-001", "category": "Laptop"},
    )
    assert asset_a.status_code == 201

    use_role(client, "other_admin")
    asset_b = client.post(
        "/api/v1/assets",
        json={"name": "Company B export", "asset_tag": "EXPORT-B-001", "category": "Laptop"},
    )
    assert asset_b.status_code == 201

    export_b = client.get("/api/v1/assets/export")
    assert export_b.status_code == 200
    assert "EXPORT-B-001" in export_b.text
    assert "EXPORT-A-001" not in export_b.text

    use_role(client, "admin")
    export_a = client.get("/api/v1/assets/export")
    assert export_a.status_code == 200
    assert "EXPORT-A-001" in export_a.text
    assert "EXPORT-B-001" not in export_a.text



def test_asset_csv_import_export_cannot_cross_tenant_boundaries(client: TestClient) -> None:
    asset_a = client.post(
        "/api/v1/assets",
        json={"name": "A Exported asset", "asset_tag": "CSV-TENANT-A", "category": "Laptop"},
    )
    assert asset_a.status_code == 201

    use_role(client, "other_admin")
    asset_b = client.post(
        "/api/v1/assets",
        json={"name": "B Private asset", "asset_tag": "CSV-TENANT-B", "category": "Server"},
    )
    assert asset_b.status_code == 201

    # A's export must not disclose Company B's inventory.
    use_role(client, "admin")
    exported = client.get("/api/v1/assets/export")
    assert exported.status_code == 200
    assert "CSV-TENANT-A" in exported.text
    assert "CSV-TENANT-B" not in exported.text

    # Import cannot accept a client-supplied organization identifier.
    malicious_csv = (
        "name,asset_tag,category,organization_id\\n"
        "Injected asset,CSV-INJECTED,Laptop,2\\n"
    )
    rejected = client.post(
        "/api/v1/assets/import",
        files={"file": ("assets.csv", malicious_csv.encode("utf-8"), "text/csv")},
    )
    assert rejected.status_code == 422
    assert all(item["asset_tag"] != "CSV-INJECTED" for item in client.get("/api/v1/assets").json())

    use_role(client, "other_admin")
    assert client.get("/api/v1/assets").json()[0]["asset_tag"] == "CSV-TENANT-B"



def test_admin_cannot_disable_or_demote_own_account(client: TestClient) -> None:
    current_id = client.get("/api/v1/auth/me").json()["id"]

    disabled = client.patch(
        f"/api/v1/users/{current_id}",
        json={"is_active": False},
    )
    assert disabled.status_code == 422
    assert client.get("/api/v1/auth/me").status_code == 200

    demoted = client.patch(
        f"/api/v1/users/{current_id}",
        json={"role": "editor"},
    )
    assert demoted.status_code == 422
    assert client.get("/api/v1/auth/me").status_code == 200


def test_admin_user_management_cannot_cross_tenant_or_assign_tenant(client: TestClient) -> None:
    use_role(client, "other_admin")
    foreign_user_id = client.get("/api/v1/auth/me").json()["id"]

    use_role(client, "admin")
    assert client.patch(
        f"/api/v1/users/{foreign_user_id}",
        json={"full_name": "Cross-tenant takeover"},
    ).status_code == 404

    created = client.post(
        "/api/v1/users",
        json={
            "email": "tenant-bound-user@example.com",
            "full_name": "Tenant Bound User",
            "password": "long-enough-password",
            "role": "viewer",
            "organization_id": 2,
        },
    )
    assert created.status_code == 201
    assert created.json()["organization_id"] == client.get("/api/v1/auth/me").json()["organization_id"]

    use_role(client, "other_admin")
    assert all(
        user["email"] != "tenant-bound-user@example.com"
        for user in client.get("/api/v1/users").json()
    )


def test_related_records_reject_foreign_departments_and_inactive_assignees(client: TestClient) -> None:
    department_a = client.post(
        "/api/v1/departments",
        json={"name": "A Department", "code": "A-DEPT"},
    ).json()
    employee = client.post(
        "/api/v1/employees",
        json={
            "employee_number": "BOUNDARY-EMP-A",
            "full_name": "Boundary Employee",
            "department_id": department_a["id"],
        },
    )
    assert employee.status_code == 201

    asset = client.post(
        "/api/v1/assets",
        json={"name": "Boundary Asset", "asset_tag": "BOUNDARY-ASSET-A", "category": "Server"},
    )
    assert asset.status_code == 201
    maintenance = client.post(
        "/api/v1/maintenance-requests",
        json={
            "asset_id": asset.json()["id"],
            "title": "Boundary maintenance",
            "description": "Validate active assignee constraint",
        },
    )
    assert maintenance.status_code == 201

    users = client.get("/api/v1/users").json()
    editor = next(user for user in users if user["email"] == "editor.a@example.com")
    deactivated = client.patch(
        f"/api/v1/users/{editor['id']}",
        json={"is_active": False},
    )
    assert deactivated.status_code == 200

    use_role(client, "other_admin")
    department_b = client.post(
        "/api/v1/departments",
        json={"name": "B Department", "code": "B-DEPT"},
    )
    assert department_b.status_code == 201
    department_b_id = department_b.json()["id"]

    use_role(client, "admin")
    reassignment = client.patch(
        f"/api/v1/employees/{employee.json()['id']}",
        json={"department_id": department_b_id},
    )
    assert reassignment.status_code == 422

    assignment = client.patch(
        f"/api/v1/maintenance-requests/{maintenance.json()['id']}",
        json={"assigned_to_user_id": editor["id"]},
    )
    assert assignment.status_code == 422

    # Rejected relationship changes must leave the original records intact.
    assert client.get("/api/v1/employees").json()[0]["department_id"] == department_a["id"]
    assert client.get(
        f"/api/v1/maintenance-requests/{maintenance.json()['id']}"
    ).json()["assigned_to_user_id"] is None



def test_unchanged_user_update_does_not_create_audit_event(client: TestClient) -> None:
    users = client.get("/api/v1/users")
    assert users.status_code == 200
    target = next(user for user in users.json() if user["email"] == "editor.a@example.com")

    before = client.get("/api/v1/audit-events")
    assert before.status_code == 200
    before_ids = [event["id"] for event in before.json()]

    response = client.patch(
        f"/api/v1/users/{target['id']}",
        json={"full_name": f" {target['full_name']} ", "role": target["role"], "is_active": target["is_active"]},
    )
    assert response.status_code == 200
    assert response.json()["full_name"] == target["full_name"]

    after = client.get("/api/v1/audit-events")
    assert after.status_code == 200
    assert [event["id"] for event in after.json()] == before_ids


def test_empty_user_update_does_not_create_audit_event(client: TestClient) -> None:
    before = client.get("/api/v1/audit-events")
    assert before.status_code == 200
    before_ids = [event["id"] for event in before.json()]

    users = client.get("/api/v1/users")
    assert users.status_code == 200
    target = next(user for user in users.json() if user["email"] == "editor.a@example.com")

    response = client.patch(f"/api/v1/users/{target['id']}", json={})
    assert response.status_code == 200
    assert response.json()["id"] == target["id"]

    after = client.get("/api/v1/audit-events")
    assert after.status_code == 200
    assert [event["id"] for event in after.json()] == before_ids


def test_password_change_allows_new_password_and_rejects_old_password(client: TestClient) -> None:
    changed = client.post(
        "/api/v1/auth/change-password",
        json={
            "current_password": "test-password-admin-123",
            "new_password": "replacement-password-456",
        },
    )
    assert changed.status_code == 200

    old_login = client.post(
        "/api/v1/auth/token",
        data={"username": "admin.a@example.com", "password": "test-password-admin-123"},
    )
    assert old_login.status_code == 401

    new_login = client.post(
        "/api/v1/auth/token",
        data={"username": "admin.a@example.com", "password": "replacement-password-456"},
    )
    assert new_login.status_code == 200
    assert new_login.json()["token_type"] == "bearer"
    client.headers.update({"Authorization": f"Bearer {new_login.json()['access_token']}"})
    assert client.get("/api/v1/auth/me").status_code == 200

    audit = client.get("/api/v1/audit-events")
    assert audit.status_code == 200
    password_events = [
        event for event in audit.json()
        if event["action"] == "user.password_changed"
    ]
    assert len(password_events) == 1
    assert password_events[0]["entity_type"] == "user"
    assert password_events[0]["details"] == {}
    serialized_event = str(password_events[0])
    assert "test-password-admin-123" not in serialized_event
    assert "replacement-password-456" not in serialized_event


def test_failed_password_change_does_not_create_audit_event(client: TestClient) -> None:
    before = client.get("/api/v1/audit-events")
    assert before.status_code == 200
    before_ids = [event["id"] for event in before.json()]

    response = client.post(
        "/api/v1/auth/change-password",
        json={
            "current_password": "incorrect-current-password",
            "new_password": "unused-replacement-password-789",
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Current password is incorrect"

    after = client.get("/api/v1/audit-events")
    assert after.status_code == 200
    assert [event["id"] for event in after.json()] == before_ids



def test_asset_creation_audit_records_actor_and_consistent_entity_details(client: TestClient) -> None:
    current_user = client.get("/api/v1/auth/me")
    assert current_user.status_code == 200
    actor_id = current_user.json()["id"]

    created = client.post(
        "/api/v1/assets",
        json={
            "name": "Audit Attribution Server",
            "asset_tag": "AUDIT-ACTOR-001",
            "category": "Server",
            "status": "active",
        },
    )
    assert created.status_code == 201
    asset_id = created.json()["id"]

    response = client.get("/api/v1/audit-events")
    assert response.status_code == 200
    matching = [
        event for event in response.json()
        if event["action"] == "asset.created"
        and event["entity_type"] == "asset"
        and event["entity_id"] == str(asset_id)
    ]
    assert len(matching) == 1
    event = matching[0]
    assert event["actor_user_id"] == actor_id
    assert event["details"] == {
        "asset_tag": "AUDIT-ACTOR-001",
        "name": "Audit Attribution Server",
        "category": "Server",
        "status": "active",
    }


def test_failed_department_delete_does_not_create_audit_event(client: TestClient) -> None:
    department = client.post(
        "/api/v1/departments",
        json={"name": "Protected Department", "code": "PROTECTED"},
    )
    assert department.status_code == 201
    department_id = department.json()["id"]

    employee = client.post(
        "/api/v1/employees",
        json={
            "employee_number": "PROTECTED-EMP-001",
            "full_name": "Assigned Employee",
            "department_id": department_id,
        },
    )
    assert employee.status_code == 201

    before = client.get("/api/v1/audit-events")
    assert before.status_code == 200
    before_ids = [event["id"] for event in before.json()]

    rejected = client.delete(f"/api/v1/departments/{department_id}")
    assert rejected.status_code == 409

    after = client.get("/api/v1/audit-events")
    assert after.status_code == 200
    assert [event["id"] for event in after.json()] == before_ids

    still_present = client.get("/api/v1/departments")
    assert still_present.status_code == 200
    assert any(item["id"] == department_id for item in still_present.json())


def test_duplicate_asset_creation_does_not_create_audit_event(client: TestClient) -> None:
    original = client.post(
        "/api/v1/assets",
        json={
            "name": "Original Audit Asset",
            "asset_tag": "AUDIT-DUPLICATE-001",
            "category": "Server",
        },
    )
    assert original.status_code == 201
    original_id = original.json()["id"]

    before = client.get("/api/v1/audit-events")
    assert before.status_code == 200
    before_ids = [event["id"] for event in before.json()]

    duplicate = client.post(
        "/api/v1/assets",
        json={
            "name": "Duplicate Audit Asset",
            "asset_tag": "AUDIT-DUPLICATE-001",
            "category": "Laptop",
        },
    )
    assert duplicate.status_code == 409

    after = client.get("/api/v1/audit-events")
    assert after.status_code == 200
    assert [event["id"] for event in after.json()] == before_ids

    assets = client.get("/api/v1/assets")
    assert assets.status_code == 200
    matching = [item for item in assets.json() if item["asset_tag"] == "AUDIT-DUPLICATE-001"]
    assert len(matching) == 1
    assert matching[0]["id"] == original_id
    assert matching[0]["name"] == "Original Audit Asset"


def test_audit_events_are_isolated_between_organizations(client: TestClient) -> None:
    asset_a = client.post(
        "/api/v1/assets",
        json={"name": "Tenant A Audit Asset", "asset_tag": "AUDIT-TENANT-A-001", "category": "Server"},
    )
    assert asset_a.status_code == 201
    asset_a_id = str(asset_a.json()["id"])

    events_a_before = client.get("/api/v1/audit-events")
    assert events_a_before.status_code == 200
    assert any(
        event["action"] == "asset.created" and event["entity_id"] == asset_a_id
        for event in events_a_before.json()
    )

    use_role(client, "other_admin")
    asset_b = client.post(
        "/api/v1/assets",
        json={"name": "Tenant B Audit Asset", "asset_tag": "AUDIT-TENANT-B-001", "category": "Laptop"},
    )
    assert asset_b.status_code == 201
    asset_b_id = str(asset_b.json()["id"])

    events_b = client.get("/api/v1/audit-events")
    assert events_b.status_code == 200
    assert any(
        event["action"] == "asset.created" and event["entity_id"] == asset_b_id
        for event in events_b.json()
    )
    assert all(event["entity_id"] != asset_a_id for event in events_b.json())

    use_role(client, "admin")
    events_a_after = client.get("/api/v1/audit-events")
    assert events_a_after.status_code == 200
    assert any(
        event["action"] == "asset.created" and event["entity_id"] == asset_a_id
        for event in events_a_after.json()
    )
    assert all(event["entity_id"] != asset_b_id for event in events_a_after.json())



def test_audit_event_list_cannot_be_scoped_to_another_organization_by_query(client: TestClient) -> None:
    asset_a = client.post(
        "/api/v1/assets",
        json={"name": "Query Tenant A Asset", "asset_tag": "AUDIT-QUERY-A-001", "category": "Server"},
    )
    assert asset_a.status_code == 201
    asset_a_id = str(asset_a.json()["id"])

    use_role(client, "other_admin")
    asset_b = client.post(
        "/api/v1/assets",
        json={"name": "Query Tenant B Asset", "asset_tag": "AUDIT-QUERY-B-001", "category": "Laptop"},
    )
    assert asset_b.status_code == 201
    asset_b_id = str(asset_b.json()["id"])

    # The authenticated user's organization is authoritative; a caller-supplied
    # organization_id query parameter must never expose another tenant's events.
    response = client.get("/api/v1/audit-events", params={"organization_id": 1})
    assert response.status_code == 200
    event_ids = {event["entity_id"] for event in response.json()}
    assert asset_b_id in event_ids
    assert asset_a_id not in event_ids



def test_audit_creation_details_exclude_sensitive_license_and_maintenance_fields(client: TestClient) -> None:
    asset = client.post(
        "/api/v1/assets",
        json={
            "name": "Audit Privacy Server",
            "asset_tag": "AUDIT-PRIVACY-SERVER",
            "category": "Server",
        },
    )
    assert asset.status_code == 201

    license_payload = {
        "name": "Confidential Enterprise Suite",
        "publisher": "Private Publisher",
        "version": "2026.11",
        "license_type": "subscription",
        "license_reference": "PRIVATE-CONTRACT-REFERENCE-DO-NOT-LOG",
        "seats_total": 50,
        "seats_used": 12,
    }
    license_response = client.post("/api/v1/software-licenses", json=license_payload)
    assert license_response.status_code == 201
    license_id = str(license_response.json()["id"])

    maintenance_payload = {
        "asset_id": asset.json()["id"],
        "title": "Confidential maintenance request",
        "description": "PRIVATE-MAINTENANCE-DESCRIPTION-DO-NOT-LOG",
        "priority": "high",
    }
    maintenance_response = client.post(
        "/api/v1/maintenance-requests",
        json=maintenance_payload,
    )
    assert maintenance_response.status_code == 201
    maintenance_id = str(maintenance_response.json()["id"])

    audit = client.get("/api/v1/audit-events")
    assert audit.status_code == 200
    events = audit.json()

    license_events = [
        event for event in events
        if event["action"] == "software_license.created"
        and event["entity_id"] == license_id
    ]
    assert len(license_events) == 1
    assert license_events[0]["details"] == {
        "name": "Confidential Enterprise Suite",
        "license_type": "subscription",
    }

    maintenance_events = [
        event for event in events
        if event["action"] == "maintenance_request.created"
        and event["entity_id"] == maintenance_id
    ]
    assert len(maintenance_events) == 1
    assert maintenance_events[0]["details"] == {
        "asset_id": asset.json()["id"],
        "priority": "high",
    }

    serialized_audit = str(events)
    for sensitive_value in (
        "PRIVATE-CONTRACT-REFERENCE-DO-NOT-LOG",
        "Private Publisher",
        "PRIVATE-MAINTENANCE-DESCRIPTION-DO-NOT-LOG",
        "Confidential maintenance request",
    ):
        assert sensitive_value not in serialized_audit


def test_noop_department_update_does_not_create_audit_event(client: TestClient) -> None:
    created = client.post(
        "/api/v1/departments",
        json={"name": "No-op Department", "code": "NOOP"},
    )
    assert created.status_code == 201
    department_id = created.json()["id"]

    before = client.get("/api/v1/audit-events")
    assert before.status_code == 200
    before_ids = [event["id"] for event in before.json()]

    updated = client.patch(
        f"/api/v1/departments/{department_id}",
        json={"name": "No-op Department", "code": "NOOP"},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "No-op Department"
    assert updated.json()["code"] == "NOOP"

    after = client.get("/api/v1/audit-events")
    assert after.status_code == 200
    assert [event["id"] for event in after.json()] == before_ids



def test_employee_update_integrity_error_rolls_back_without_audit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = client.post(
        "/api/v1/employees",
        json={
            "employee_number": "EMP-UPDATE-ROLLBACK-001",
            "full_name": "Original Employee",
            "email": "employee.update.rollback@example.com",
        },
    )
    assert created.status_code == 201
    employee_id = created.json()["id"]
    existing_audit_ids = {
        event["id"] for event in client.get("/api/v1/audit-events").json()
    }
    original_record_audit = main.record_audit

    def fail_employee_update_audit(session, user, action, entity_type, entity_id, details=None):
        if action == "employee.updated":
            raise IntegrityError("audit insert", {}, Exception("simulated integrity conflict"))
        return original_record_audit(session, user, action, entity_type, entity_id, details)

    monkeypatch.setattr(main, "record_audit", fail_employee_update_audit)
    response = client.patch(
        f"/api/v1/employees/{employee_id}",
        json={"full_name": "Partially Updated Employee"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Employee update conflicts with current organization records"
    )
    stored = next(
        employee for employee in client.get("/api/v1/employees").json()
        if employee["id"] == employee_id
    )
    assert stored["full_name"] == "Original Employee"
    audit_ids = {
        event["id"] for event in client.get("/api/v1/audit-events").json()
    }
    assert audit_ids == existing_audit_ids


def test_noop_employee_update_does_not_create_audit_event(client: TestClient) -> None:
    created = client.post(
        "/api/v1/employees",
        json={"employee_number": "NOOP-EMP-001", "full_name": "No-op Employee"},
    )
    assert created.status_code == 201
    employee_id = created.json()["id"]

    before = client.get("/api/v1/audit-events")
    assert before.status_code == 200
    before_ids = [event["id"] for event in before.json()]

    updated = client.patch(
        f"/api/v1/employees/{employee_id}",
        json={"employee_number": "NOOP-EMP-001", "full_name": "No-op Employee"},
    )
    assert updated.status_code == 200
    assert updated.json()["employee_number"] == "NOOP-EMP-001"

    after = client.get("/api/v1/audit-events")
    assert after.status_code == 200
    assert [event["id"] for event in after.json()] == before_ids

def test_noop_maintenance_update_does_not_create_audit_event(client: TestClient) -> None:
    asset = client.post(
        "/api/v1/assets",
        json={"name": "Maintenance host", "asset_tag": "MAINT-NOOP-01", "category": "Server"},
    ).json()
    created = client.post(
        "/api/v1/maintenance-requests",
        json={
            "asset_id": asset["id"],
            "title": "Check cooling",
            "description": "Inspect the cooling system.",
        },
    )
    assert created.status_code == 201
    request_id = created.json()["id"]

    before = client.get("/api/v1/audit-events")
    assert before.status_code == 200
    before_ids = [event["id"] for event in before.json()]

    unchanged = client.patch(
        f"/api/v1/maintenance-requests/{request_id}",
        json={"title": "Check cooling", "status": "open"},
    )
    assert unchanged.status_code == 200
    assert unchanged.json()["resolved_at"] is None

    after = client.get("/api/v1/audit-events")
    assert [event["id"] for event in after.json()] == before_ids

    resolved = client.patch(
        f"/api/v1/maintenance-requests/{request_id}",
        json={"status": "resolved"},
    )
    assert resolved.status_code == 200
    resolved_at = resolved.json()["resolved_at"]
    assert resolved_at is not None

    before_repeat = client.get("/api/v1/audit-events")
    before_repeat_ids = [event["id"] for event in before_repeat.json()]
    repeated = client.patch(
        f"/api/v1/maintenance-requests/{request_id}",
        json={"status": "resolved"},
    )
    assert repeated.status_code == 200
    assert repeated.json()["resolved_at"] == resolved_at
    after_repeat = client.get("/api/v1/audit-events")
    assert [event["id"] for event in after_repeat.json()] == before_repeat_ids


def test_noop_software_license_update_does_not_create_audit_event(client: TestClient) -> None:
    created = client.post(
        "/api/v1/software-licenses",
        json={
            "name": "Office Suite",
            "license_type": "subscription",
            "license_reference": "NOOP-LICENSE-001",
            "seats_total": 20,
            "seats_used": 5,
        },
    )
    assert created.status_code == 201
    license_id = created.json()["id"]

    before = client.get("/api/v1/audit-events")
    assert before.status_code == 200
    before_ids = [event["id"] for event in before.json()]

    unchanged = client.patch(
        f"/api/v1/software-licenses/{license_id}",
        json={
            "name": " Office Suite ",
            "license_reference": " NOOP-LICENSE-001 ",
            "seats_total": 20,
            "seats_used": 5,
        },
    )
    assert unchanged.status_code == 200
    assert unchanged.json()["name"] == "Office Suite"

    after = client.get("/api/v1/audit-events")
    assert [event["id"] for event in after.json()] == before_ids

def test_maintenance_create_integrity_error_rolls_back_without_audit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    asset = client.post(
        "/api/v1/assets",
        json={"name": "Managed server", "asset_tag": "MAINT-ROLLBACK-001", "category": "Server"},
    )
    assert asset.status_code == 201

    original_record_audit = main.record_audit

    def fail_maintenance_audit(session, user, action, entity_type, entity_id, details=None):
        if action == "maintenance_request.created":
            raise IntegrityError("audit insert", {}, Exception("simulated integrity conflict"))
        return original_record_audit(session, user, action, entity_type, entity_id, details)

    monkeypatch.setattr(main, "record_audit", fail_maintenance_audit)
    response = client.post(
        "/api/v1/maintenance-requests",
        json={
            "asset_id": asset.json()["id"],
            "title": "Inspect server",
            "description": "Verify transaction rollback after an integrity conflict",
        },
    )

    assert response.status_code == 409
    assert client.get("/api/v1/maintenance-requests").json() == []
    actions = [event["action"] for event in client.get("/api/v1/audit-events").json()]
    assert "maintenance_request.created" not in actions

def test_employee_create_integrity_error_rolls_back_without_audit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing_employee_ids = {
        employee["id"] for employee in client.get("/api/v1/employees").json()
    }
    existing_audit_ids = {
        event["id"] for event in client.get("/api/v1/audit-events").json()
    }
    original_record_audit = main.record_audit

    def fail_employee_audit(session, user, action, entity_type, entity_id, details=None):
        if action == "employee.created":
            raise IntegrityError("audit insert", {}, Exception("simulated integrity conflict"))
        return original_record_audit(session, user, action, entity_type, entity_id, details)

    monkeypatch.setattr(main, "record_audit", fail_employee_audit)
    response = client.post(
        "/api/v1/employees",
        json={
            "employee_number": "EMP-ROLLBACK-001",
            "full_name": "Rollback Employee",
            "email": "rollback.employee@example.com",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Employee creation conflicts with current organization records"
    )
    employee_ids = {
        employee["id"] for employee in client.get("/api/v1/employees").json()
    }
    audit_ids = {
        event["id"] for event in client.get("/api/v1/audit-events").json()
    }
    assert employee_ids == existing_employee_ids
    assert audit_ids == existing_audit_ids

def test_software_license_create_integrity_error_rolls_back_without_audit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing_audit_ids = {
        event["id"] for event in client.get("/api/v1/audit-events").json()
    }
    original_record_audit = main.record_audit

    def fail_license_audit(session, user, action, entity_type, entity_id, details=None):
        if action == "software_license.created":
            raise IntegrityError("audit insert", {}, Exception("simulated integrity conflict"))
        return original_record_audit(session, user, action, entity_type, entity_id, details)

    monkeypatch.setattr(main, "record_audit", fail_license_audit)
    response = client.post(
        "/api/v1/software-licenses",
        json={
            "name": "Rollback License",
            "license_type": "subscription",
            "license_reference": "ROLLBACK-LICENSE-001",
            "seats_total": 10,
            "seats_used": 2,
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Software license creation conflicts with current organization records"
    )
    assert client.get("/api/v1/software-licenses").json() == []
    audit_ids = {
        event["id"] for event in client.get("/api/v1/audit-events").json()
    }
    assert audit_ids == existing_audit_ids


def test_employee_delete_integrity_error_rolls_back_without_audit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = client.post(
        "/api/v1/employees",
        json={
            "employee_number": "EMP-DELETE-ROLLBACK-001",
            "full_name": "Employee To Keep",
            "email": "employee.delete.rollback@example.com",
        },
    )
    assert created.status_code == 201
    employee_id = created.json()["id"]
    existing_audit_ids = {
        event["id"] for event in client.get("/api/v1/audit-events").json()
    }
    original_record_audit = main.record_audit

    def fail_employee_delete_audit(session, user, action, entity_type, entity_id, details=None):
        if action == "employee.deleted":
            raise IntegrityError("audit insert", {}, Exception("simulated integrity conflict"))
        return original_record_audit(session, user, action, entity_type, entity_id, details)

    monkeypatch.setattr(main, "record_audit", fail_employee_delete_audit)
    response = client.delete(f"/api/v1/employees/{employee_id}")

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Employee deletion conflicts with current organization records"
    )
    employees = client.get("/api/v1/employees")
    assert employees.status_code == 200
    assert any(employee["id"] == employee_id for employee in employees.json())
    after_audit_ids = {
        event["id"] for event in client.get("/api/v1/audit-events").json()
    }
    assert after_audit_ids == existing_audit_ids

