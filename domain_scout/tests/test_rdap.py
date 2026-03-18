"""Tests for RDAP source — unit tests with mocks and helpers."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from domain_scout.config import ScoutConfig
from domain_scout.sources.rdap import RDAP_SKIP_TLDS, RDAPLookup, _RDAPCircuitBreaker


def _make_httpx_mock(json_payload: Any = None, status_code: int = 200) -> AsyncMock:
    """Build a mock httpx.AsyncClient returning the given JSON response."""
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.raise_for_status = MagicMock()
    if status_code >= 400:
        mock_request = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}", request=mock_request, response=mock_response,
        )

    if json_payload is not None:
        mock_response.json.return_value = json_payload
    else:
        mock_response.json.return_value = {}

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_response
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


class TestRDAPLookup:
    """Unit tests for RDAPLookup class."""

    @pytest.mark.asyncio
    async def test_get_registrant_org_success(self) -> None:
        """Test successful retrieval of registrant organization."""
        config = ScoutConfig()
        rdap = RDAPLookup(config)

        # Sample RDAP response with registrant entity
        mock_data = {
            "entities": [
                {
                    "roles": ["registrant"],
                    "vcardArray": [
                        "vcard",
                        [["version", {}, "text", "4.0"], ["org", {}, "text", "Example Corp"]],
                    ],
                }
            ]
        }

        mock_client = _make_httpx_mock(mock_data)

        with patch("domain_scout.sources.rdap.httpx.AsyncClient", return_value=mock_client):
            org = await rdap.get_registrant_org("example.com")

        assert org == "Example Corp"

    @pytest.mark.asyncio
    async def test_get_registrant_org_fallback_fn(self) -> None:
        """Test fallback to formatted name (fn) if org is missing."""
        config = ScoutConfig()
        rdap = RDAPLookup(config)

        mock_data = {
            "entities": [
                {
                    "roles": ["registrant"],
                    "vcardArray": [
                        "vcard",
                        [["version", {}, "text", "4.0"], ["fn", {}, "text", "John Doe"]],
                    ],
                }
            ]
        }

        mock_client = _make_httpx_mock(mock_data)

        with patch("domain_scout.sources.rdap.httpx.AsyncClient", return_value=mock_client):
            org = await rdap.get_registrant_org("example.com")

        assert org == "John Doe"

    @pytest.mark.asyncio
    async def test_get_registrant_org_fallback_toplevel(self) -> None:
        """Test fallback to top-level entity if registrant entity is missing."""
        config = ScoutConfig()
        rdap = RDAPLookup(config)

        mock_data = {
            "entities": [
                {
                    "roles": ["registrar"],  # Not registrant
                    "vcardArray": [
                        "vcard",
                        [["version", {}, "text", "4.0"], ["org", {}, "text", "Top Level Corp"]],
                    ],
                }
            ]
        }

        mock_client = _make_httpx_mock(mock_data)

        with patch("domain_scout.sources.rdap.httpx.AsyncClient", return_value=mock_client):
            org = await rdap.get_registrant_org("example.com")

        assert org == "Top Level Corp"

    @pytest.mark.asyncio
    async def test_get_registrant_org_nested_entity(self) -> None:
        """Test finding registrant entity nested inside another entity."""
        config = ScoutConfig()
        rdap = RDAPLookup(config)

        mock_data = {
            "entities": [
                {
                    "roles": ["registrar"],
                    "entities": [
                        {
                            "roles": ["registrant"],
                            "vcardArray": [
                                "vcard",
                                [
                                    ["version", {}, "text", "4.0"],
                                    ["org", {}, "text", "Nested Corp"],
                                ],
                            ],
                        }
                    ],
                }
            ]
        }

        mock_client = _make_httpx_mock(mock_data)

        with patch("domain_scout.sources.rdap.httpx.AsyncClient", return_value=mock_client):
            org = await rdap.get_registrant_org("example.com")

        assert org == "Nested Corp"

    @pytest.mark.asyncio
    async def test_get_registrant_org_http_error(self) -> None:
        """Test handling of HTTP errors."""
        config = ScoutConfig()
        rdap = RDAPLookup(config)

        mock_client = _make_httpx_mock(None, status_code=404)

        with patch("domain_scout.sources.rdap.httpx.AsyncClient", return_value=mock_client):
            org = await rdap.get_registrant_org("example.com")

        assert org is None

    @pytest.mark.asyncio
    async def test_get_registrant_info_success(self) -> None:
        """Test successful retrieval of registrant info."""
        config = ScoutConfig()
        rdap = RDAPLookup(config)

        mock_data = {
            "entities": [
                {
                    "roles": ["registrant"],
                    "vcardArray": [
                        "vcard",
                        [
                            ["version", {}, "text", "4.0"],
                            ["org", {}, "text", "Example Corp"],
                            ["fn", {}, "text", "John Doe"],
                            ["adr", {}, "text", ["", "", "", "", "", "", "US"]],
                        ],
                    ],
                }
            ]
        }

        mock_client = _make_httpx_mock(mock_data)

        with patch("domain_scout.sources.rdap.httpx.AsyncClient", return_value=mock_client):
            info = await rdap.get_registrant_info("example.com")

        assert info["org"] == "Example Corp"
        assert info["name"] == "John Doe"
        assert info["country"] == "US"

    @pytest.mark.asyncio
    async def test_get_registrant_info_http_error(self) -> None:
        """Test handling of HTTP errors in get_registrant_info."""
        config = ScoutConfig()
        rdap = RDAPLookup(config)

        mock_client = _make_httpx_mock(None, status_code=500)

        with patch("domain_scout.sources.rdap.httpx.AsyncClient", return_value=mock_client):
            info = await rdap.get_registrant_info("example.com")

        assert info["org"] is None
        assert info["name"] is None
        assert info["country"] is None

    def test_extract_org_missing_vcard(self) -> None:
        """Test extraction when vCard is missing or malformed."""
        # Using class method directly since logic is stateless

        # Missing vcardArray
        data_missing: dict[str, object] = {"entities": [{"roles": ["registrant"]}]}
        assert RDAPLookup._extract_org(data_missing) is None

        # Malformed vcardArray (not a list)
        data_malformed: dict[str, object] = {
            "entities": [{"roles": ["registrant"], "vcardArray": "invalid"}]
        }
        assert RDAPLookup._extract_org(data_malformed) is None

        # Malformed vcardArray (too short)
        data_short: dict[str, object] = {
            "entities": [{"roles": ["registrant"], "vcardArray": ["vcard"]}]
        }
        assert RDAPLookup._extract_org(data_short) is None

    def test_extract_country_malformed_adr(self) -> None:
        """Test extraction of country with malformed address."""
        # Malformed adr (not a list)
        data_malformed: dict[str, object] = {
            "entities": [
                {"roles": ["registrant"], "vcardArray": ["vcard", [["adr", {}, "text", "invalid"]]]}
            ]
        }
        assert RDAPLookup._extract_country(data_malformed) is None

        # Malformed adr (list too short)
        data_short: dict[str, object] = {
            "entities": [
                {"roles": ["registrant"], "vcardArray": ["vcard", [["adr", {}, "text", ["US"]]]]}
            ]
        }
        assert RDAPLookup._extract_country(data_short) is None

    def test_extract_name_no_registrant(self) -> None:
        """Test extract_name when no registrant entity is found."""
        data: dict[str, object] = {
            "entities": [
                {
                    "roles": ["registrar"],
                    "vcardArray": ["vcard", [["fn", {}, "text", "Registrar Name"]]],
                }
            ]
        }
        # extract_name only looks for registrant entity
        assert RDAPLookup._extract_name(data) is None


class TestRDAPSkipTLDs:
    """Tests for RDAP ccTLD skip logic."""

    def test_skip_set_contains_expected_tlds(self) -> None:
        """Verify known unsupported TLDs are in the skip set."""
        for tld in ("it", "de", "jp", "cn", "ru", "edu", "io", "us"):
            assert tld in RDAP_SKIP_TLDS, f"{tld} missing from RDAP_SKIP_TLDS"

    def test_skip_set_excludes_supported_tlds(self) -> None:
        """Verify TLDs known to have RDAP are NOT in the skip set."""
        for tld in ("com", "net", "org", "uk", "au", "br", "fr", "nl"):
            assert tld not in RDAP_SKIP_TLDS, f"{tld} should not be in RDAP_SKIP_TLDS"

    @pytest.mark.asyncio
    async def test_skipped_tld_returns_none_without_http(self) -> None:
        """A .it domain must return None without making an HTTP request."""
        config = ScoutConfig()
        rdap = RDAPLookup(config)

        with patch("domain_scout.sources.rdap.httpx.AsyncClient") as mock_cls:
            org = await rdap.get_registrant_org("example.it")

        assert org is None
        mock_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_skipped_tld_info_returns_nulls_without_http(self) -> None:
        """get_registrant_info for a skipped TLD returns null dict, no HTTP."""
        config = ScoutConfig()
        rdap = RDAPLookup(config)

        with patch("domain_scout.sources.rdap.httpx.AsyncClient") as mock_cls:
            info = await rdap.get_registrant_info("example.de")

        assert info == {"org": None, "name": None, "country": None}
        mock_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_supported_tld_makes_http_request(self) -> None:
        """A .com domain must proceed with the normal HTTP lookup."""
        config = ScoutConfig()
        rdap = RDAPLookup(config)

        mock_data: dict[str, object] = {
            "entities": [
                {
                    "roles": ["registrant"],
                    "vcardArray": [
                        "vcard",
                        [["version", {}, "text", "4.0"], ["org", {}, "text", "Test Corp"]],
                    ],
                }
            ]
        }
        mock_client = _make_httpx_mock(mock_data)

        with patch("domain_scout.sources.rdap.httpx.AsyncClient", return_value=mock_client):
            org = await rdap.get_registrant_org("example.com")

        assert org == "Test Corp"
        mock_client.get.assert_called_once()


class TestRDAPCircuitBreaker:
    """Tests for the RDAP circuit breaker."""

    def test_starts_closed(self) -> None:
        cb = _RDAPCircuitBreaker(failure_threshold=3, recovery_timeout=30.0)
        assert cb.state == "closed"
        assert cb.should_allow() is True

    def test_opens_after_threshold_failures(self) -> None:
        cb = _RDAPCircuitBreaker(failure_threshold=3, recovery_timeout=30.0)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == "closed"
        cb.record_failure()
        assert cb.state == "open"
        assert cb.should_allow() is False

    def test_half_open_after_recovery_timeout(self) -> None:
        cb = _RDAPCircuitBreaker(failure_threshold=1, recovery_timeout=0.0)
        cb.record_failure()
        assert cb.state == "open"
        # With 0s recovery, immediately transitions to half_open
        assert cb.should_allow() is True
        assert cb.state == "half_open"

    def test_half_open_success_closes(self) -> None:
        cb = _RDAPCircuitBreaker(failure_threshold=1, recovery_timeout=0.0)
        cb.record_failure()
        cb.should_allow()  # transitions to half_open
        cb.record_success()
        assert cb.state == "closed"

    def test_half_open_failure_reopens(self) -> None:
        cb = _RDAPCircuitBreaker(failure_threshold=1, recovery_timeout=0.0)
        cb.record_failure()
        cb.should_allow()  # transitions to half_open
        cb.record_failure()
        assert cb.state == "open"

    def test_success_resets_failure_count(self) -> None:
        cb = _RDAPCircuitBreaker(failure_threshold=3, recovery_timeout=30.0)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        # 2 failures + success + 1 failure should NOT trip (count reset)
        cb.record_failure()
        assert cb.state == "closed"

    def test_reset(self) -> None:
        cb = _RDAPCircuitBreaker(failure_threshold=1, recovery_timeout=30.0)
        cb.record_failure()
        assert cb.state == "open"
        cb.reset()
        assert cb.state == "closed"
        assert cb.should_allow() is True


class TestRDAPRateLimiting:
    """Tests for RDAP semaphore and circuit breaker integration."""

    def setup_method(self) -> None:
        """Reset class-level state between tests."""
        RDAPLookup._breaker = None
        RDAPLookup._semaphore = None

    @pytest.mark.asyncio
    async def test_circuit_breaker_skips_when_open(self) -> None:
        """When breaker is open, _query returns empty dict without HTTP call."""
        config = ScoutConfig(rdap_cb_failure_threshold=1, rdap_cb_recovery_timeout=999.0)
        rdap = RDAPLookup(config)

        mock_client = _make_httpx_mock(status_code=500)
        with patch("domain_scout.sources.rdap.httpx.AsyncClient", return_value=mock_client):
            # First call fails (5xx) and trips the breaker
            result1 = await rdap.get_registrant_org("fail.com")
            assert result1 is None

            # Second call should be skipped (breaker open)
            mock_client.get.reset_mock()
            result2 = await rdap.get_registrant_org("another.com")
            assert result2 is None
            mock_client.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_404_does_not_trip_circuit_breaker(self) -> None:
        """404 is normal (domain not in RDAP) and should not trip the breaker."""
        config = ScoutConfig(rdap_cb_failure_threshold=1, rdap_cb_recovery_timeout=999.0)
        rdap = RDAPLookup(config)

        mock_client = _make_httpx_mock(status_code=404)
        with patch("domain_scout.sources.rdap.httpx.AsyncClient", return_value=mock_client):
            # Three 404s should NOT trip the breaker
            for _ in range(3):
                await rdap.get_registrant_org("missing.com")

            assert RDAPLookup._breaker is not None
            assert RDAPLookup._breaker.state == "closed"

    @pytest.mark.asyncio
    async def test_5xx_trips_circuit_breaker(self) -> None:
        """5xx errors should trip the breaker."""
        config = ScoutConfig(rdap_cb_failure_threshold=2, rdap_cb_recovery_timeout=999.0)
        rdap = RDAPLookup(config)

        mock_client = _make_httpx_mock(status_code=500)
        with patch("domain_scout.sources.rdap.httpx.AsyncClient", return_value=mock_client):
            await rdap.get_registrant_org("fail1.com")
            await rdap.get_registrant_org("fail2.com")

            assert RDAPLookup._breaker is not None
            assert RDAPLookup._breaker.state == "open"

    @pytest.mark.asyncio
    async def test_class_level_breaker_shared(self) -> None:
        """Breaker is shared across RDAPLookup instances."""
        config = ScoutConfig(rdap_cb_failure_threshold=2, rdap_cb_recovery_timeout=999.0)
        rdap1 = RDAPLookup(config)
        rdap2 = RDAPLookup(config)

        mock_client = _make_httpx_mock(status_code=500)
        with patch("domain_scout.sources.rdap.httpx.AsyncClient", return_value=mock_client):
            await rdap1.get_registrant_org("fail1.com")
            await rdap2.get_registrant_org("fail2.com")

            # Two failures across different instances should trip the shared breaker
            assert RDAPLookup._breaker is not None
            assert RDAPLookup._breaker.state == "open"

    @pytest.mark.asyncio
    async def test_semaphore_initialized(self) -> None:
        """Semaphore is created with configured concurrency."""
        config = ScoutConfig(max_rdap_concurrent=2)
        RDAPLookup(config)
        assert RDAPLookup._semaphore is not None
        assert RDAPLookup._semaphore._value == 2  # noqa: SLF001
