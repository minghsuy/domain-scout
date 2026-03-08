"""Acceptance tests: mock at source level with realistic fixture data.

These tests exercise the full scoring/merging/filtering pipeline by mocking
CT/RDAP/DNS sources with realistic Walmart data, rather than mocking at the
Scout level.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from domain_scout.config import ScoutConfig
from domain_scout.models import EntityInput
from domain_scout.scout import Scout

# --- Walmart fixture data ---
# Simulates CT records from crt.sh for walmart.com seed expansion
_WALMART_CT_DOMAIN: dict[str, list[dict[str, object]]] = {
    "walmart.com": [
        {
            "cert_id": 1001,
            "common_name": "walmart.com",
            "subject": "O=Walmart Inc.",
            "org_name": "Walmart Inc.",
            "not_before": "2024-01-15T00:00:00",
            "not_after": "2025-01-15T00:00:00",
            "san_dns_names": [
                "walmart.com",
                "www.walmart.com",
                "samsclub.com",
                "www.samsclub.com",
                "wal-mart.com",
            ],
        },
        {
            "cert_id": 1002,
            "common_name": "walmart.ca",
            "subject": "O=Walmart Canada Corp.",
            "org_name": "Walmart Canada Corp.",
            "not_before": "2024-03-01T00:00:00",
            "not_after": "2025-03-01T00:00:00",
            "san_dns_names": ["walmart.ca", "www.walmart.ca"],
        },
    ],
    "samsclub.com": [
        {
            "cert_id": 1003,
            "common_name": "samsclub.com",
            "subject": "O=Walmart Inc.",
            "org_name": "Walmart Inc.",
            "not_before": "2024-02-01T00:00:00",
            "not_after": "2025-02-01T00:00:00",
            "san_dns_names": [
                "samsclub.com",
                "www.samsclub.com",
                "walmart.com",
                "wal-mart.com",
            ],
        },
    ],
}

# CT org search returns certs with matching O= field
_WALMART_CT_ORG: list[dict[str, object]] = [
    {
        "cert_id": 2001,
        "common_name": "walmart.com",
        "subject": "O=Walmart Inc.",
        "org_name": "Walmart Inc.",
        "not_before": "2024-01-01T00:00:00",
        "not_after": "2025-01-01T00:00:00",
        "san_dns_names": [
            "walmart.com",
            "www.walmart.com",
            "walmart.ca",
            "wal-mart.com",
        ],
    },
]

# DNS resolution map
_WALMART_DNS: dict[str, bool] = {
    "walmart.com": True,
    "www.walmart.com": True,
    "samsclub.com": True,
    "www.samsclub.com": True,
    "walmart.ca": True,
    "www.walmart.ca": True,
    "wal-mart.com": True,
    # CDN/infra domains that should NOT appear
    "cloudflare.com": True,
    "akamai.com": True,
}


def _make_scout() -> Scout:
    """Create a Scout with mocked sources."""
    config = ScoutConfig()
    scout = Scout(config=config)

    # Mock CT source
    async def ct_search_by_domain(domain: str) -> list[dict[str, object]]:
        return _WALMART_CT_DOMAIN.get(domain, [])

    async def ct_search_by_org(
        org_name: str, *, verify_org: bool = True
    ) -> list[dict[str, object]]:
        return list(_WALMART_CT_ORG)

    async def ct_get_cert_org(cert_id: int) -> str | None:
        return "Walmart Inc."

    scout._ct.search_by_domain = AsyncMock(side_effect=ct_search_by_domain)  # type: ignore[method-assign]
    scout._ct.search_by_org = AsyncMock(side_effect=ct_search_by_org)  # type: ignore[method-assign]
    scout._ct.get_cert_org = AsyncMock(side_effect=ct_get_cert_org)  # type: ignore[method-assign]

    # Mock RDAP source
    scout._rdap.get_registrant_org = AsyncMock(return_value="Walmart Inc.")  # type: ignore[method-assign]
    scout._rdap.get_registrant_info = AsyncMock(  # type: ignore[method-assign]
        return_value={"org": "Walmart Inc.", "name": None, "country": "US"}
    )

    # Mock DNS
    async def dns_resolves(domain: str) -> bool:
        return _WALMART_DNS.get(domain, False)

    async def dns_bulk_resolve(domains: list[str]) -> dict[str, bool]:
        return {d: _WALMART_DNS.get(d, False) for d in domains}

    async def dns_shares_infrastructure(ref: str, other: str) -> bool:
        return False

    scout._dns.resolves = AsyncMock(side_effect=dns_resolves)  # type: ignore[method-assign]
    scout._dns.bulk_resolve = AsyncMock(side_effect=dns_bulk_resolve)  # type: ignore[method-assign]
    scout._dns.shares_infrastructure = AsyncMock(side_effect=dns_shares_infrastructure)  # type: ignore[method-assign]

    return scout


class TestWalmartAcceptance:
    """Acceptance tests using Walmart fixture data with source-level mocks."""

    @pytest.mark.asyncio
    async def test_walmart_finds_key_domains(self) -> None:
        """Must find key Walmart domains; must NOT include CDN providers."""
        scout = _make_scout()
        entity = EntityInput(
            company_name="Walmart",
            seed_domain=["walmart.com", "samsclub.com"],
        )
        result = await scout.discover_async(entity)

        found_domains = {d.domain for d in result.domains}

        # Must find these key related domains
        assert "samsclub.com" in found_domains, f"Missing samsclub.com in {found_domains}"
        assert "walmart.ca" in found_domains, f"Missing walmart.ca in {found_domains}"
        assert "wal-mart.com" in found_domains, f"Missing wal-mart.com in {found_domains}"

        # Must NOT include CDN/infra providers
        assert "cloudflare.com" not in found_domains
        assert "akamai.com" not in found_domains

    @pytest.mark.asyncio
    async def test_walmart_scores_differentiate(self) -> None:
        """Not all domains should score identically (catches boost stacking regression)."""
        scout = _make_scout()
        entity = EntityInput(
            company_name="Walmart",
            seed_domain=["walmart.com", "samsclub.com"],
        )
        result = await scout.discover_async(entity)

        scores = {d.confidence for d in result.domains}
        assert len(scores) > 1, f"All domains scored identically: {scores}"

    @pytest.mark.asyncio
    async def test_walmart_key_domains_high_confidence(self) -> None:
        """Key related domains should score >= 0.80."""
        scout = _make_scout()
        entity = EntityInput(
            company_name="Walmart",
            seed_domain=["walmart.com", "samsclub.com"],
        )
        result = await scout.discover_async(entity)

        domain_scores = {d.domain: d.confidence for d in result.domains}

        for key_domain in ["samsclub.com", "walmart.ca", "wal-mart.com"]:
            assert key_domain in domain_scores, f"Missing {key_domain}"
            assert domain_scores[key_domain] >= 0.80, (
                f"{key_domain} scored {domain_scores[key_domain]}, expected >= 0.80"
            )

    @pytest.mark.asyncio
    async def test_walmart_rdap_corroboration(self) -> None:
        """Key resolving domains should have rdap_registrant_match in sources."""
        scout = _make_scout()
        entity = EntityInput(
            company_name="Walmart",
            seed_domain=["walmart.com", "samsclub.com"],
        )
        result = await scout.discover_async(entity)

        domain_map = {d.domain: d for d in result.domains}

        # Cross-seed verified domains with RDAP corroboration should have the source
        for key_domain in ["samsclub.com", "wal-mart.com"]:
            assert key_domain in domain_map, f"Missing {key_domain}"
            assert "rdap_registrant_match" in domain_map[key_domain].sources, (
                f"{key_domain} missing rdap_registrant_match, "
                f"sources={domain_map[key_domain].sources}"
            )
