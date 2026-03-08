import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from domain_scout.config import ScoutConfig
from domain_scout.scout import Scout

@pytest.fixture
def mock_ct():
    ct = AsyncMock()
    ct.search_by_domain = AsyncMock()
    ct.search_by_org = AsyncMock()
    return ct

@pytest.fixture
def mock_rdap():
    rdap = AsyncMock()
    rdap.get_registrant_org = AsyncMock()
    return rdap

@pytest.fixture
def mock_dns():
    dns = AsyncMock()
    dns.resolves = AsyncMock()
    return dns

@pytest.fixture
def scout(mock_ct, mock_rdap, mock_dns):
    config = ScoutConfig()
    scout = Scout(config=config)
    scout._ct = mock_ct
    scout._rdap = mock_rdap
    scout._dns = mock_dns
    return scout

@pytest.mark.asyncio
async def test_validate_seed_success(scout, mock_dns, mock_rdap, mock_ct):
    mock_dns.resolves.return_value = True
    mock_rdap.get_registrant_org.return_value = "Example Corp"
    mock_ct.search_by_domain.return_value = [
        {"org_name": "Example Corp", "san_dns_names": ["example.com", "other.com"]}
    ]

    errors = []
    result = await scout._validate_seed("example.com", "Example Corp", ["example.com", "other.com"], errors)

    assert result["assessment"] == "confirmed"
    assert result["org_name"] == "Example Corp"
    assert "other.com" in result["co_hosted_seeds"]
    assert len(errors) == 0

@pytest.mark.asyncio
async def test_validate_seed_rdap_failure_still_resolves(scout, mock_dns, mock_rdap, mock_ct):
    mock_dns.resolves.return_value = True
    mock_rdap.get_registrant_org.side_effect = Exception("RDAP error")
    mock_ct.search_by_domain.return_value = []

    errors = []
    result = await scout._validate_seed("example.com", "Unrelated Corp", ["example.com"], errors)

    assert result["assessment"] == "suspicious"
    assert len(errors) == 1
    assert "RDAP lookup failed" in errors[0]

@pytest.mark.asyncio
async def test_validate_seed_invalid(scout, mock_dns, mock_rdap, mock_ct):
    mock_dns.resolves.return_value = False
    mock_rdap.get_registrant_org.return_value = None
    mock_ct.search_by_domain.return_value = []

    errors = []
    result = await scout._validate_seed("invalid.local", "Some Corp", ["invalid.local"], errors)

    assert result["assessment"] == "invalid"

@pytest.mark.asyncio
async def test_strategy_org_search_success(scout, mock_ct):
    mock_ct.search_by_org.return_value = [
        {"org_name": "Target Corp", "san_dns_names": ["target1.com"], "common_name": "target2.com"}
    ]

    errors = []
    results = await scout._strategy_org_search("Target Corp", errors)

    assert len(errors) == 0
    assert len(results) == 2
    domains = [r[0] for r in results]
    assert "target1.com" in domains
    assert "target2.com" in domains

@pytest.mark.asyncio
async def test_strategy_org_search_failure(scout, mock_ct):
    mock_ct.search_by_org.side_effect = Exception("CT Search Failed")

    errors = []
    results = await scout._strategy_org_search("Target Corp", errors)

    assert len(errors) == 1
    assert "CT org search failed" in errors[0]
    assert len(results) == 0

@pytest.mark.asyncio
async def test_strategy_org_search_low_similarity(scout, mock_ct):
    # Setup a result with an org name that is very different
    mock_ct.search_by_org.return_value = [
        {"org_name": "Completely Unrelated Inc", "san_dns_names": ["unrelated.com"]}
    ]

    errors = []
    results = await scout._strategy_org_search("Target Corp", errors)

    assert len(errors) == 0
    assert len(results) == 0

@pytest.mark.asyncio
async def test_strategy_seed_expansion_success(scout, mock_ct):
    mock_ct.search_by_domain.return_value = [
        {
            "org_name": "Target Corp",
            "san_dns_names": ["sub.example.com", "other-related.com"],
            "common_name": "example.com"
        }
    ]

    errors = []
    results = await scout._strategy_seed_expansion("example.com", "Target Corp", errors)

    assert len(errors) == 0
    domains = [r[0] for r in results]
    assert "example.com" in domains
    assert "other-related.com" in domains

    # Check accumulated evidence
    for domain, accum in results:
        if domain == "example.com":
            assert f"ct_seed_subdomain:example.com" in accum.sources
        elif domain == "other-related.com":
            assert f"ct_san_expansion:example.com" in accum.sources

@pytest.mark.asyncio
async def test_strategy_seed_expansion_cdn_cert(scout, mock_ct):
    # Simulate a CDN cert with many unique base domains and no org match
    sans = [f"tenant{i}.com" for i in range(15)] + ["example.com"]
    mock_ct.search_by_domain.return_value = [
        {
            "org_name": "Cloudflare, Inc.",
            "san_dns_names": sans,
        }
    ]

    errors = []
    results = await scout._strategy_seed_expansion("example.com", "Target Corp", errors)

    assert len(errors) == 0
    domains = [r[0] for r in results]
    # The seed domain itself should be returned
    assert "example.com" in domains
    # But the CDN tenant domains should be filtered out
    assert "tenant1.com" not in domains

@pytest.mark.asyncio
async def test_strategy_seed_expansion_failure(scout, mock_ct):
    mock_ct.search_by_domain.side_effect = Exception("CT Domain Search Failed")

    errors = []
    results = await scout._strategy_seed_expansion("example.com", "Target Corp", errors)

    assert len(errors) == 1
    assert "CT seed expansion failed" in errors[0]
    assert len(results) == 0

@pytest.mark.asyncio
async def test_strategy_domain_guess_success(scout, mock_dns):
    # scout._dns.bulk_resolve expects a list of domains and returns a dict mapping domain to boolean
    def mock_bulk_resolve(domains):
        # We simulate that only 'target.com' and 'targetsan.com' resolve
        resolving = ["target.com", "targetsan.com"]
        return {d: (d in resolving) for d in domains}

    mock_dns.bulk_resolve.side_effect = mock_bulk_resolve

    errors = []
    results = await scout._strategy_domain_guess("Target Corp", "San Francisco, CA", errors)

    assert len(errors) == 0
    domains = [r[0] for r in results]
    assert "target.com" in domains
    assert "targetsan.com" in domains
    assert "target.net" not in domains  # Was not in resolving list

    for domain, accum in results:
        assert accum.resolves is True
        assert "dns_guess" in accum.sources
