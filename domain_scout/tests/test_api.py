"""Tests for REST API endpoints."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path
from fastapi.testclient import TestClient

from domain_scout.api import ScanRequest, create_app, get_app
from domain_scout.models import EntityInput
from domain_scout.tests.conftest import mock_result as _mock_result


@pytest.fixture
def client() -> TestClient:
    """Create a test client with no cache."""
    app = create_app(cache=None, max_concurrent=2)
    return TestClient(app)


@pytest.fixture
def mock_discover() -> Iterator[AsyncMock]:
    """Patch Scout.discover_async to return a canned result."""
    with patch(
        "domain_scout.api.Scout.discover_async",
        new_callable=AsyncMock,
        return_value=_mock_result(),
    ) as mocked:
        yield mocked


def _mock_httpx_client(
    *, status_code: int = 200, side_effect: Exception | None = None
) -> AsyncMock:
    """Build a mock httpx.AsyncClient for /ready endpoint tests."""
    mock_client = AsyncMock()
    if side_effect:
        mock_client.get = AsyncMock(side_effect=side_effect)
    else:
        mock_resp = MagicMock()
        mock_resp.status_code = status_code
        mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client


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
        with patch("domain_scout.api.httpx.AsyncClient", return_value=_mock_httpx_client()):
            resp = client.get("/ready")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ready"
        assert "version" in data
        assert data["details"]["crt_sh"] == "ok"

    def test_ready_degraded(self, client: TestClient) -> None:
        """Ready endpoint returns 'degraded' when crt.sh probe fails."""
        mock = _mock_httpx_client(side_effect=ConnectionError("refused"))
        with patch("domain_scout.api.httpx.AsyncClient", return_value=mock):
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


class TestMetricsEndpoint:
    def test_metrics_returns_200(self, client: TestClient) -> None:
        """The /metrics endpoint returns Prometheus text format."""
        resp = client.get("/metrics")
        assert resp.status_code == 200
        assert "text/plain" in resp.headers.get("content-type", "")
        assert "domain_scout_scans_total" in resp.text


class TestGetApp:
    def test_get_app_default(self) -> None:
        """get_app() returns a working FastAPI app with default config."""
        with patch.dict(os.environ, {"DOMAIN_SCOUT_CACHE": "false"}):
            app = get_app()
        test_client = TestClient(app)
        resp = test_client.get("/health")
        assert resp.status_code == 200

    def test_get_app_warehouse_env(self) -> None:
        """get_app() reads warehouse/subsidiaries/local_mode env vars."""
        env = {
            "DOMAIN_SCOUT_CACHE": "false",
            "DOMAIN_SCOUT_WAREHOUSE_PATH": "/opt/warehouse",
            "DOMAIN_SCOUT_SUBSIDIARIES_PATH": "/opt/subs.csv",
            "DOMAIN_SCOUT_LOCAL_MODE": "local_first",
        }
        with patch.dict(os.environ, env, clear=False):
            app = get_app()
        assert app.state.default_warehouse_path == "/opt/warehouse"
        assert app.state.default_subsidiaries_path == "/opt/subs.csv"
        assert app.state.default_local_mode == "local_first"

    def test_get_app_auto_local_first(self) -> None:
        """get_app() auto-enables local_first when warehouse path is set."""
        env = {
            "DOMAIN_SCOUT_CACHE": "false",
            "DOMAIN_SCOUT_WAREHOUSE_PATH": "/opt/warehouse",
        }
        with patch.dict(os.environ, env, clear=False):
            app = get_app()
        assert app.state.default_local_mode == "local_first"

    def test_get_app_invalid_local_mode_ignored(self) -> None:
        """get_app() ignores invalid DOMAIN_SCOUT_LOCAL_MODE values."""
        env = {
            "DOMAIN_SCOUT_CACHE": "false",
            "DOMAIN_SCOUT_LOCAL_MODE": "bogus",
        }
        with patch.dict(os.environ, env, clear=False):
            app = get_app()
        assert app.state.default_local_mode == "disabled"

    def test_get_app_explicit_disabled_respected(self) -> None:
        """Explicit disabled mode is not overridden by auto-enable logic."""
        env = {
            "DOMAIN_SCOUT_CACHE": "false",
            "DOMAIN_SCOUT_LOCAL_MODE": "disabled",
            "DOMAIN_SCOUT_WAREHOUSE_PATH": "/opt/warehouse",
        }
        with patch.dict(os.environ, env, clear=False):
            app = get_app()
        assert app.state.default_local_mode == "disabled"


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

    def test_scan_warehouse_path_traversal(
        self, client: TestClient, mock_discover: AsyncMock
    ) -> None:
        """Scan returns 400 when warehouse_path contains path traversal."""
        resp = client.post(
            "/scan",
            json={
                "entity": {"company_name": "TestCorp"},
                "local_mode": "local_only",
                "warehouse_path": "../../../etc/passwd",
            },
        )
        assert resp.status_code == 400
        assert "Invalid warehouse_path" in resp.json()["detail"]

        # Absolute paths must NOT be rejected as traversal
        resp_absolute = client.post(
            "/scan",
            json={
                "entity": {"company_name": "TestCorp"},
                "local_mode": "local_only",
                "warehouse_path": "/opt/warehouse",
            },
        )
        assert resp_absolute.status_code != 400

    def test_scan_subsidiaries_path_traversal(
        self, client: TestClient, mock_discover: AsyncMock
    ) -> None:
        """Scan returns 400 when subsidiaries_path contains path traversal."""
        resp = client.post(
            "/scan",
            json={
                "entity": {"company_name": "TestCorp"},
                "subsidiaries_path": "../../etc/passwd",
            },
        )
        assert resp.status_code == 400
        assert "Invalid subsidiaries_path" in resp.json()["detail"]


class TestServerDefaults:
    """Tests for server-default configuration via create_app params."""

    @pytest.fixture
    def default_client(self, tmp_path: Path) -> TestClient:
        app = create_app(
            cache=None,
            max_concurrent=2,
            default_warehouse_path=str(tmp_path / "warehouse"),
            default_subsidiaries_path=str(tmp_path / "subs.csv"),
            default_local_mode="local_first",
        )
        return TestClient(app)

    def test_server_defaults_used(
        self, default_client: TestClient, mock_discover: AsyncMock
    ) -> None:
        """Server defaults are applied when request omits local_mode/paths."""
        with patch("domain_scout.api.Scout.__init__", return_value=None):
            resp = default_client.post(
                "/scan",
                json={"entity": {"company_name": "TestCorp"}},
            )
        assert resp.status_code == 200
        assert mock_discover.call_count == 1

    def test_request_overrides_server_defaults(
        self, default_client: TestClient, mock_discover: AsyncMock
    ) -> None:
        """Per-request values override server defaults."""
        with patch("domain_scout.api.Scout.__init__", return_value=None):
            resp = default_client.post(
                "/scan",
                json={
                    "entity": {"company_name": "TestCorp"},
                    "local_mode": "local_only",
                    "warehouse_path": "/other/warehouse",
                },
            )
        assert resp.status_code == 200

    def test_request_disabled_overrides_server_default(
        self,
        mock_discover: AsyncMock,
    ) -> None:
        """Explicit local_mode='disabled' in request overrides server default."""
        app = create_app(
            cache=None,
            max_concurrent=2,
            default_local_mode="local_first",
            default_warehouse_path="/opt/warehouse",
        )
        client = TestClient(app)
        resp = client.post(
            "/scan",
            json={
                "entity": {"company_name": "TestCorp"},
                "local_mode": "disabled",
            },
        )
        assert resp.status_code == 200


class TestAPIKeyAuth:
    @pytest.fixture
    def auth_client(self) -> TestClient:
        app = create_app(cache=None, max_concurrent=2, api_key="secret-key")
        return TestClient(app)

    def test_missing_api_key_unauthorized(self, auth_client: TestClient) -> None:
        """Requests without API key return 401 on protected endpoints."""
        resp = auth_client.post("/scan", json={"entity": {"company_name": "TestCorp"}})
        assert resp.status_code == 401
        assert resp.json()["detail"] == "API Key required"

        resp = auth_client.get("/cache/stats")
        assert resp.status_code == 401

        resp = auth_client.post("/cache/clear")
        assert resp.status_code == 401

        import json

        resp = auth_client.post(
            "/diff",
            json={
                "baseline": json.loads(_mock_result().model_dump_json()),
                "current": json.loads(_mock_result().model_dump_json()),
            },
        )
        assert resp.status_code == 401

    def test_invalid_api_key_unauthorized(self, auth_client: TestClient) -> None:
        """Requests with an invalid API key return 401."""
        resp = auth_client.post(
            "/scan",
            json={"entity": {"company_name": "TestCorp"}},
            headers={"X-API-Key": "wrong-key"},
        )
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Invalid API Key"

    def test_valid_api_key_succeeds(
        self, auth_client: TestClient, mock_discover: AsyncMock
    ) -> None:
        """Requests with a valid API key succeed."""
        resp = auth_client.post(
            "/scan",
            json={"entity": {"company_name": "TestCorp"}},
            headers={"X-API-Key": "secret-key"},
        )
        assert resp.status_code == 200

        resp = auth_client.get("/cache/stats", headers={"X-API-Key": "secret-key"})
        assert resp.status_code == 200

        import json

        resp = auth_client.post(
            "/diff",
            json={
                "baseline": json.loads(_mock_result().model_dump_json()),
                "current": json.loads(_mock_result().model_dump_json()),
            },
            headers={"X-API-Key": "secret-key"},
        )
        assert resp.status_code == 200

    def test_public_endpoints_unaffected(self, auth_client: TestClient) -> None:
        """Endpoints like /health and /ready remain accessible without a key."""
        resp = auth_client.get("/health")
        assert resp.status_code == 200

        with patch("domain_scout.api.httpx.AsyncClient", return_value=_mock_httpx_client()):
            resp = auth_client.get("/ready")
        assert resp.status_code == 200
