"""Unit tests for Scout class internal methods."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from domain_scout.config import ScoutConfig
from domain_scout.scout import Scout


@pytest.fixture
def mock_ct():
    ct = AsyncMock()
    ct.search_by_domain = AsyncMock(return_value=[])
    ct.search_by_org = AsyncMock(return_value=[])
    return ct


@pytest.fixture
def mock_rdap():
    rdap = AsyncMock()
    rdap.get_registrant_org = AsyncMock(return_value=None)
    return rdap


@pytest.fixture
def mock_dns():
    dns = AsyncMock()
    dns.resolves = AsyncMock(return_value=True)
    dns.bulk_resolve = AsyncMock(return_value={})
    return dns


@pytest.fixture
def scout(mock_ct, mock_rdap, mock_dns):
    """Build a Scout with injected mock dependencies via patching."""
    with (
        patch("domain_scout.scout.CTLogSource", return_value=mock_ct),
        patch("domain_scout.scout.RDAPLookup", return_value=mock_rdap),
        patch("domain_scout.scout.DNSChecker", return_value=mock_dns),
    ):
        s = Scout(config=ScoutConfig())
    return s


# --- _validate_seed ---


@pytest.mark.asyncio
async def test_validate_seed_confirmed(scout, mock_dns, mock_rdap, mock_ct):
    mock_dns.resolves.return_value = True
    mock_rdap.get_registrant_org.return_value = "Example Corp"
    mock_ct.search_by_domain.return_value = [
        {"org_name": "Example Corp", "san_dns_names": ["example.com", "other.com"]}
    ]

    errors: list[str] = []
    result = await scout._validate_seed("example.com", "Example Corp", ["example.com", "other.com"], errors)

    assert result["assessment"] == "confirmed"
    assert result["org_name"] is not None
    assert not errors


@pytest.mark.asyncio
async def test_validate_seed_rdap_failure_records_error(scout, mock_dns, mock_rdap, mock_ct):
    mock_dns.resolves.return_value = True
    mock_rdap.get_registrant_org.side_effect = Exception("connection timeout")
    mock_ct.search_by_domain.return_value = []

    errors: list[str] = []
    result = await scout._validate_seed("example.com", "Unrelated Corp", ["example.com"], errors)

    # Domain resolves but no org match -> suspicious
    assert result["assessment"] == "suspicious"
    assert len(errors) == 1
    assert "RDAP" in errors[0]


@pytest.mark.asyncio
async def test_validate_seed_invalid_when_unresolvable(scout, mock_dns, mock_rdap, mock_ct):
    mock_dns.resolves.return_value = False
    mock_rdap.get_registrant_org.return_value = None
    mock_ct.search_by_domain.return_value = []

    errors: list[str] = []
    result = await scout._validate_seed("invalid.local", "Some Corp", ["invalid.local"], errors)

    assert result["assessment"] == "invalid"


# --- _strategy_org_search ---


@pytest.mark.asyncio
async def test_strategy_org_search_returns_matching_domains(scout, mock_ct):
    mock_ct.search_by_org.return_value = [
        {"org_name": "Target Corp", "san_dns_names": ["target1.com"], "common_name": "target2.com"}
    ]

    errors: list[str] = []
    results = await scout._strategy_org_search("Target Corp", errors)

    assert not errors
    domains = {r[0] for r in results}
    assert len(domains) >= 1  # At least some domains extracted


@pytest.mark.asyncio
async def test_strategy_org_search_ct_error(scout, mock_ct):
    mock_ct.search_by_org.side_effect = Exception("network error")

    errors: list[str] = []
    results = await scout._strategy_org_search("Target Corp", errors)

    assert len(errors) == 1
    assert results == []


@pytest.mark.asyncio
async def test_strategy_org_search_filters_low_similarity(scout, mock_ct):
    mock_ct.search_by_org.return_value = [
        {"org_name": "Completely Unrelated Inc", "san_dns_names": ["unrelated.com"]}
    ]

    errors: list[str] = []
    results = await scout._strategy_org_search("Target Corp", errors)

    assert not errors
    assert results == []


# --- _strategy_seed_expansion ---


@pytest.mark.asyncio
async def test_strategy_seed_expansion_extracts_domains(scout, mock_ct):
    mock_ct.search_by_domain.return_value = [
        {
            "org_name": "Target Corp",
            "san_dns_names": ["sub.example.com", "other-related.com"],
            "common_name": "example.com",
        }
    ]

    errors: list[str] = []
    results = await scout._strategy_seed_expansion("example.com", "Target Corp", errors)

    assert not errors
    domains = [r[0] for r in results]
    assert len(domains) >= 1


@pytest.mark.asyncio
async def test_strategy_seed_expansion_filters_cdn_certs(scout, mock_ct):
    sans = [f"tenant{i}.com" for i in range(15)] + ["example.com"]
    mock_ct.search_by_domain.return_value = [
        {"org_name": "Cloudflare, Inc.", "san_dns_names": sans}
    ]

    errors: list[str] = []
    results = await scout._strategy_seed_expansion("example.com", "Target Corp", errors)

    assert not errors
    domains = [r[0] for r in results]
    # CDN tenant domains should be filtered out
    assert "tenant1.com" not in domains


@pytest.mark.asyncio
async def test_strategy_seed_expansion_ct_error(scout, mock_ct):
    mock_ct.search_by_domain.side_effect = Exception("CT timeout")

    errors: list[str] = []
    results = await scout._strategy_seed_expansion("example.com", "Target Corp", errors)

    assert len(errors) == 1
    assert results == []


# --- _strategy_domain_guess ---


@pytest.mark.asyncio
async def test_strategy_domain_guess_returns_resolving_domains(scout, mock_dns):
    resolving = {"target.com", "targetsan.com"}

    def mock_bulk_resolve(domains):
        return {d: (d in resolving) for d in domains}

    mock_dns.bulk_resolve.side_effect = mock_bulk_resolve

    errors: list[str] = []
    results = await scout._strategy_domain_guess("Target Corp", "San Francisco, CA", errors)

    assert not errors
    domains = [r[0] for r in results]
    # Domains that resolve should be included
    for d in domains:
        assert d in resolving
    # Non-resolving domains excluded
    assert "target.net" not in domains

    for _domain, accum in results:
        assert accum.resolves is True
        assert "dns_guess" in accum.sources
