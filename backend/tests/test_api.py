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
    asset_version = created.json()["version"]

    listed = client.get("/api/v1/assets")
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    updated = client.patch(
        f"/api/v1/assets/{asset_id}",
        json={"expected_version": asset_version, "status": "in_repair"},
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


def test_asset_delete_persists_complete_audit_snapshot(client: TestClient) -> None:
    created = client.post(
        "/api/v1/assets",
        json={
            "name": "Retired database server",
            "asset_tag": "DELETE-AUDIT-SNAPSHOT-001",
            "category": "Server",
            "status": "in_repair",
        },
    )
    assert created.status_code == 201
    asset = created.json()

    deleted = client.delete(f"/api/v1/assets/{asset['id']}")
    assert deleted.status_code == 204
    assert client.get(f"/api/v1/assets/{asset['id']}").status_code == 404

    events = client.get("/api/v1/audit-events")
    assert events.status_code == 200
    deletion_events = [
        event for event in events.json()
        if event["action"] == "asset.deleted"
        and event["entity_id"] == str(asset["id"])
    ]
    assert len(deletion_events) == 1
    assert deletion_events[0]["entity_type"] == "asset"
    assert deletion_events[0]["actor_user_id"] is not None
    assert deletion_events[0]["details"] == {
        "asset_tag": "DELETE-AUDIT-SNAPSHOT-001",
        "name": "Retired database server",
        "category": "Server",
        "status": "in_repair",
    }


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


def test_asset_create_audit_integrity_conflict_rolls_back_asset(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_audit(*args, **kwargs):
        raise IntegrityError("insert audit event", {}, Exception("audit constraint"))

    monkeypatch.setattr(main, "record_audit", fail_audit)
    response = client.post(
        "/api/v1/assets",
        json={"name": "Audit conflict laptop", "asset_tag": "AUDIT-CREATE-001", "category": "Laptop"},
    )
    assert response.status_code == 409
    assert client.get("/api/v1/assets").json() == []


def test_asset_update_audit_integrity_conflict_rolls_back_changes(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = client.post(
        "/api/v1/assets",
        json={"name": "Original laptop", "asset_tag": "AUDIT-UPDATE-001", "category": "Laptop"},
    )
    asset_id = created.json()["id"]
    asset_version = created.json()["version"]

    def fail_audit(*args, **kwargs):
        raise IntegrityError("insert audit event", {}, Exception("audit constraint"))

    monkeypatch.setattr(main, "record_audit", fail_audit)
    response = client.patch(f"/api/v1/assets/{asset_id}", json={"expected_version": asset_version, "name": "Must roll back"})
    assert response.status_code == 409
    refreshed = client.get(f"/api/v1/assets/{asset_id}")
    assert refreshed.status_code == 200
    assert refreshed.json()["name"] == "Original laptop"


def test_asset_history_is_tenant_scoped_and_readable_by_viewer(client: TestClient) -> None:
    created = client.post(
        "/api/v1/assets",
        json={"name": "History laptop", "asset_tag": "HIST-001", "category": "Laptop"},
    )
    asset_id = created.json()["id"]
    assert client.patch(f"/api/v1/assets/{asset_id}", json={"expected_version": created.json()["version"], "name": "Renamed laptop"}).status_code == 200

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

    versions = {item["id"]: item["version"] for item in client.get("/api/v1/assets").json() if item["id"] in ids}
    response = client.patch(
        "/api/v1/assets/bulk",
        json={"asset_ids": ids, "versions": versions, "status": "in_repair", "assigned_to": "Alex Smith"},
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
        json={"asset_ids": [asset_id], "versions": {asset_id: created.json()["version"]}, "status": "active", "assigned_to": None},
    )
    assert response.status_code == 200
    assert response.json()["updated"] == 0
    history = client.get(f"/api/v1/assets/{asset_id}/history")
    assert not any(event["action"] == "asset.updated" for event in history.json())


def test_bulk_asset_update_validates_ids_and_fields(client: TestClient) -> None:
    for payload in (
        {"asset_ids": [] , "status": "active"},
        {"asset_ids": [1, 1], "versions": {1: 1}, "status": "active"},
        {"asset_ids": [1], "versions": {1: 1}, "status": None, "assigned_to": "A"},
        {"asset_ids": [1], "versions": {1: 1}},
        {"asset_ids": list(range(1, 102)), "versions": {i: 1 for i in range(1, 102)}, "status": "active"},
    ):
        assert client.patch("/api/v1/assets/bulk", json=payload).status_code == 422


def test_bulk_asset_update_requires_editor_or_admin(client: TestClient) -> None:
    use_role(client, "viewer")
    response = client.patch(
        "/api/v1/assets/bulk",
        json={"asset_ids": [1], "versions": {1: 1}, "status": "retired"},
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
        json={"asset_ids": [first["id"], foreign["id"]], "versions": {first["id"]: first["version"], foreign["id"]: foreign["version"]}, "status": "retired"},
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



def test_asset_csv_import_aggregate_audit_conflict_rolls_back_batch(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = (
        "name,asset_tag,category\n"
        "Imported laptop,CSV-AUDIT-ROLLBACK-001,Laptop\n"
    )
    existing_audit_ids = {
        event["id"] for event in client.get("/api/v1/audit-events").json()
    }
    original_record_audit = main.record_audit

    def fail_batch_audit(session, user, action, entity_type, entity_id, details=None):
        if action == "asset.imported" and entity_type == "asset_import":
            raise IntegrityError("audit insert", {}, Exception("simulated audit conflict"))
        return original_record_audit(session, user, action, entity_type, entity_id, details)

    monkeypatch.setattr(main, "record_audit", fail_batch_audit)
    response = client.post(
        "/api/v1/assets/import",
        files={"file": ("assets.csv", payload.encode("utf-8"), "text/csv")},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Asset import conflicts with current organization records"
    assert client.get("/api/v1/assets").json() == []
    assert {
        event["id"] for event in client.get("/api/v1/audit-events").json()
    } == existing_audit_ids


def test_asset_csv_import_row_audit_failure_rolls_back_only_failed_row(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    failed_tag = "CSV-ROW-AUDIT-FAIL-002"
    payload = (
        "name,asset_tag,category\n"
        "First laptop,CSV-ROW-AUDIT-OK-001,Laptop\n"
        "Failed laptop,CSV-ROW-AUDIT-FAIL-002,Laptop\n"
        "Last laptop,CSV-ROW-AUDIT-OK-003,Laptop\n"
    )
    original_record_audit = main.record_audit

    def fail_one_row_audit(session, user, action, entity_type, entity_id, details=None):
        if (
            action == "asset.imported"
            and entity_type == "asset"
            and details
            and details.get("asset_tag") == failed_tag
        ):
            raise IntegrityError("audit insert", {}, Exception("simulated row audit conflict"))
        return original_record_audit(session, user, action, entity_type, entity_id, details)

    monkeypatch.setattr(main, "record_audit", fail_one_row_audit)
    response = client.post(
        "/api/v1/assets/import",
        files={"file": ("assets.csv", payload.encode("utf-8"), "text/csv")},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["created"] == 2
    assert result["skipped"] == 1
    assert result["errors"] == [
        {"row": 3, "error": "Duplicate asset tag or serial number"}
    ]

    assets = client.get("/api/v1/assets").json()
    assert {asset["asset_tag"] for asset in assets} == {
        "CSV-ROW-AUDIT-OK-001",
        "CSV-ROW-AUDIT-OK-003",
    }

    imported_events = [
        event for event in client.get("/api/v1/audit-events").json()
        if event["action"] == "asset.imported"
    ]
    row_events = [event for event in imported_events if event["entity_type"] == "asset"]
    assert {
        event["details"]["asset_tag"] for event in row_events
    } == {"CSV-ROW-AUDIT-OK-001", "CSV-ROW-AUDIT-OK-003"}
    batch_events = [event for event in imported_events if event["entity_type"] == "asset_import"]
    assert len(batch_events) == 1
    assert batch_events[0]["details"] == {
        "created": 2,
        "skipped": 1,
        "error_count": 1,
    }



def test_asset_csv_import_unexpected_audit_error_rolls_back_entire_batch(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    failed_tag = "CSV-UNEXPECTED-ERROR-002"
    payload = (
        "name,asset_tag,category\n"
        "First laptop,CSV-UNEXPECTED-ERROR-001,Laptop\n"
        "Failed laptop,CSV-UNEXPECTED-ERROR-002,Laptop\n"
        "Last laptop,CSV-UNEXPECTED-ERROR-003,Laptop\n"
    )
    existing_audit_ids = {
        event["id"] for event in client.get("/api/v1/audit-events").json()
    }
    original_record_audit = main.record_audit
    failed_calls = 0

    def fail_with_unexpected_error(session, user, action, entity_type, entity_id, details=None):
        nonlocal failed_calls
        if (
            action == "asset.imported"
            and entity_type == "asset"
            and details
            and details.get("asset_tag") == failed_tag
        ):
            failed_calls += 1
            raise RuntimeError("simulated unexpected audit failure")
        return original_record_audit(session, user, action, entity_type, entity_id, details)

    monkeypatch.setattr(main, "record_audit", fail_with_unexpected_error)
    with pytest.raises(RuntimeError, match="simulated unexpected audit failure"):
        client.post(
            "/api/v1/assets/import",
            files={"file": ("assets.csv", payload.encode("utf-8"), "text/csv")},
        )

    assert failed_calls == 1
    assert client.get("/api/v1/assets").json() == []
    assert {
        event["id"] for event in client.get("/api/v1/audit-events").json()
    } == existing_audit_ids


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
    response = client.patch(f"/api/v1/assets/{asset_id}", json={"expected_version": created.json()["version"], "name": None})
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

def test_user_create_audit_integrity_conflict_rolls_back_user(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_audit(*args, **kwargs):
        raise IntegrityError("insert audit event", {}, Exception("audit constraint"))

    monkeypatch.setattr(main, "record_audit", fail_audit)
    response = client.post(
        "/api/v1/users",
        json={
            "email": "audit-conflict@example.com",
            "full_name": "Audit Conflict",
            "password": "long-enough-password",
            "role": "viewer",
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "Email is already registered"
    assert all(user["email"] != "audit-conflict@example.com" for user in client.get("/api/v1/users").json())


def test_user_update_audit_integrity_conflict_rolls_back_changes(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    users = client.get("/api/v1/users").json()
    target = next(user for user in users if user["email"] == "editor.a@example.com")

    def fail_audit(*args, **kwargs):
        raise IntegrityError("insert audit event", {}, Exception("audit constraint"))

    monkeypatch.setattr(main, "record_audit", fail_audit)
    response = client.patch(
        f"/api/v1/users/{target['id']}",
        json={"expected_version": target["version"], "full_name": "Should Not Persist"},
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "User update conflicts with current organization records"
    refreshed = next(
        user for user in client.get("/api/v1/users").json()
        if user["id"] == target["id"]
    )
    assert refreshed["full_name"] == "Editor"


def test_admin_cannot_demote_own_account(client: TestClient) -> None:
    current = client.get("/api/v1/auth/me").json()
    response = client.patch(
        f"/api/v1/users/{current['id']}",
        json={"expected_version": current["version"], "role": "editor"},
    )
    assert response.status_code == 422


def test_deactivated_user_token_is_rejected(client: TestClient) -> None:
    users = client.get("/api/v1/users").json()
    editor = next(user for user in users if user["email"] == "editor.a@example.com")
    disabled = client.patch(
        f"/api/v1/users/{editor['id']}",
        json={"expected_version": editor["version"], "is_active": False},
    )
    assert disabled.status_code == 200
    use_role(client, "editor")
    assert client.get("/api/v1/assets").status_code == 401


def test_user_optimistic_locking_rejects_stale_updates_and_increments_version(client: TestClient) -> None:
    target = next(user for user in client.get("/api/v1/users").json() if user["email"] == "editor.a@example.com")
    first = client.patch(
        f"/api/v1/users/{target['id']}",
        json={"expected_version": target["version"], "full_name": "Updated Editor"},
    )
    assert first.status_code == 200
    assert first.json()["version"] == target["version"] + 1

    stale = client.patch(
        f"/api/v1/users/{target['id']}",
        json={"expected_version": target["version"], "full_name": "Stale overwrite"},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"] == "User has changed; reload it before updating"


def test_user_update_requires_expected_version(client: TestClient) -> None:
    target = next(user for user in client.get("/api/v1/users").json() if user["email"] == "editor.a@example.com")
    response = client.patch(f"/api/v1/users/{target['id']}", json={"full_name": "Missing version"})
    assert response.status_code == 422


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
    license_version = created.json()["record_version"]

    listed = client.get("/api/v1/software-licenses")
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    invalid_update = client.patch(
        f"/api/v1/software-licenses/{license_id}",
        json={"expected_version": license_version, "seats_total": 4},
    )
    assert invalid_update.status_code == 422

    updated = client.patch(
        f"/api/v1/software-licenses/{license_id}",
        json={"expected_version": license_version, "seats_used": 10},
    )
    assert updated.status_code == 200
    assert updated.json()["seats_used"] == 10
    assert updated.json()["record_version"] == license_version + 1

    stale = client.patch(
        f"/api/v1/software-licenses/{license_id}",
        json={"expected_version": license_version, "name": "Stale overwrite"},
    )
    assert stale.status_code == 409
    assert client.get(f"/api/v1/software-licenses/{license_id}").json()["name"] == "Office Suite"

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
    license_version = created.json()["record_version"]
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
        json={"expected_version": license_version, "name": "Partially Updated License"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Software license update conflicts with current organization records or stale version"
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
    request_version = created.json()["version"]
    assert request_version == 1
    assert created.json()["requested_by_user_id"] == viewer["id"]
    assert len(client.get("/api/v1/maintenance-requests").json()) == 1

    use_role(client, "editor")
    assigned = client.patch(
        f"/api/v1/maintenance-requests/{request_id}",
        json={"expected_version": request_version, "assigned_to_user_id": editor["id"], "status": "in_progress"},
    )
    assert assigned.status_code == 200
    assert assigned.json()["assigned_to_user_id"] == editor["id"]
    assert assigned.json()["version"] == request_version + 1

    stale = client.patch(
        f"/api/v1/maintenance-requests/{request_id}",
        json={"expected_version": request_version, "status": "cancelled"},
    )
    assert stale.status_code == 409
    assert client.get(f"/api/v1/maintenance-requests/{request_id}").json()["status"] == "in_progress"

    resolved = client.patch(
        f"/api/v1/maintenance-requests/{request_id}",
        json={"expected_version": assigned.json()["version"], "status": "resolved"},
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
        json={"expected_version": created.json()["version"], "status": "resolved"},
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
        json={"expected_version": request_record["version"], "assigned_to_user_id": other_id},
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
        json={"expected_version": employee.json()["version"], "job_title": "Senior Systems Engineer"},
    )
    assert updated.status_code == 200
    assert updated.json()["job_title"] == "Senior Systems Engineer"

    use_role(client, "viewer")
    assert client.get("/api/v1/employees").status_code == 403
    assert client.get("/api/v1/departments").status_code == 403


def test_employee_optimistic_locking_rejects_stale_updates(client: TestClient) -> None:
    created = client.post(
        "/api/v1/employees",
        json={"employee_number": "EMP-LOCK-001", "full_name": "Concurrent Employee"},
    )
    assert created.status_code == 201
    employee = created.json()
    assert employee["version"] == 1

    first_update = client.patch(
        f"/api/v1/employees/{employee['id']}",
        json={"expected_version": 1, "job_title": "Updated by first editor"},
    )
    assert first_update.status_code == 200
    assert first_update.json()["version"] == 2

    stale_update = client.patch(
        f"/api/v1/employees/{employee['id']}",
        json={"expected_version": 1, "full_name": "Stale overwrite"},
    )
    assert stale_update.status_code == 409

    stored = next(
        item for item in client.get("/api/v1/employees").json()
        if item["id"] == employee["id"]
    )
    assert stored["full_name"] == "Concurrent Employee"
    assert stored["job_title"] == "Updated by first editor"
    assert stored["version"] == 2


def test_employee_update_requires_expected_version(client: TestClient) -> None:
    created = client.post(
        "/api/v1/employees",
        json={"employee_number": "EMP-LOCK-REQUIRED", "full_name": "Version Required"},
    )
    assert created.status_code == 201
    response = client.patch(
        f"/api/v1/employees/{created.json()['id']}",
        json={"full_name": "Missing version"},
    )
    assert response.status_code == 422


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
        json={"expected_version": next(u["version"] for u in client.get("/api/v1/users").json() if u["id"] == editor_id), "is_active": False},
    )
    assert response.status_code == 200
    response = client.patch(
        f"/api/v1/users/{editor_id}",
        json={"expected_version": response.json()["version"], "is_active": True},
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
        f"/api/v1/departments/{department_b['id']}", json={"expected_version": department_b["version"], "name": "Hijacked"}
    ).status_code == 404
    assert client.delete(f"/api/v1/departments/{department_b['id']}").status_code == 404
    assert client.patch(
        f"/api/v1/employees/{employee_b['id']}", json={"expected_version": 1, "full_name": "Hijacked"}
    ).status_code == 404
    assert client.delete(f"/api/v1/employees/{employee_b['id']}").status_code == 404
    assert client.get(f"/api/v1/software-licenses/{license_b['id']}").status_code == 404
    assert client.patch(
        f"/api/v1/software-licenses/{license_b['id']}", json={"expected_version": 1, "name": "Hijacked"}
    ).status_code == 404
    assert client.delete(f"/api/v1/software-licenses/{license_b['id']}").status_code == 404
    assert client.get(
        f"/api/v1/maintenance-requests/{maintenance_b['id']}"
    ).status_code == 404
    assert client.patch(
        f"/api/v1/maintenance-requests/{maintenance_b['id']}",
        json={"expected_version": maintenance_b["version"], "title": "Hijacked"},
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
        json={"expected_version": client.get("/api/v1/auth/me").json()["version"], "is_active": False},
    )
    assert disabled.status_code == 422
    assert client.get("/api/v1/auth/me").status_code == 200

    demoted = client.patch(
        f"/api/v1/users/{current_id}",
        json={"expected_version": client.get("/api/v1/auth/me").json()["version"], "role": "editor"},
    )
    assert demoted.status_code == 422
    assert client.get("/api/v1/auth/me").status_code == 200


def test_admin_user_management_cannot_cross_tenant_or_assign_tenant(client: TestClient) -> None:
    use_role(client, "other_admin")
    foreign_user_id = client.get("/api/v1/auth/me").json()["id"]

    use_role(client, "admin")
    assert client.patch(
        f"/api/v1/users/{foreign_user_id}",
        json={"expected_version": 1, "full_name": "Cross-tenant takeover"},
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
        json={"expected_version": editor["version"], "is_active": False},
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
        json={"expected_version": employee.json()["version"], "department_id": department_b_id},
    )
    assert reassignment.status_code == 422

    assignment = client.patch(
        f"/api/v1/maintenance-requests/{maintenance.json()['id']}",
        json={"expected_version": maintenance.json()["version"], "assigned_to_user_id": editor["id"]},
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
        json={"expected_version": target["version"], "full_name": f" {target['full_name']} ", "role": target["role"], "is_active": target["is_active"]},
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

    response = client.patch(f"/api/v1/users/{target['id']}", json={"expected_version": target["version"]})
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


def test_department_delete_audit_retains_department_snapshot(client: TestClient) -> None:
    created = client.post(
        "/api/v1/departments",
        json={"name": "Retired Operations", "code": "OPS-RETIRED"},
    )
    assert created.status_code == 201
    department_id = created.json()["id"]

    deleted = client.delete(f"/api/v1/departments/{department_id}")
    assert deleted.status_code == 204
    assert client.get(f"/api/v1/departments/{department_id}").status_code == 404

    events = client.get("/api/v1/audit-events")
    assert events.status_code == 200
    matching = [
        event for event in events.json()
        if event["action"] == "department.deleted"
        and event["entity_id"] == str(department_id)
    ]
    assert len(matching) == 1
    assert matching[0]["details"] == {
        "name": "Retired Operations",
        "code": "OPS-RETIRED",
    }


def test_employee_delete_audit_retains_employee_snapshot(client: TestClient) -> None:
    created = client.post(
        "/api/v1/employees",
        json={
            "employee_number": "EMP-AUDIT-SNAPSHOT-001",
            "full_name": "Former Operations Lead",
            "job_title": "Operations Lead",
        },
    )
    assert created.status_code == 201
    employee = created.json()

    deleted = client.delete(f"/api/v1/employees/{employee['id']}")
    assert deleted.status_code == 204
    assert client.get(f"/api/v1/employees/{employee['id']}").status_code == 404

    events = client.get("/api/v1/audit-events")
    assert events.status_code == 200
    matching = [
        event for event in events.json()
        if event["action"] == "employee.deleted"
        and event["entity_id"] == str(employee["id"])
    ]
    assert len(matching) == 1
    assert matching[0]["details"] == {
        "employee_number": "EMP-AUDIT-SNAPSHOT-001",
        "full_name": "Former Operations Lead",
        "department_id": None,
        "job_title": "Operations Lead",
        "is_active": True,
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
        json={"expected_version": created.json()["version"], "name": "No-op Department", "code": "NOOP"},
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
        json={"expected_version": created.json()["version"], "full_name": "Partially Updated Employee"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Employee update conflicts with current organization records or stale version"
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
        json={"expected_version": created.json()["version"], "employee_number": "NOOP-EMP-001", "full_name": "No-op Employee"},
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
    request_version = created.json()["version"]

    before = client.get("/api/v1/audit-events")
    assert before.status_code == 200
    before_ids = [event["id"] for event in before.json()]

    unchanged = client.patch(
        f"/api/v1/maintenance-requests/{request_id}",
        json={"expected_version": request_version, "title": "Check cooling", "status": "open"},
    )
    assert unchanged.status_code == 200
    assert unchanged.json()["resolved_at"] is None

    after = client.get("/api/v1/audit-events")
    assert [event["id"] for event in after.json()] == before_ids

    resolved = client.patch(
        f"/api/v1/maintenance-requests/{request_id}",
        json={"expected_version": unchanged.json()["version"], "status": "resolved"},
    )
    assert resolved.status_code == 200
    resolved_at = resolved.json()["resolved_at"]
    assert resolved_at is not None

    before_repeat = client.get("/api/v1/audit-events")
    before_repeat_ids = [event["id"] for event in before_repeat.json()]
    repeated = client.patch(
        f"/api/v1/maintenance-requests/{request_id}",
        json={"expected_version": resolved.json()["version"], "status": "resolved"},
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
            "expected_version": 1,
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

def test_maintenance_update_integrity_error_rolls_back_without_audit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    asset = client.post(
        "/api/v1/assets",
        json={"name": "Maintenance rollback host", "asset_tag": "MAINT-UPDATE-ROLLBACK-001", "category": "Server"},
    )
    assert asset.status_code == 201
    created = client.post(
        "/api/v1/maintenance-requests",
        json={
            "asset_id": asset.json()["id"],
            "title": "Original maintenance title",
            "description": "Original maintenance description",
        },
    )
    assert created.status_code == 201
    request_id = created.json()["id"]
    existing_audit_ids = {
        event["id"] for event in client.get("/api/v1/audit-events").json()
    }
    original_record_audit = main.record_audit

    def fail_maintenance_update_audit(session, user, action, entity_type, entity_id, details=None):
        if action == "maintenance_request.updated":
            raise IntegrityError("audit insert", {}, Exception("simulated integrity conflict"))
        return original_record_audit(session, user, action, entity_type, entity_id, details)

    monkeypatch.setattr(main, "record_audit", fail_maintenance_update_audit)
    response = client.patch(
        f"/api/v1/maintenance-requests/{request_id}",
        json={"expected_version": created.json()["version"], "title": "Partially updated maintenance title"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Maintenance request update conflicts with current organization records or stale version"
    )
    stored = client.get("/api/v1/maintenance-requests")
    assert stored.status_code == 200
    request_record = next(item for item in stored.json() if item["id"] == request_id)
    assert request_record["title"] == "Original maintenance title"
    assert request_record["description"] == "Original maintenance description"
    audit_ids = {
        event["id"] for event in client.get("/api/v1/audit-events").json()
    }
    assert audit_ids == existing_audit_ids


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


def test_software_license_delete_integrity_error_rolls_back_without_audit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = client.post(
        "/api/v1/software-licenses",
        json={
            "name": "License To Keep",
            "license_type": "subscription",
            "license_reference": "DELETE-ROLLBACK-LICENSE-001",
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

    def fail_license_delete_audit(session, user, action, entity_type, entity_id, details=None):
        if action == "software_license.deleted":
            raise IntegrityError("audit insert", {}, Exception("simulated integrity conflict"))
        return original_record_audit(session, user, action, entity_type, entity_id, details)

    monkeypatch.setattr(main, "record_audit", fail_license_delete_audit)
    response = client.delete(f"/api/v1/software-licenses/{license_id}")

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Software license deletion conflicts with current organization records"
    )
    licenses = client.get("/api/v1/software-licenses")
    assert licenses.status_code == 200
    assert any(license_record["id"] == license_id for license_record in licenses.json())
    after_audit_ids = {
        event["id"] for event in client.get("/api/v1/audit-events").json()
    }
    assert after_audit_ids == existing_audit_ids



def test_department_create_audit_integrity_error_rolls_back_department(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing_audit_ids = {
        event["id"] for event in client.get("/api/v1/audit-events").json()
    }
    original_record_audit = main.record_audit

    def fail_department_create_audit(session, user, action, entity_type, entity_id, details=None):
        if action == "department.created":
            raise IntegrityError("audit insert", {}, Exception("simulated integrity conflict"))
        return original_record_audit(session, user, action, entity_type, entity_id, details)

    monkeypatch.setattr(main, "record_audit", fail_department_create_audit)
    response = client.post(
        "/api/v1/departments",
        json={"name": "Rollback Department", "code": "ROLLBACK"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Department creation conflicts with current organization records"
    )
    assert client.get("/api/v1/departments").json() == []
    assert {
        event["id"] for event in client.get("/api/v1/audit-events").json()
    } == existing_audit_ids


def test_department_update_audit_integrity_error_rolls_back_changes(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = client.post(
        "/api/v1/departments",
        json={"name": "Original Department", "code": "ORIGINAL"},
    )
    assert created.status_code == 201
    department_id = created.json()["id"]
    existing_audit_ids = {
        event["id"] for event in client.get("/api/v1/audit-events").json()
    }
    original_record_audit = main.record_audit

    def fail_department_update_audit(session, user, action, entity_type, entity_id, details=None):
        if action == "department.updated":
            raise IntegrityError("audit insert", {}, Exception("simulated integrity conflict"))
        return original_record_audit(session, user, action, entity_type, entity_id, details)

    monkeypatch.setattr(main, "record_audit", fail_department_update_audit)
    response = client.patch(
        f"/api/v1/departments/{department_id}",
        json={"expected_version": created.json()["version"], "name": "Partially Updated Department"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Department update conflicts with current organization records or stale version"
    )
    stored = client.get("/api/v1/departments")
    assert stored.status_code == 200
    department = next(item for item in stored.json() if item["id"] == department_id)
    assert department["name"] == "Original Department"
    assert department["code"] == "ORIGINAL"
    assert {
        event["id"] for event in client.get("/api/v1/audit-events").json()
    } == existing_audit_ids


def test_department_delete_audit_integrity_error_rolls_back_department(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = client.post(
        "/api/v1/departments",
        json={"name": "Department To Keep", "code": "DELETE-ROLLBACK"},
    )
    assert created.status_code == 201
    department_id = created.json()["id"]
    existing_audit_ids = {
        event["id"] for event in client.get("/api/v1/audit-events").json()
    }
    original_record_audit = main.record_audit

    def fail_department_delete_audit(session, user, action, entity_type, entity_id, details=None):
        if action == "department.deleted":
            raise IntegrityError("audit insert", {}, Exception("simulated integrity conflict"))
        return original_record_audit(session, user, action, entity_type, entity_id, details)

    monkeypatch.setattr(main, "record_audit", fail_department_delete_audit)
    response = client.delete(f"/api/v1/departments/{department_id}")

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Department deletion conflicts with current organization records"
    )
    departments = client.get("/api/v1/departments")
    assert departments.status_code == 200
    assert any(item["id"] == department_id for item in departments.json())
    assert {
        event["id"] for event in client.get("/api/v1/audit-events").json()
    } == existing_audit_ids


def test_password_change_audit_integrity_error_rolls_back_credentials(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing_audit_ids = {
        event["id"] for event in client.get("/api/v1/audit-events").json()
    }

    def fail_password_audit(session, user, action, entity_type, entity_id, details=None):
        if action == "user.password_changed":
            raise IntegrityError("audit insert", {}, Exception("simulated integrity conflict"))
        raise AssertionError(f"Unexpected audit action: {action}")

    monkeypatch.setattr(main, "record_audit", fail_password_audit)
    response = client.post(
        "/api/v1/auth/change-password",
        json={
            "current_password": "test-password-admin-123",
            "new_password": "replacement-password-456",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Password change conflicts with current account records"
    )
    assert client.get("/api/v1/auth/me").status_code == 200

    old_login = client.post(
        "/api/v1/auth/token",
        data={"username": "admin.a@example.com", "password": "test-password-admin-123"},
    )
    assert old_login.status_code == 200
    new_login = client.post(
        "/api/v1/auth/token",
        data={"username": "admin.a@example.com", "password": "replacement-password-456"},
    )
    assert new_login.status_code == 401
    assert {
        event["id"] for event in client.get("/api/v1/audit-events").json()
    } == existing_audit_ids


def test_asset_delete_audit_integrity_error_rolls_back_asset(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = client.post(
        "/api/v1/assets",
        json={
            "name": "Asset to preserve",
            "asset_tag": "DEL-AUDIT-ROLLBACK",
            "category": "Laptop",
        },
    )
    assert created.status_code == 201
    asset_id = created.json()["id"]
    existing_audit_ids = {
        event["id"] for event in client.get("/api/v1/audit-events").json()
    }

    def fail_asset_delete_audit(session, user, action, entity_type, entity_id, details=None):
        if action == "asset.deleted":
            raise IntegrityError("audit insert", {}, Exception("simulated integrity conflict"))
        raise AssertionError(f"Unexpected audit action: {action}")

    monkeypatch.setattr(main, "record_audit", fail_asset_delete_audit)
    response = client.delete(f"/api/v1/assets/{asset_id}")

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Asset deletion conflicts with current organization records"
    )
    stored = client.get(f"/api/v1/assets/{asset_id}")
    assert stored.status_code == 200
    assert stored.json()["asset_tag"] == "DEL-AUDIT-ROLLBACK"
    assert {
        event["id"] for event in client.get("/api/v1/audit-events").json()
    } == existing_audit_ids


def test_bulk_asset_update_audit_integrity_error_rolls_back_all_assets(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    asset_ids = []
    for index in range(2):
        created = client.post(
            "/api/v1/assets",
            json={
                "name": f"Original bulk asset {index}",
                "asset_tag": f"BULK-AUDIT-ROLLBACK-{index}",
                "category": "Laptop",
                "assigned_to": f"Original owner {index}",
            },
        )
        assert created.status_code == 201
        asset_ids.append(created.json()["id"])

    versions = {item["id"]: item["version"] for item in client.get("/api/v1/assets").json() if item["id"] in asset_ids}
    existing_audit_ids = {
        event["id"] for event in client.get("/api/v1/audit-events").json()
    }
    original_record_audit = main.record_audit
    failed_actions = 0

    def fail_second_bulk_audit(session, user, action, entity_type, entity_id, details=None):
        nonlocal failed_actions
        if action == "asset.updated" and details and details.get("source") == "bulk":
            failed_actions += 1
            if failed_actions == 2:
                raise IntegrityError("audit insert", {}, Exception("simulated integrity conflict"))
        return original_record_audit(session, user, action, entity_type, entity_id, details)

    monkeypatch.setattr(main, "record_audit", fail_second_bulk_audit)
    response = client.patch(
        "/api/v1/assets/bulk",
        json={"asset_ids": asset_ids, "versions": versions, "status": "in_repair", "assigned_to": "New owner"},
    )

    assert response.status_code == 409
    assert "conflicts" in response.json()["detail"]
    assert failed_actions == 2

    stored_assets = client.get("/api/v1/assets").json()
    for asset_id, index in zip(asset_ids, range(2)):
        stored = next(asset for asset in stored_assets if asset["id"] == asset_id)
        assert stored["name"] == f"Original bulk asset {index}"
        assert stored["status"] == "active"
        assert stored["assigned_to"] == f"Original owner {index}"

    audit_ids = {
        event["id"] for event in client.get("/api/v1/audit-events").json()
    }
    assert audit_ids == existing_audit_ids


def test_asset_update_rejects_stale_version_without_audit(client: TestClient) -> None:
    created = client.post(
        "/api/v1/assets",
        json={"name": "Versioned asset", "asset_tag": "VERSION-001", "category": "Laptop"},
    )
    assert created.status_code == 201
    asset = created.json()
    assert asset["version"] == 1

    updated = client.patch(
        f"/api/v1/assets/{asset['id']}",
        json={"expected_version": 1, "name": "Updated versioned asset"},
    )
    assert updated.status_code == 200
    assert updated.json()["version"] == 2

    audit_before = client.get("/api/v1/audit-events").json()
    stale = client.patch(
        f"/api/v1/assets/{asset['id']}",
        json={"expected_version": 1, "name": "Stale overwrite"},
    )
    assert stale.status_code == 409
    assert "changed" in stale.json()["detail"].lower()

    stored = client.get(f"/api/v1/assets/{asset['id']}").json()
    assert stored["name"] == "Updated versioned asset"
    assert stored["version"] == 2
    assert client.get("/api/v1/audit-events").json() == audit_before


def test_bulk_asset_update_requires_current_versions(client: TestClient) -> None:
    created_assets = []
    for index in range(2):
        response = client.post(
            "/api/v1/assets",
            json={
                "name": f"Bulk versioned asset {index}",
                "asset_tag": f"BULK-VERSION-{index}",
                "category": "Laptop",
            },
        )
        assert response.status_code == 201
        created_assets.append(response.json())

    ids = [item["id"] for item in created_assets]
    versions = {item["id"]: item["version"] for item in created_assets}
    fresh = client.patch(
        f"/api/v1/assets/{ids[0]}",
        json={"expected_version": versions[ids[0]], "name": "Changed elsewhere"},
    )
    assert fresh.status_code == 200

    audit_before = client.get("/api/v1/audit-events").json()
    stale_bulk = client.patch(
        "/api/v1/assets/bulk",
        json={"asset_ids": ids, "versions": versions, "status": "in_repair"},
    )
    assert stale_bulk.status_code == 409

    stored = client.get("/api/v1/assets").json()
    for asset_id in ids:
        asset = next(item for item in stored if item["id"] == asset_id)
        assert asset["status"] == "active"
    changed = next(item for item in stored if item["id"] == ids[0])
    assert changed["name"] == "Changed elsewhere"
    assert changed["version"] == 2
    assert client.get("/api/v1/audit-events").json() == audit_before


def test_department_update_uses_optimistic_locking_and_rejects_stale_versions(
    client: TestClient,
) -> None:
    created = client.post(
        "/api/v1/departments",
        json={"name": "Versioned Department", "code": "VERSIONED"},
    )
    assert created.status_code == 201
    department = created.json()
    assert department["version"] == 1

    updated = client.patch(
        f"/api/v1/departments/{department['id']}",
        json={"expected_version": 1, "name": "Updated Department"},
    )
    assert updated.status_code == 200
    assert updated.json()["version"] == 2

    audit_before = client.get("/api/v1/audit-events").json()
    stale = client.patch(
        f"/api/v1/departments/{department['id']}",
        json={"expected_version": 1, "name": "Stale overwrite"},
    )
    assert stale.status_code == 409
    assert "changed" in stale.json()["detail"].lower()

    stored = client.get("/api/v1/departments").json()
    assert stored[0]["name"] == "Updated Department"
    assert stored[0]["version"] == 2
    assert client.get("/api/v1/audit-events").json() == audit_before


def test_department_update_requires_expected_version(client: TestClient) -> None:
    created = client.post(
        "/api/v1/departments",
        json={"name": "Version Required", "code": "VERSION-REQ"},
    )
    assert created.status_code == 201

    response = client.patch(
        f"/api/v1/departments/{created.json()['id']}",
        json={"name": "Missing version"},
    )
    assert response.status_code == 422



def test_user_optimistic_locking_rejects_a_real_concurrent_write() -> None:
    from sqlalchemy.orm.exc import StaleDataError

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(
        bind=test_engine, autoflush=False, expire_on_commit=False
    )
    Base.metadata.create_all(bind=test_engine)
    try:
        with TestingSessionLocal() as setup_session:
            organization = Organization(name="Concurrency Test")
            setup_session.add(organization)
            setup_session.flush()
            user = User(
                organization_id=organization.id,
                email="concurrent.user@example.com",
                full_name="Original Name",
                password_hash=password_hash.hash("concurrency-test-password-123"),
                role=UserRole.editor,
            )
            setup_session.add(user)
            setup_session.commit()
            user_id = user.id

        with TestingSessionLocal() as first_session, TestingSessionLocal() as second_session:
            first_copy = first_session.get(User, user_id)
            second_copy = second_session.get(User, user_id)
            assert first_copy is not None
            assert second_copy is not None
            assert first_copy.version == second_copy.version == 1

            first_copy.full_name = "First Writer"
            first_session.commit()

            second_copy.full_name = "Stale Writer"
            with pytest.raises(StaleDataError):
                second_session.commit()
            second_session.rollback()

        with TestingSessionLocal() as verify_session:
            stored = verify_session.get(User, user_id)
            assert stored is not None
            assert stored.full_name == "First Writer"
            assert stored.version == 2
    finally:
        Base.metadata.drop_all(bind=test_engine)
        test_engine.dispose()



def test_all_versioned_entities_reject_real_concurrent_writes() -> None:
    from sqlalchemy.orm.exc import StaleDataError

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(
        bind=test_engine, autoflush=False, expire_on_commit=False
    )
    Base.metadata.create_all(bind=test_engine)
    try:
        with TestingSessionLocal() as setup_session:
            organization = Organization(name="All Models Concurrency")
            setup_session.add(organization)
            setup_session.flush()

            user = User(
                organization_id=organization.id,
                email="all-models-admin@example.com",
                full_name="Original User",
                password_hash=password_hash.hash("all-models-test-password-123"),
                role=UserRole.admin,
            )
            asset = main.Asset(
                organization_id=organization.id,
                name="Original Asset",
                asset_tag="CONCURRENT-ASSET-001",
                category="Laptop",
            )
            setup_session.add_all([user, asset])
            setup_session.flush()

            entities = [
                (asset, "name", "Updated Asset"),
                (
                    main.SoftwareLicense(
                        organization_id=organization.id,
                        name="Original License",
                        license_type=main.SoftwareLicenseType.subscription,
                        seats_total=10,
                        seats_used=1,
                    ),
                    "name",
                    "Updated License",
                ),
                (
                    main.MaintenanceRequest(
                        organization_id=organization.id,
                        asset_id=asset.id,
                        requested_by_user_id=user.id,
                        title="Original Request",
                        description="Concurrency test request",
                        priority=main.MaintenancePriority.normal,
                        status=main.MaintenanceStatus.open,
                    ),
                    "title",
                    "Updated Request",
                ),
                (
                    main.Department(
                        organization_id=organization.id,
                        name="Original Department",
                        code="CONCURRENT-DEPT",
                    ),
                    "name",
                    "Updated Department",
                ),
                (
                    main.Employee(
                        organization_id=organization.id,
                        employee_number="CONCURRENT-EMP-001",
                        full_name="Original Employee",
                        email="concurrent.employee@example.com",
                    ),
                    "full_name",
                    "Updated Employee",
                ),
                (user, "full_name", "Updated User"),
            ]
            setup_session.add_all([entity for entity, _, _ in entities[1:]])
            setup_session.commit()
            entity_ids = [
                (type(entity), entity.id, field, new_value)
                for entity, field, new_value in entities
            ]

        for model, entity_id, field, new_value in entity_ids:
            with TestingSessionLocal() as first_session, TestingSessionLocal() as second_session:
                first_copy = first_session.get(model, entity_id)
                second_copy = second_session.get(model, entity_id)
                assert first_copy is not None
                assert second_copy is not None

                first_version = getattr(first_copy, "record_version", None)
                version_field = "record_version" if first_version is not None else "version"
                assert getattr(first_copy, version_field) == getattr(second_copy, version_field) == 1

                setattr(first_copy, field, new_value)
                first_session.commit()

                setattr(second_copy, field, f"Stale {new_value}")
                with pytest.raises(StaleDataError):
                    second_session.commit()
                second_session.rollback()

            with TestingSessionLocal() as verify_session:
                stored = verify_session.get(model, entity_id)
                assert stored is not None
                assert getattr(stored, field) == new_value
                assert getattr(stored, version_field) == 2
    finally:
        Base.metadata.drop_all(bind=test_engine)
        test_engine.dispose()


def test_department_create_audit_failure_rolls_back_department(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    existing_events = client.get("/api/v1/audit-events").json()

    def fail_audit(*args, **kwargs):
        raise IntegrityError("insert audit event", {}, Exception("simulated audit failure"))

    monkeypatch.setattr(main, "record_audit", fail_audit)
    response = client.post(
        "/api/v1/departments",
        json={"name": "Must Roll Back", "code": "ROLLBACK"},
    )

    assert response.status_code == 409
    assert client.get("/api/v1/departments").json() == []
    assert client.get("/api/v1/audit-events").json() == existing_events


def test_bulk_asset_update_audit_failure_rolls_back_all_assets(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    created_assets = []
    for index in range(2):
        response = client.post(
            "/api/v1/assets",
            json={
                "name": f"Atomic bulk asset {index}",
                "asset_tag": f"BULK-ATOMIC-{index}",
                "category": "Server",
            },
        )
        assert response.status_code == 201
        created_assets.append(response.json())

    asset_ids = [asset["id"] for asset in created_assets]
    versions = {asset["id"]: asset["version"] for asset in created_assets}
    before_events = client.get("/api/v1/audit-events").json()
    original_record_audit = main.record_audit
    calls = 0

    def fail_on_second_audit(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise IntegrityError(
                "insert audit event", {}, Exception("simulated second audit failure")
            )
        return original_record_audit(*args, **kwargs)

    monkeypatch.setattr(main, "record_audit", fail_on_second_audit)
    response = client.patch(
        "/api/v1/assets/bulk",
        json={
            "asset_ids": asset_ids,
            "versions": versions,
            "status": "in_repair",
            "assigned_to": "Atomicity Test",
        },
    )

    assert response.status_code == 409
    assert calls == 2

    for asset in created_assets:
        current = client.get(f"/api/v1/assets/{asset['id']}").json()
        assert current["status"] == "active"
        assert current["assigned_to"] is None
        assert current["version"] == asset["version"]
        history = client.get(f"/api/v1/assets/{asset['id']}/history").json()
        assert [event["action"] for event in history] == ["asset.created"]

    assert client.get("/api/v1/audit-events").json() == before_events
