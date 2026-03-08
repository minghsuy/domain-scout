"""Tests for RDAP source — unit tests with mocks and helpers."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from domain_scout.config import ScoutConfig
from domain_scout.sources.rdap import RDAP_SKIP_TLDS, RDAPLookup


def _make_httpx_mock(json_payload: Any = None, status_code: int = 200) -> AsyncMock:
    """Build a mock httpx.AsyncClient returning the given JSON response."""
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.raise_for_status = MagicMock()
    if status_code >= 400:
        mock_response.raise_for_status.side_effect = Exception(f"HTTP {status_code}")

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
