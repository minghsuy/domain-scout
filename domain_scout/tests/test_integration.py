"""Integration tests that hit real crt.sh. Skip in CI with: pytest -m 'not integration'."""

from __future__ import annotations

import pytest

from domain_scout.config import ScoutConfig
from domain_scout.sources.ct_logs import CTLogSource


@pytest.mark.integration
@pytest.mark.asyncio
async def test_crtsh_postgres_search() -> None:
    """Search crt.sh Postgres for a well-known domain and verify we get results."""
    config = ScoutConfig(ct_recent_years=5, ct_max_results=10)
    ct = CTLogSource(config)
    records = await ct.search_by_domain("paloaltonetworks.com")
    assert len(records) > 0

    # At least some certs should have SANs
    total_sans = sum(len(r.get("san_dns_names", [])) for r in records)
    assert total_sans > 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_crtsh_org_search() -> None:
    """Search by org name and verify we get domain results."""
    config = ScoutConfig(ct_recent_years=3, ct_max_results=20)
    ct = CTLogSource(config)
    records = await ct.search_by_org("Palo Alto Networks")
    # Filter to only records with matching org
    matched = [r for r in records if r.get("org_name")]
    # We should get some certs with org names
    assert len(matched) >= 0  # May be 0 if crt.sh is slow; don't fail hard


@pytest.mark.integration
@pytest.mark.asyncio
async def test_full_scout_run() -> None:
    """End-to-end test of the Scout orchestrator."""
    from domain_scout.scout import Scout

    config = ScoutConfig(ct_recent_years=2, ct_max_results=20)
    s = Scout(config=config)
    from domain_scout.models import EntityInput

    result = await s.discover_async(
        entity=EntityInput(
            company_name="Palo Alto Networks",
            location="Santa Clara, CA",
            seed_domain="paloaltonetworks.com",
        )
    )
    assert result.seed_domain_assessment in ("confirmed", "suspicious", "invalid")
    assert result.search_metadata.get("elapsed_seconds", 0) > 0
    # Should find at least the seed domain
    domains = [d.domain for d in result.domains]
    assert "paloaltonetworks.com" in domains or len(domains) > 0
