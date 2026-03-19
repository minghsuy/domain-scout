"""Tests for CTScout remote API source."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from domain_scout.config import ScoutConfig
from domain_scout.sources.ctscout_remote import CTScoutRemoteSource


def _make_httpx_mock(json_payload: object = None, status_code: int = 200) -> AsyncMock:
    """Build a mock httpx.AsyncClient returning the given JSON response."""
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.raise_for_status = MagicMock()
    if status_code >= 400:
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}",
            request=MagicMock(),
            response=mock_response,
        )
    mock_response.json.return_value = json_payload or {}

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


class TestCTScoutRemoteSource:
    """Unit tests for CTScoutRemoteSource."""

    @pytest.mark.asyncio
    async def test_search_by_org_returns_records(self) -> None:
        config = ScoutConfig(ctscout_api_key="ds_free_test")
        source = CTScoutRemoteSource(config)

        mock_data = {
            "domains": [
                {
                    "org": "Goldman Sachs & Co. LLC",
                    "apex_domain": "gs.com",
                    "cert_count": 200,
                    "subdomain_count": 114,
                    "first_seen": "2026-01-01",
                    "last_seen": "2026-03-14",
                },
            ],
            "total": 1,
        }
        mock_client = _make_httpx_mock(mock_data)

        with patch(
            "domain_scout.sources.ctscout_remote.httpx.AsyncClient",
            return_value=mock_client,
        ):
            records = await source.search_by_org("Goldman Sachs")

        assert len(records) == 1
        assert records[0]["org_name"] == "Goldman Sachs & Co. LLC"
        assert records[0]["san_dns_names"] == ["gs.com"]
        assert records[0]["source_type"] == "ctscout_warehouse"
        assert records[0]["cert_count"] == 200

    @pytest.mark.asyncio
    async def test_search_by_domain_returns_records(self) -> None:
        config = ScoutConfig(ctscout_api_key="ds_free_test")
        source = CTScoutRemoteSource(config)

        mock_data = {
            "domains": [
                {
                    "org": "Goldman Sachs & Co. LLC",
                    "apex_domain": "gs.com",
                    "cert_count": 200,
                    "subdomain_count": 114,
                    "first_seen": "2026-01-01",
                    "last_seen": "2026-03-14",
                },
            ],
        }
        mock_client = _make_httpx_mock(mock_data)

        with patch(
            "domain_scout.sources.ctscout_remote.httpx.AsyncClient",
            return_value=mock_client,
        ):
            records = await source.search_by_domain("gs.com")

        assert len(records) == 1
        # Verify seed_domain was sent in the request body
        call_kwargs = mock_client.post.call_args
        assert call_kwargs.kwargs["json"] == {"seed_domain": ["gs.com"]}

    @pytest.mark.asyncio
    async def test_null_apex_domain_skipped(self) -> None:
        config = ScoutConfig(ctscout_api_key="ds_free_test")
        source = CTScoutRemoteSource(config)

        mock_data = {
            "domains": [
                {"org": "Test", "apex_domain": None, "cert_count": 1},
                {"org": "Test", "apex_domain": "test.com", "cert_count": 2},
            ],
        }
        mock_client = _make_httpx_mock(mock_data)

        with patch(
            "domain_scout.sources.ctscout_remote.httpx.AsyncClient",
            return_value=mock_client,
        ):
            records = await source.search_by_org("Test")

        assert len(records) == 1
        assert records[0]["san_dns_names"] == ["test.com"]

    @pytest.mark.asyncio
    async def test_api_error_raises(self) -> None:
        config = ScoutConfig(ctscout_api_key="ds_free_test")
        source = CTScoutRemoteSource(config)

        mock_client = _make_httpx_mock(status_code=500)

        with (
            patch(
                "domain_scout.sources.ctscout_remote.httpx.AsyncClient",
                return_value=mock_client,
            ),
            pytest.raises(httpx.HTTPStatusError),
        ):
            await source.search_by_org("Fail")

    @pytest.mark.asyncio
    async def test_empty_query_returns_empty(self) -> None:
        config = ScoutConfig(ctscout_api_key="ds_free_test")
        source = CTScoutRemoteSource(config)

        records = await source.search_by_org("")
        assert records == []

    @pytest.mark.asyncio
    async def test_get_cert_org_returns_none(self) -> None:
        config = ScoutConfig(ctscout_api_key="ds_free_test")
        source = CTScoutRemoteSource(config)
        assert await source.get_cert_org(12345) is None
