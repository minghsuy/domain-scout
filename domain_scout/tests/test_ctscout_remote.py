"""Tests for CTScout remote API source."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from domain_scout.config import ScoutConfig
from domain_scout.scout import Scout
from domain_scout.sources.ctscout_remote import (
    CTSCOUT_API_VERSION,
    CTScoutRemoteSource,
    CTScoutSchemaError,
    CTScoutSemanticFallbackUnsupportedError,
)


def _domain(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "org": "Goldman Sachs & Co. LLC",
        "apex_domain": "gs.com",
        "cert_count": 200,
        "subdomain_count": 114,
        "first_seen": "2026-01-01",
        "last_seen": "2026-03-14",
        "is_top_org_for_apex": True,
        "apex_contested": False,
        "apex_bulk_infra": False,
        "dns_verified": True,
    }
    row.update(overrides)
    return row


def _body(domains: list[dict[str, object]]) -> dict[str, object]:
    return {
        "domains": domains,
        "total": len(domains),
        "source": "warehouse",
        "match_type": "exact",
        "org_match_strategy": "substring",
    }


def _make_httpx_mock(
    json_payload: object = None,
    status_code: int = 200,
    api_version: str | None = CTSCOUT_API_VERSION,
) -> AsyncMock:
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
    mock_response.headers = {"X-API-Version": api_version} if api_version is not None else {}

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

        mock_data = _body([_domain()])
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
        assert records[0]["ctscout_attribution"] == {
            "api_version": CTSCOUT_API_VERSION,
            "org": "Goldman Sachs & Co. LLC",
            "apex_domain": "gs.com",
            "match_type": "exact",
            "org_match_strategy": "substring",
            "cert_count": 200,
            "subdomain_count": 114,
            "is_top_org_for_apex": True,
            "apex_contested": False,
            "apex_bulk_infra": False,
            "dns_verified": True,
            "cert_volume_attribution_safe": True,
        }

        # The adapter metadata must survive the synthetic CT record boundary
        # and reach the public EvidenceRecord model.
        processed = Scout(config)._process_org_record(
            records[0],
            "Goldman Sachs",
            "ct_org_match",
        )
        evidence = processed[0][1].evidence[0]
        assert evidence.ctscout_attribution is not None
        assert evidence.ctscout_attribution.api_version == CTSCOUT_API_VERSION
        assert evidence.ctscout_attribution.cert_volume_attribution_safe is True

    @pytest.mark.asyncio
    async def test_search_by_domain_returns_records(self) -> None:
        config = ScoutConfig(ctscout_api_key="ds_free_test")
        source = CTScoutRemoteSource(config)

        mock_data = _body([_domain()])
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

        mock_data = _body(
            [
                _domain(org="Test", apex_domain=None, cert_count=1),
                _domain(org="Test", apex_domain="test.com", cert_count=2),
            ]
        )
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

    @pytest.mark.asyncio
    async def test_golden_worker_contract_fixture(self) -> None:
        """The reviewed Worker response remains fully consumed by this adapter."""
        fixture_path = Path(__file__).with_name("fixtures") / "ctscout_scan_2026-07-11.json"
        fixture = json.loads(fixture_path.read_text())
        source = CTScoutRemoteSource(ScoutConfig(ctscout_api_key="ds_free_test"))
        mock_client = _make_httpx_mock(
            fixture["body"],
            api_version=fixture["headers"]["X-API-Version"],
        )

        with patch(
            "domain_scout.sources.ctscout_remote.httpx.AsyncClient",
            return_value=mock_client,
        ):
            records = await source.search_by_org("Dominator")

        provenance = records[0]["ctscout_attribution"]
        assert isinstance(provenance, dict)
        assert provenance["is_top_org_for_apex"] is True
        assert provenance["apex_contested"] is True
        assert provenance["apex_bulk_infra"] is False
        assert provenance["dns_verified"] is True
        # A contested argmax is still a volume guess, even with 50,000 certs.
        assert provenance["cert_volume_attribution_safe"] is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize("api_version", [None, "2026-07-12"])
    async def test_missing_or_unreviewed_api_version_fails_closed(
        self, api_version: str | None
    ) -> None:
        source = CTScoutRemoteSource(ScoutConfig(ctscout_api_key="ds_free_test"))
        mock_client = _make_httpx_mock(_body([_domain()]), api_version=api_version)

        with (
            patch(
                "domain_scout.sources.ctscout_remote.httpx.AsyncClient",
                return_value=mock_client,
            ),
            pytest.raises(CTScoutSchemaError, match="X-API-Version"),
        ):
            await source.search_by_org("Goldman Sachs")

    @pytest.mark.asyncio
    async def test_unreviewed_domain_field_fails_closed(self) -> None:
        source = CTScoutRemoteSource(ScoutConfig(ctscout_api_key="ds_free_test"))
        mock_client = _make_httpx_mock(_body([_domain(future_attribution_signal="review me")]))

        with (
            patch(
                "domain_scout.sources.ctscout_remote.httpx.AsyncClient",
                return_value=mock_client,
            ),
            pytest.raises(CTScoutSchemaError, match="future_attribution_signal"),
        ):
            await source.search_by_org("Goldman Sachs")

    @pytest.mark.asyncio
    async def test_missing_safety_annotation_fails_closed(self) -> None:
        source = CTScoutRemoteSource(ScoutConfig(ctscout_api_key="ds_free_test"))
        row = _domain()
        del row["dns_verified"]
        mock_client = _make_httpx_mock(_body([row]))

        with (
            patch(
                "domain_scout.sources.ctscout_remote.httpx.AsyncClient",
                return_value=mock_client,
            ),
            pytest.raises(CTScoutSchemaError, match="dns_verified"),
        ):
            await source.search_by_org("Goldman Sachs")

    @pytest.mark.asyncio
    async def test_semantic_fallback_is_explicitly_unsupported(self) -> None:
        source = CTScoutRemoteSource(ScoutConfig(ctscout_api_key="ds_free_test"))
        mock_client = _make_httpx_mock(
            {
                "domains": [],
                "total": 0,
                "source": "warehouse",
                "match_type": "semantic",
                "org_match_strategy": "semantic",
                "empty_reason": "semantic_offered",
                "candidates": [
                    {
                        "org": "Goldman Sachs Group, Inc.",
                        "similarity": 0.91,
                        "top_apex_domain": "goldmansachs.com",
                    }
                ],
            }
        )

        with (
            patch(
                "domain_scout.sources.ctscout_remote.httpx.AsyncClient",
                return_value=mock_client,
            ),
            pytest.raises(
                CTScoutSemanticFallbackUnsupportedError,
                match="corroborate",
            ),
        ):
            await source.search_by_org("Goldman")
