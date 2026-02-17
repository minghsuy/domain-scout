"""Tests for REST API endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator
from fastapi.testclient import TestClient

from domain_scout.api import ScanRequest, create_app
from domain_scout.models import EntityInput, RunMetadata, ScoutResult


@pytest.fixture
def client() -> TestClient:
    """Create a test client with no cache."""
    app = create_app(cache=None, max_concurrent=2)
    return TestClient(app)


def _mock_result() -> ScoutResult:
    """Build a minimal ScoutResult for mocking discover_async."""
    return ScoutResult(
        entity=EntityInput(company_name="TestCorp", seed_domain=["test.com"]),
        domains=[],
        run_metadata=RunMetadata(
            tool_version="0.3.0",
            timestamp=datetime.now(UTC),
            elapsed_seconds=1.0,
            domains_found=0,
        ),
    )


@pytest.fixture
def mock_discover() -> Iterator[AsyncMock]:
    """Patch Scout.discover_async to return a canned result."""
    with patch(
        "domain_scout.api.Scout.discover_async",
        new_callable=AsyncMock,
        return_value=_mock_result(),
    ) as mocked:
        yield mocked


class TestHealth:
    def test_health(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "version" in data


class TestReady:
    def test_ready_ok(self, client: TestClient) -> None:
        """Ready endpoint returns 'ready' when crt.sh probe succeeds."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("domain_scout.api.httpx.AsyncClient", return_value=mock_client):
            resp = client.get("/ready")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ready"
        assert "version" in data
        assert data["details"]["crt_sh"] == "ok"

    def test_ready_degraded(self, client: TestClient) -> None:
        """Ready endpoint returns 'degraded' when crt.sh probe fails."""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=ConnectionError("refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("domain_scout.api.httpx.AsyncClient", return_value=mock_client):
            resp = client.get("/ready")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "degraded"
        assert data["details"]["crt_sh"] == "unreachable"


class TestScan:
    def test_scan_basic(self, client: TestClient, mock_discover: AsyncMock) -> None:
        resp = client.post(
            "/scan",
            json={
                "entity": {
                    "company_name": "TestCorp",
                    "seed_domain": ["test.com"],
                }
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["entity"]["company_name"] == "TestCorp"
        assert "run_metadata" in data

    def test_scan_with_profile(self, client: TestClient, mock_discover: AsyncMock) -> None:
        resp = client.post(
            "/scan",
            json={
                "entity": {"company_name": "TestCorp"},
                "profile": "strict",
            },
        )
        assert resp.status_code == 200

    def test_scan_invalid_profile(self, client: TestClient) -> None:
        resp = client.post(
            "/scan",
            json={
                "entity": {"company_name": "TestCorp"},
                "profile": "invalid_profile",
            },
        )
        # Pydantic validates ProfileName literal → 422
        assert resp.status_code == 422

    def test_scan_with_timeout(self, client: TestClient, mock_discover: AsyncMock) -> None:
        resp = client.post(
            "/scan",
            json={
                "entity": {"company_name": "TestCorp"},
                "timeout": 30,
            },
        )
        assert resp.status_code == 200

    def test_scan_with_deep(self, client: TestClient, mock_discover: AsyncMock) -> None:
        resp = client.post(
            "/scan",
            json={
                "entity": {"company_name": "TestCorp"},
                "deep": True,
            },
        )
        assert resp.status_code == 200

    def test_scan_timeout_capped(self, client: TestClient, mock_discover: AsyncMock) -> None:
        """Timeout values are capped at 300 by Pydantic le= constraint."""
        resp = client.post(
            "/scan",
            json={
                "entity": {"company_name": "TestCorp"},
                "timeout": 600,
            },
        )
        assert resp.status_code == 422

    def test_scan_500_on_failure(self, client: TestClient) -> None:
        """Scan returns 500 when discover_async raises."""
        with patch(
            "domain_scout.api.Scout.discover_async",
            new_callable=AsyncMock,
            side_effect=RuntimeError("boom"),
        ):
            resp = client.post(
                "/scan",
                json={"entity": {"company_name": "TestCorp"}},
            )
        assert resp.status_code == 500
        assert "Internal scan error" in resp.json()["detail"]

    def test_scan_429_on_semaphore_timeout(self, client: TestClient) -> None:
        """Scan returns 429 when semaphore acquisition times out."""
        with (
            patch("domain_scout.api._SEMAPHORE_TIMEOUT", 0.01),
            patch(
                "domain_scout.api.Scout.discover_async",
                new_callable=AsyncMock,
                return_value=_mock_result(),
            ),
        ):
            # Create app with 0 concurrent slots — no request can ever acquire
            app = create_app(cache=None, max_concurrent=0)
            zero_client = TestClient(app)
            resp = zero_client.post(
                "/scan",
                json={"entity": {"company_name": "TestCorp"}},
            )
        assert resp.status_code == 429
        assert "Too many concurrent scans" in resp.json()["detail"]


class TestScanRequest:
    def test_minimal(self) -> None:
        req = ScanRequest(entity=EntityInput(company_name="Acme"))
        assert req.entity.company_name == "Acme"
        assert req.profile is None
        assert req.timeout is None
        assert req.deep is False

    def test_full(self) -> None:
        req = ScanRequest(
            entity=EntityInput(
                company_name="Acme",
                seed_domain=["acme.com"],
                location="NYC",
                industry="tech",
            ),
            profile="strict",
            timeout=60,
            deep=True,
        )
        assert req.profile == "strict"
        assert req.timeout == 60
        assert req.deep is True
