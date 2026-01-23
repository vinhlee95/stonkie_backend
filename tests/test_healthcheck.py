"""
Integration tests for healthcheck endpoints.

Tests verify that the API health endpoints are accessible and return expected responses.
"""

from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


class TestHealthcheckEndpoints:
    """Test health check endpoints."""

    def test_health_endpoint(self):
        """Test /api/health endpoint returns ok status."""
        response = client.get("/api/health")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_healthcheck_endpoint(self):
        """Test /api/healthcheck endpoint returns success flag."""
        response = client.get("/api/healthcheck")

        assert response.status_code == 200
        assert response.json() == {"success": True}

    def test_healthcheck_endpoint_response_format(self):
        """Test /api/healthcheck returns proper JSON with boolean value."""
        response = client.get("/api/healthcheck")

        assert response.status_code == 200
        data = response.json()
        assert "success" in data
        assert isinstance(data["success"], bool)
        assert data["success"] is True
