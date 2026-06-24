import pytest


async def test_health_returns_ok(client):
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["database"] == "connected"


async def test_health_response_shape(client):
    response = await client.get("/health")
    data = response.json()
    assert "status" in data
    assert "database" in data
