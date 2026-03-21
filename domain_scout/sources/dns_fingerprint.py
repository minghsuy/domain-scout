"""DNS fingerprint extraction and matching for DV-cert companies.

Extracts infrastructure fingerprints (MX tenant, NS zones, IP /24 prefixes,
SPF includes) from domains using standard DNS queries. Compares fingerprints
to identify domains likely belonging to the same organization.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from domain_scout.sources.dns_utils import DNSChecker

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# MX tenant parsing — provider-specific patterns
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MXTenant:
    """Parsed MX tenant identifier."""

    provider: str
    tenant_id: str

    def __str__(self) -> str:
        return f"{self.provider}:{self.tenant_id}"


# Proofpoint: mxa-00393d01.gslb.pphosted.com → tenant "00393d01"
_PROOFPOINT_RE = re.compile(r"^mx[ab]?-([0-9a-f]{6,12})\.gslb\.pphosted\.com$", re.IGNORECASE)

# Mimecast: us-smtp-inbound-1.mimecast.com → tenant "us-1"
_MIMECAST_RE = re.compile(r"^([a-z]{2})-smtp-inbound-(\d+)\.mimecast\.com$", re.IGNORECASE)

# Microsoft 365: company-com.mail.protection.outlook.com → tenant "company-com"
_MICROSOFT_RE = re.compile(r"^(.+)\.mail\.protection\.outlook\.com$", re.IGNORECASE)

# Barracuda: company.ess.barracudanetworks.com → tenant "company"
_BARRACUDA_RE = re.compile(r"^(.+)\.ess\.barracudanetworks\.com$", re.IGNORECASE)

# Cisco IronPort / Email Security: company.iphmx.com → tenant "company"
_IRONPORT_RE = re.compile(r"^(.+)\.iphmx\.com$", re.IGNORECASE)

# FireEye/Trellix: company.fireeyecloud.com → tenant "company"
_FIREEYE_RE = re.compile(r"^(.+)\.fireeyecloud\.com$", re.IGNORECASE)


def parse_mx_tenant(mx_host: str) -> MXTenant | None:
    """Extract provider and tenant ID from an MX hostname.

    Returns None for shared/non-unique MX records (e.g., Google Workspace
    uses the same MX for all customers).
    """
    mx_host = mx_host.lower().rstrip(".")

    m = _PROOFPOINT_RE.match(mx_host)
    if m:
        return MXTenant(provider="proofpoint", tenant_id=m.group(1))

    m = _MIMECAST_RE.match(mx_host)
    if m:
        return MXTenant(provider="mimecast", tenant_id=f"{m.group(1)}-{m.group(2)}")

    m = _MICROSOFT_RE.match(mx_host)
    if m:
        return MXTenant(provider="microsoft365", tenant_id=m.group(1))

    m = _BARRACUDA_RE.match(mx_host)
    if m:
        return MXTenant(provider="barracuda", tenant_id=m.group(1))

    m = _IRONPORT_RE.match(mx_host)
    if m:
        return MXTenant(provider="ironport", tenant_id=m.group(1))

    m = _FIREEYE_RE.match(mx_host)
    if m:
        return MXTenant(provider="fireeye", tenant_id=m.group(1))

    # Google Workspace (aspmx.l.google.com, etc.) — not customer-unique, skip
    # Generic MX records — no tenant signal
    return None


# ---------------------------------------------------------------------------
# SPF include parsing
# ---------------------------------------------------------------------------

_SPF_INCLUDE_RE = re.compile(r"include:(\S+)", re.IGNORECASE)


def parse_spf_includes(txt_records: list[str]) -> list[str]:
    """Extract SPF include domains from TXT records."""
    includes: list[str] = []
    for txt in txt_records:
        if not txt.lower().startswith("v=spf1"):
            continue
        includes.extend(_SPF_INCLUDE_RE.findall(txt))
    return sorted(set(includes))


# ---------------------------------------------------------------------------
# DNS Fingerprint
# ---------------------------------------------------------------------------


@dataclass
class DNSFingerprint:
    """Infrastructure fingerprint extracted from DNS records."""

    domain: str
    mx_tenants: list[MXTenant] = field(default_factory=list)
    mx_hosts: list[str] = field(default_factory=list)
    ns_zones: list[str] = field(default_factory=list)
    ip_prefixes: list[str] = field(default_factory=list)
    spf_includes: list[str] = field(default_factory=list)

    @property
    def has_signals(self) -> bool:
        """Whether this fingerprint has any usable signals."""
        return bool(self.mx_tenants or self.ns_zones or self.ip_prefixes or self.spf_includes)


def _ns_zone(ns_host: str) -> str:
    """Extract the zone portion of an NS hostname.

    e.g., "ns1-04.azure-dns.com" → "azure-dns.com"
          "dns1.p05.nsone.net" → "nsone.net"
          "ns-1234.awsdns-56.co.uk" → "awsdns-56.co.uk"
    """
    parts = ns_host.lower().rstrip(".").split(".")
    # For known two-part TLDs (co.uk, com.au), keep 3 parts
    if len(parts) >= 3 and parts[-2] in ("co", "com", "net", "org"):
        return ".".join(parts[-3:])
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return ns_host


def _ip_prefix(ip: str) -> str | None:
    """Extract /24 prefix from an IPv4 address. Returns None for IPv6."""
    if "." not in ip:
        return None
    parts = ip.split(".")
    if len(parts) != 4:
        return None
    return ".".join(parts[:3])


async def extract_fingerprint(domain: str, dns: DNSChecker) -> DNSFingerprint:
    """Extract a DNS infrastructure fingerprint from a domain.

    Makes MX, NS, A, and TXT queries. All queries are standard DNS —
    no external API calls, no rate limits, no API keys needed.
    """
    mx_task = dns.get_mx_records(domain)
    ns_task = dns.get_nameservers(domain)
    ip_task = dns.get_ips(domain)
    txt_task = dns.get_txt_records(domain)

    mx_hosts, ns_hosts, ips, txt_records = await asyncio.gather(mx_task, ns_task, ip_task, txt_task)

    # Parse MX tenants
    mx_tenants: list[MXTenant] = []
    for mx in mx_hosts:
        tenant = parse_mx_tenant(mx)
        if tenant:
            mx_tenants.append(tenant)

    # Normalize NS to zones
    ns_zones = sorted({_ns_zone(ns) for ns in ns_hosts})

    # Extract /24 prefixes
    ip_prefixes = sorted({p for ip in ips if (p := _ip_prefix(ip)) is not None})

    # Parse SPF includes
    spf_includes = parse_spf_includes(txt_records)

    fp = DNSFingerprint(
        domain=domain,
        mx_tenants=mx_tenants,
        mx_hosts=sorted(mx_hosts),
        ns_zones=ns_zones,
        ip_prefixes=ip_prefixes,
        spf_includes=spf_includes,
    )
    log.debug(
        "fingerprint.extracted",
        domain=domain,
        mx_tenants=len(mx_tenants),
        ns_zones=len(ns_zones),
        ip_prefixes=len(ip_prefixes),
        spf_includes=len(spf_includes),
    )
    return fp


# ---------------------------------------------------------------------------
# Fingerprint matching
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FingerprintSignal:
    """A single matching signal between two fingerprints."""

    signal_type: str  # "mx_tenant", "ns_zone", "ip_prefix", "spf_include"
    seed_value: str
    candidate_value: str
    provider: str | None = None  # For MX: "proofpoint", "mimecast", etc.


@dataclass
class FingerprintMatch:
    """Result of comparing a candidate fingerprint against a seed fingerprint."""

    candidate_domain: str
    seed_domain: str
    signals: list[FingerprintSignal] = field(default_factory=list)

    @property
    def has_mx_tenant(self) -> bool:
        return any(s.signal_type == "mx_tenant" for s in self.signals)

    @property
    def has_ns_zone(self) -> bool:
        return any(s.signal_type == "ns_zone" for s in self.signals)

    @property
    def has_ip_prefix(self) -> bool:
        return any(s.signal_type == "ip_prefix" for s in self.signals)

    @property
    def signal_count(self) -> int:
        return len(self.signals)

    @property
    def signal_types(self) -> set[str]:
        return {s.signal_type for s in self.signals}


def match_fingerprint(candidate: DNSFingerprint, seed: DNSFingerprint) -> FingerprintMatch:
    """Compare a candidate fingerprint against a seed fingerprint.

    Returns a FingerprintMatch with all matching signals. Callers decide
    how to score based on signal types and count.
    """
    signals: list[FingerprintSignal] = []

    # MX tenant match (strongest signal)
    seed_tenants = {str(t): t for t in seed.mx_tenants}
    for ct in candidate.mx_tenants:
        key = str(ct)
        if key in seed_tenants:
            signals.append(
                FingerprintSignal(
                    signal_type="mx_tenant",
                    seed_value=key,
                    candidate_value=key,
                    provider=ct.provider,
                )
            )

    # NS zone match (moderate signal — many companies share DNS providers)
    seed_ns = set(seed.ns_zones)
    for nz in candidate.ns_zones:
        if nz in seed_ns:
            signals.append(
                FingerprintSignal(
                    signal_type="ns_zone",
                    seed_value=nz,
                    candidate_value=nz,
                )
            )

    # IP /24 prefix match (weak signal — shared hosting, CDNs)
    seed_ips = set(seed.ip_prefixes)
    for ip in candidate.ip_prefixes:
        if ip in seed_ips:
            signals.append(
                FingerprintSignal(
                    signal_type="ip_prefix",
                    seed_value=ip,
                    candidate_value=ip,
                )
            )

    # SPF include match (moderate signal — shared email infra)
    seed_spf = set(seed.spf_includes)
    for spf in candidate.spf_includes:
        if spf in seed_spf:
            signals.append(
                FingerprintSignal(
                    signal_type="spf_include",
                    seed_value=spf,
                    candidate_value=spf,
                )
            )

    return FingerprintMatch(
        candidate_domain=candidate.domain,
        seed_domain=seed.domain,
        signals=signals,
    )
