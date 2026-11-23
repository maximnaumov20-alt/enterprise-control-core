from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import Base, app, get_session


@pytest.fixture()
def client() -> Generator[TestClient, None, None]:
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(
        bind=test_engine, autoflush=False, expire_on_commit=False
    )
    Base.metadata.create_all(bind=test_engine)

    def override_get_session():
        with TestingSessionLocal() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=test_engine)
        test_engine.dispose()


def test_health_endpoint(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_asset_create_list_update_delete(client: TestClient) -> None:
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

    deleted = client.delete(f"/api/v1/assets/{asset_id}")
    assert deleted.status_code == 204
    assert client.get(f"/api/v1/assets/{asset_id}").status_code == 404


def test_duplicate_asset_tag_is_rejected(client: TestClient) -> None:
    payload = {
        "name": "Office laptop",
        "asset_tag": "ECC-LT-002",
        "category": "Laptop",
    }
    assert client.post("/api/v1/assets", json=payload).status_code == 201
    duplicate = client.post("/api/v1/assets", json=payload)
    assert duplicate.status_code == 409


def test_required_asset_fields_cannot_be_cleared(client: TestClient) -> None:
    created = client.post(
        "/api/v1/assets",
        json={"name": "Office laptop", "asset_tag": "ECC-LT-003", "category": "Laptop"},
    )
    asset_id = created.json()["id"]
    response = client.patch(f"/api/v1/assets/{asset_id}", json={"name": None})
    assert response.status_code == 422
