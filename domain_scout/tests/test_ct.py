"""Tests for CT log source — unit tests with mocks and helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from hypothesis import given
from hypothesis import strategies as st

from domain_scout.sources.ct_logs import CTLogSource, extract_base_domain, is_valid_domain


class TestExtractBaseDomain:
    def test_simple(self) -> None:
        assert extract_base_domain("www.example.com") == "example.com"

    def test_wildcard(self) -> None:
        assert extract_base_domain("*.example.com") == "example.com"

    def test_deep_subdomain(self) -> None:
        assert extract_base_domain("a.b.c.example.com") == "example.com"

    def test_cctld(self) -> None:
        assert extract_base_domain("www.example.co.uk") == "example.co.uk"

    def test_bare(self) -> None:
        assert extract_base_domain("example.com") == "example.com"

    def test_trailing_dot(self) -> None:
        assert extract_base_domain("example.com.") == "example.com"

    def test_single_label(self) -> None:
        assert extract_base_domain("localhost") is None

    def test_empty(self) -> None:
        assert extract_base_domain("") is None

    def test_ipv4_returns_none(self) -> None:
        assert extract_base_domain("192.168.1.1") is None

    def test_ipv4_common(self) -> None:
        assert extract_base_domain("10.0.0.1") is None

    def test_ipv4_public(self) -> None:
        assert extract_base_domain("8.8.8.8") is None


class TestIsValidDomain:
    def test_valid(self) -> None:
        assert is_valid_domain("example.com")

    def test_wildcard_only(self) -> None:
        assert not is_valid_domain("*")

    def test_localhost(self) -> None:
        assert not is_valid_domain("localhost")

    def test_ip(self) -> None:
        assert not is_valid_domain("192.168.1.1")

    def test_empty(self) -> None:
        assert not is_valid_domain("")

    def test_wildcard_subdomain(self) -> None:
        assert is_valid_domain("*.example.com")

    def test_single_label(self) -> None:
        assert not is_valid_domain("example")


class TestJsonQueryFields:
    """Verify JSON fallback sets correct field values."""

    @pytest.mark.asyncio
    async def test_json_org_name_is_none(self) -> None:
        """JSON API doesn't provide subject organization — org_name must be None."""
        from unittest.mock import MagicMock

        from domain_scout.config import ScoutConfig

        config = ScoutConfig()
        ct = CTLogSource(config)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = [
            {
                "id": 12345,
                "common_name": "example.com",
                "name_value": "example.com\nwww.example.com",
                "issuer_name": "DigiCert Inc",
                "not_before": "2024-01-01T00:00:00",
                "not_after": "2025-01-01T00:00:00",
            }
        ]

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("domain_scout.sources.ct_logs.httpx.AsyncClient", return_value=mock_client):
            results = await ct._json_query("example.com")

        assert len(results) == 1
        assert results[0]["org_name"] is None
        assert results[0]["subject"] == ""

    @pytest.mark.asyncio
    async def test_json_sans_parsed_from_name_value(self) -> None:
        """SANs should be parsed from name_value (newline-separated)."""
        from unittest.mock import MagicMock

        from domain_scout.config import ScoutConfig

        config = ScoutConfig()
        ct = CTLogSource(config)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = [
            {
                "id": 99999,
                "common_name": "test.example.com",
                "name_value": "test.example.com\nwww.example.com\napi.example.com",
                "issuer_name": "Let's Encrypt",
                "not_before": "2024-06-01T00:00:00",
                "not_after": "2024-09-01T00:00:00",
            }
        ]

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("domain_scout.sources.ct_logs.httpx.AsyncClient", return_value=mock_client):
            results = await ct._json_query("example.com")

        assert len(results) == 1
        sans = results[0]["san_dns_names"]
        assert isinstance(sans, list)
        assert "test.example.com" in sans
        assert "www.example.com" in sans
        assert "api.example.com" in sans


class TestPropertyBased:
    """Property-based tests using hypothesis."""

    @given(
        a=st.integers(min_value=0, max_value=255),
        b=st.integers(min_value=0, max_value=255),
        c=st.integers(min_value=0, max_value=255),
        d=st.integers(min_value=0, max_value=255),
    )
    def test_extract_base_domain_rejects_ipv4(self, a: int, b: int, c: int, d: int) -> None:
        """Any IPv4 address must return None from extract_base_domain."""
        ip = f"{a}.{b}.{c}.{d}"
        assert extract_base_domain(ip) is None
