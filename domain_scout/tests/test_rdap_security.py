"""Security tests for RDAPLookup to prevent SSRF and path traversal."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from domain_scout.sources.rdap import RDAPLookup
from domain_scout.config import ScoutConfig

@pytest.fixture
def rdap_lookup():
    config = ScoutConfig()
    return RDAPLookup(config)

@pytest.mark.asyncio
async def test_rdap_query_validates_domain_path_traversal(rdap_lookup):
    """Ensure path traversal attempts raise ValueError in _query."""
    malicious_domains = [
        "../../etc/passwd",
        "example.com/../../",
        "../example.com",
        "example.com/..",
    ]

    for domain in malicious_domains:
        # We test _query directly because get_registrant_org suppresses exceptions
        with pytest.raises(ValueError, match="Invalid domain"):
            await rdap_lookup._query(domain)

@pytest.mark.asyncio
async def test_rdap_query_validates_domain_url_chars(rdap_lookup):
    """Ensure URL control characters raise ValueError in _query."""
    malicious_domains = [
        "http://example.com",
        "example.com?query=1",
        "example.com#fragment",
        "user:pass@example.com",
        "example.com:80",
    ]

    for domain in malicious_domains:
        with pytest.raises(ValueError, match="Invalid domain"):
            await rdap_lookup._query(domain)

@pytest.mark.asyncio
async def test_rdap_query_valid_domain_passes(rdap_lookup):
    """Ensure valid domains are processed correctly."""
    valid_domains = [
        "example.com",
        "sub.example.com",
        "example-domain.com",
        "123.com",
    ]

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {}
        mock_get.return_value = mock_response

        for domain in valid_domains:
            await rdap_lookup.get_registrant_org(domain)

        assert mock_get.call_count == len(valid_domains)
