"""Tests for DNS fingerprint extraction and matching."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from domain_scout.config import ScoutConfig
from domain_scout.models import EntityInput
from domain_scout.scout import Scout
from domain_scout.sources.dns_fingerprint import (
    DNSFingerprint,
    MXTenant,
    _ip_prefix,
    _ns_zone,
    extract_fingerprint,
    match_fingerprint,
    parse_mx_tenant,
    parse_spf_includes,
)
from domain_scout.sources.dns_utils import DNSChecker

# ---------------------------------------------------------------------------
# MX tenant parsing
# ---------------------------------------------------------------------------


class TestParseMxTenant:
    def test_proofpoint(self) -> None:
        t = parse_mx_tenant("mxa-00393d01.gslb.pphosted.com")
        assert t is not None
        assert t.provider == "proofpoint"
        assert t.tenant_id == "00393d01"

    def test_proofpoint_mxb(self) -> None:
        t = parse_mx_tenant("mxb-00393d01.gslb.pphosted.com")
        assert t is not None
        assert t.provider == "proofpoint"
        assert t.tenant_id == "00393d01"

    def test_mimecast_returns_none(self) -> None:
        """Mimecast inbound MX is shared infrastructure, not per-customer."""
        assert parse_mx_tenant("us-smtp-inbound-1.mimecast.com") is None

    def test_microsoft365(self) -> None:
        t = parse_mx_tenant("contoso-com.mail.protection.outlook.com")
        assert t is not None
        assert t.provider == "microsoft365"
        assert t.tenant_id == "contoso-com"

    def test_barracuda(self) -> None:
        t = parse_mx_tenant("acmecorp.ess.barracudanetworks.com")
        assert t is not None
        assert t.provider == "barracuda"
        assert t.tenant_id == "acmecorp"

    def test_ironport(self) -> None:
        t = parse_mx_tenant("acmecorp.iphmx.com")
        assert t is not None
        assert t.provider == "ironport"
        assert t.tenant_id == "acmecorp"

    def test_fireeye(self) -> None:
        t = parse_mx_tenant("acmecorp.fireeyecloud.com")
        assert t is not None
        assert t.provider == "fireeye"
        assert t.tenant_id == "acmecorp"

    def test_google_returns_none(self) -> None:
        """Google MX is shared across all customers — not a tenant signal."""
        assert parse_mx_tenant("aspmx.l.google.com") is None
        assert parse_mx_tenant("alt1.aspmx.l.google.com") is None

    def test_unknown_returns_none(self) -> None:
        assert parse_mx_tenant("mail.example.com") is None

    def test_trailing_dot(self) -> None:
        t = parse_mx_tenant("mxa-00393d01.gslb.pphosted.com.")
        assert t is not None
        assert t.provider == "proofpoint"

    def test_case_insensitive(self) -> None:
        t = parse_mx_tenant("MXA-00393D01.GSLB.PPHOSTED.COM")
        assert t is not None
        assert t.provider == "proofpoint"


class TestMXTenantStr:
    def test_str(self) -> None:
        t = MXTenant(provider="proofpoint", tenant_id="00393d01")
        assert str(t) == "proofpoint:00393d01"


# ---------------------------------------------------------------------------
# SPF parsing
# ---------------------------------------------------------------------------


class TestParseSPFIncludes:
    def test_basic_spf(self) -> None:
        txts = ["v=spf1 include:spf.protection.outlook.com include:_spf.google.com -all"]
        result = parse_spf_includes(txts)
        assert "spf.protection.outlook.com" in result
        assert "_spf.google.com" in result

    def test_non_spf_records_ignored(self) -> None:
        txts = [
            "google-site-verification=abc123",
            "v=spf1 include:example.com -all",
        ]
        result = parse_spf_includes(txts)
        assert result == ["example.com"]

    def test_empty(self) -> None:
        assert parse_spf_includes([]) == []

    def test_deduplication(self) -> None:
        txts = [
            "v=spf1 include:a.com include:a.com -all",
        ]
        assert parse_spf_includes(txts) == ["a.com"]


# ---------------------------------------------------------------------------
# NS zone extraction
# ---------------------------------------------------------------------------


class TestNsZone:
    def test_azure_dns(self) -> None:
        assert _ns_zone("ns1-04.azure-dns.com") == "azure-dns.com"

    def test_nsone(self) -> None:
        assert _ns_zone("dns1.p05.nsone.net") == "nsone.net"

    def test_aws_couk(self) -> None:
        assert _ns_zone("ns-1234.awsdns-56.co.uk") == "awsdns-56.co.uk"

    def test_simple(self) -> None:
        assert _ns_zone("ns1.example.com") == "example.com"

    def test_trailing_dot(self) -> None:
        assert _ns_zone("ns1.example.com.") == "example.com"


# ---------------------------------------------------------------------------
# IP prefix extraction
# ---------------------------------------------------------------------------


class TestIpPrefix:
    def test_ipv4(self) -> None:
        assert _ip_prefix("192.168.1.100") == "192.168.1"

    def test_ipv6_returns_none(self) -> None:
        assert _ip_prefix("2001:db8::1") is None

    def test_malformed_returns_none(self) -> None:
        assert _ip_prefix("not-an-ip") is None


# ---------------------------------------------------------------------------
# Fingerprint extraction (mocked DNS)
# ---------------------------------------------------------------------------


class TestExtractFingerprint:
    @pytest.fixture
    def checker(self) -> DNSChecker:
        return DNSChecker(ScoutConfig())

    @pytest.mark.asyncio
    async def test_extract_full(self, checker: DNSChecker) -> None:
        with (
            patch.object(
                checker,
                "get_mx_records",
                new_callable=AsyncMock,
                return_value=["mxa-00393d01.gslb.pphosted.com"],
            ),
            patch.object(
                checker,
                "get_nameservers",
                new_callable=AsyncMock,
                return_value=["ns1-04.azure-dns.com", "ns2-04.azure-dns.com"],
            ),
            patch.object(
                checker,
                "get_ips",
                new_callable=AsyncMock,
                return_value=["10.0.1.5", "10.0.1.6"],
            ),
            patch.object(
                checker,
                "get_txt_records",
                new_callable=AsyncMock,
                return_value=["v=spf1 include:spf.protection.outlook.com -all"],
            ),
        ):
            fp = await extract_fingerprint("example.com", checker)

        assert fp.domain == "example.com"
        assert len(fp.mx_tenants) == 1
        assert fp.mx_tenants[0].provider == "proofpoint"
        assert fp.ns_zones == ["azure-dns.com"]
        assert fp.ip_prefixes == ["10.0.1"]
        assert fp.spf_includes == ["spf.protection.outlook.com"]
        assert fp.has_signals is True

    @pytest.mark.asyncio
    async def test_extract_empty(self, checker: DNSChecker) -> None:
        with (
            patch.object(checker, "get_mx_records", new_callable=AsyncMock, return_value=[]),
            patch.object(checker, "get_nameservers", new_callable=AsyncMock, return_value=[]),
            patch.object(checker, "get_ips", new_callable=AsyncMock, return_value=[]),
            patch.object(checker, "get_txt_records", new_callable=AsyncMock, return_value=[]),
        ):
            fp = await extract_fingerprint("empty.com", checker)

        assert fp.has_signals is False


# ---------------------------------------------------------------------------
# Fingerprint matching
# ---------------------------------------------------------------------------


class TestMatchFingerprint:
    def _make_fp(
        self,
        domain: str,
        mx_tenants: list[MXTenant] | None = None,
        ns_zones: list[str] | None = None,
        ip_prefixes: list[str] | None = None,
        spf_includes: list[str] | None = None,
    ) -> DNSFingerprint:
        return DNSFingerprint(
            domain=domain,
            mx_tenants=mx_tenants or [],
            ns_zones=ns_zones or [],
            ip_prefixes=ip_prefixes or [],
            spf_includes=spf_includes or [],
        )

    def test_mx_tenant_match(self) -> None:
        tenant = MXTenant(provider="proofpoint", tenant_id="00393d01")
        seed = self._make_fp("seed.com", mx_tenants=[tenant])
        candidate = self._make_fp("candidate.com", mx_tenants=[tenant])

        result = match_fingerprint(candidate, seed)
        assert result.has_mx_tenant
        assert result.signal_count == 1
        assert result.signals[0].provider == "proofpoint"

    def test_ns_zone_match(self) -> None:
        """Private/custom NS zones should match."""
        seed = self._make_fp("seed.com", ns_zones=["ns.shelterinsurance.com"])
        candidate = self._make_fp("candidate.com", ns_zones=["ns.shelterinsurance.com"])

        result = match_fingerprint(candidate, seed)
        assert result.has_ns_zone
        assert not result.has_mx_tenant

    def test_ns_zone_shared_infra_filtered(self) -> None:
        """Shared NS providers (Cloudflare, AWS, Azure) should NOT match."""
        seed = self._make_fp("seed.com", ns_zones=["cloudflare.com"])
        candidate = self._make_fp("candidate.com", ns_zones=["cloudflare.com"])

        result = match_fingerprint(candidate, seed)
        assert result.signal_count == 0

    def test_ip_prefix_not_matched(self) -> None:
        """IP /24 prefix is intentionally skipped (CDN false positives)."""
        seed = self._make_fp("seed.com", ip_prefixes=["10.0.1"])
        candidate = self._make_fp("candidate.com", ip_prefixes=["10.0.1"])

        result = match_fingerprint(candidate, seed)
        assert result.signal_count == 0

    def test_multi_signal_match(self) -> None:
        tenant = MXTenant(provider="microsoft365", tenant_id="contoso-com")
        seed = self._make_fp(
            "seed.com",
            mx_tenants=[tenant],
            ns_zones=["ns.contoso.com"],
        )
        candidate = self._make_fp(
            "candidate.com",
            mx_tenants=[tenant],
            ns_zones=["ns.contoso.com"],
        )

        result = match_fingerprint(candidate, seed)
        assert result.signal_count == 2
        assert result.signal_types == {"mx_tenant", "ns_zone"}

    def test_no_match(self) -> None:
        seed = self._make_fp("seed.com", ns_zones=["ns.acme.com"])
        candidate = self._make_fp("candidate.com", ns_zones=["ns.other.com"])

        result = match_fingerprint(candidate, seed)
        assert result.signal_count == 0

    def test_spf_match(self) -> None:
        """Custom SPF includes should match; shared providers should not."""
        seed = self._make_fp("seed.com", spf_includes=["spf.shelterinsurance.com"])
        candidate = self._make_fp(
            "candidate.com", spf_includes=["spf.shelterinsurance.com", "_spf.google.com"]
        )

        result = match_fingerprint(candidate, seed)
        assert result.signal_count == 1
        assert result.signals[0].signal_type == "spf_include"

    def test_spf_shared_provider_filtered(self) -> None:
        """Shared SPF providers (M365, Google, SendGrid) should NOT match."""
        seed = self._make_fp("seed.com", spf_includes=["spf.protection.outlook.com"])
        candidate = self._make_fp("candidate.com", spf_includes=["spf.protection.outlook.com"])

        result = match_fingerprint(candidate, seed)
        assert result.signal_count == 0

    def test_partial_mx_no_match(self) -> None:
        """Different tenants on same provider should not match."""
        seed = self._make_fp(
            "seed.com", mx_tenants=[MXTenant(provider="proofpoint", tenant_id="aaa")]
        )
        candidate = self._make_fp(
            "candidate.com", mx_tenants=[MXTenant(provider="proofpoint", tenant_id="bbb")]
        )

        result = match_fingerprint(candidate, seed)
        assert result.signal_count == 0


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class TestFingerprintConfig:
    def test_default_mode(self) -> None:
        config = ScoutConfig()
        assert config.discovery_mode == "default"

    def test_fingerprint_mode(self) -> None:
        config = ScoutConfig(discovery_mode="fingerprint")
        assert config.discovery_mode == "fingerprint"

    def test_fp_candidate_limit(self) -> None:
        config = ScoutConfig(fp_candidate_limit=50)
        assert config.fp_candidate_limit == 50


# ---------------------------------------------------------------------------
# Acceptance: fingerprint mode through Scout with mocked sources
# ---------------------------------------------------------------------------

# Simulates a DV-cert company (no org in certs) where Shelter Insurance
# subsidiaries share Proofpoint MX tenant 002d0c01.

_SHELTER_CT_DOMAIN: dict[str, list[dict[str, object]]] = {
    "shelterinsurance.com": [
        {
            "cert_id": 3001,
            "common_name": "shelterinsurance.com",
            "subject": "CN=shelterinsurance.com",
            "org_name": None,  # DV cert — no org
            "not_before": "2024-01-01T00:00:00",
            "not_after": "2025-01-01T00:00:00",
            "san_dns_names": [
                "shelterinsurance.com",
                "www.shelterinsurance.com",
                "amshieldinsurance.com",
            ],
        },
    ],
}

_SHELTER_DNS: dict[str, bool] = {
    "shelterinsurance.com": True,
    "www.shelterinsurance.com": True,
    "amshieldinsurance.com": True,
}

# MX records per domain — all share Proofpoint tenant 002d0c01
_SHELTER_MX: dict[str, list[str]] = {
    "shelterinsurance.com": ["mxa-002d0c01.gslb.pphosted.com", "mxb-002d0c01.gslb.pphosted.com"],
    "amshieldinsurance.com": ["mxa-002d0c01.gslb.pphosted.com", "mxb-002d0c01.gslb.pphosted.com"],
}

_SHELTER_NS: dict[str, list[str]] = {
    "shelterinsurance.com": ["ns1.cloudflare.com", "ns2.cloudflare.com"],
    "amshieldinsurance.com": ["ns1.cloudflare.com", "ns2.cloudflare.com"],
}


def _make_shelter_scout() -> Scout:
    """Create a Scout in fingerprint mode with mocked sources for Shelter Insurance."""
    config = ScoutConfig(discovery_mode="fingerprint", deep_mode=True, total_timeout=30)
    scout = Scout(config=config)

    # Mock CT — DV certs, no org search results
    async def ct_search_by_domain(domain: str) -> list[dict[str, object]]:
        return _SHELTER_CT_DOMAIN.get(domain, [])

    async def ct_search_by_org(
        org_name: str, *, verify_org: bool = True
    ) -> list[dict[str, object]]:
        return []  # DV certs — org search finds nothing

    async def ct_get_cert_org(cert_id: int) -> str | None:
        return None

    scout._ct.search_by_domain = AsyncMock(side_effect=ct_search_by_domain)  # type: ignore[method-assign]
    scout._ct.search_by_org = AsyncMock(side_effect=ct_search_by_org)  # type: ignore[method-assign]
    scout._ct.get_cert_org = AsyncMock(side_effect=ct_get_cert_org)  # type: ignore[method-assign]

    # Mock RDAP — no registrant info (privacy-protected)
    scout._rdap.get_registrant_org = AsyncMock(return_value=None)  # type: ignore[method-assign]
    scout._rdap.get_registrant_info = AsyncMock(  # type: ignore[method-assign]
        return_value={"org": None, "name": None, "country": None}
    )

    # Mock DNS
    async def dns_resolves(domain: str) -> bool:
        return _SHELTER_DNS.get(domain, False)

    async def dns_bulk_resolve(domains: list[str]) -> dict[str, bool]:
        return {d: _SHELTER_DNS.get(d, False) for d in domains}

    async def dns_shares_infrastructure(ref: str, other: str) -> bool:
        return False

    async def dns_get_mx(domain: str) -> list[str]:
        return _SHELTER_MX.get(domain, [])

    async def dns_get_ns(domain: str) -> list[str]:
        return _SHELTER_NS.get(domain, [])

    async def dns_get_ips(domain: str) -> list[str]:
        return ["10.0.1.1"] if domain in _SHELTER_DNS else []

    async def dns_get_txt(domain: str) -> list[str]:
        return []

    scout._dns.resolves = AsyncMock(side_effect=dns_resolves)  # type: ignore[method-assign]
    scout._dns.bulk_resolve = AsyncMock(side_effect=dns_bulk_resolve)  # type: ignore[method-assign]
    scout._dns.shares_infrastructure = AsyncMock(side_effect=dns_shares_infrastructure)  # type: ignore[method-assign]
    scout._dns.get_mx_records = AsyncMock(side_effect=dns_get_mx)  # type: ignore[method-assign]
    scout._dns.get_nameservers = AsyncMock(side_effect=dns_get_ns)  # type: ignore[method-assign]
    scout._dns.get_ips = AsyncMock(side_effect=dns_get_ips)  # type: ignore[method-assign]
    scout._dns.get_txt_records = AsyncMock(side_effect=dns_get_txt)  # type: ignore[method-assign]

    return scout


class TestFingerprintAcceptance:
    """Acceptance tests: fingerprint mode through full Scout pipeline."""

    @pytest.mark.asyncio
    async def test_fingerprint_finds_mx_tenant_match(self) -> None:
        """Domains sharing MX tenant with seed should get fp:mx_tenant source."""
        scout = _make_shelter_scout()
        entity = EntityInput(
            company_name="Shelter Insurance",
            seed_domain=["shelterinsurance.com"],
        )
        result = await scout.discover_async(entity)
        domain_map = {d.domain: d for d in result.domains}

        assert "amshieldinsurance.com" in domain_map, (
            f"Missing amshieldinsurance.com, found: {list(domain_map.keys())}"
        )
        assert "fp:mx_tenant" in domain_map["amshieldinsurance.com"].sources

    @pytest.mark.asyncio
    async def test_fingerprint_skips_strategy_a(self) -> None:
        """In fingerprint mode, CT org search should not be called."""
        scout = _make_shelter_scout()
        entity = EntityInput(
            company_name="Shelter Insurance",
            seed_domain=["shelterinsurance.com"],
        )
        await scout.discover_async(entity)

        # search_by_org should not have been called (Strategy A skipped)
        scout._ct.search_by_org.assert_not_called()  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_fingerprint_mx_tenant_boosts_confidence(self) -> None:
        """MX tenant match should boost confidence (treated like RDAP corroboration)."""
        scout = _make_shelter_scout()
        entity = EntityInput(
            company_name="Shelter Insurance",
            seed_domain=["shelterinsurance.com"],
        )
        result = await scout.discover_async(entity)
        domain_map = {d.domain: d for d in result.domains}

        assert "amshieldinsurance.com" in domain_map, (
            f"Missing amshieldinsurance.com, found: {list(domain_map.keys())}"
        )
        assert domain_map["amshieldinsurance.com"].confidence >= 0.80
