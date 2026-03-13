"""Pydantic models for input, output, and intermediate data."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — Pydantic needs runtime import
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from typing import Any


class EntityInput(BaseModel):
    """Describes the business entity to search for."""

    company_name: str = Field(min_length=1, max_length=200)
    location: str | None = None
    seed_domain: list[str] = Field(default_factory=list, max_length=50)
    industry: str | None = None


class EvidenceRecord(BaseModel):
    """A single piece of attribution evidence for a discovered domain."""

    source_type: str
    description: str
    seed_domain: str | None = None
    cert_id: int | None = None
    cert_org: str | None = None
    similarity_score: float | None = None
    rdap_org: str | None = None


class DiscoveredDomain(BaseModel):
    """A single domain discovered during the search."""

    domain: str
    confidence: float = Field(ge=0.0, le=1.0)
    sources: list[str] = Field(default_factory=list)
    evidence: list[EvidenceRecord] = Field(default_factory=list)
    cert_org_names: list[str] = Field(default_factory=list)
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    resolves: bool = False
    rdap_org: str | None = None
    is_seed: bool = False
    seed_sources: list[str] = Field(default_factory=list)


class RunMetadata(BaseModel):
    """Metadata about a domain-scout run for audit and reproducibility."""

    schema_version: str = "1.0"
    tool_version: str
    timestamp: datetime
    elapsed_seconds: float
    domains_found: int
    timed_out: bool = False
    seed_count: int = 0
    errors: list[str] = Field(default_factory=list)
    config: dict[str, object] = Field(default_factory=dict)


class ScoutResult(BaseModel):
    """Complete result of a domain-scout run."""

    entity: EntityInput
    domains: list[DiscoveredDomain] = Field(default_factory=list)
    seed_domain_assessment: dict[str, str] = Field(default_factory=dict)
    seed_cross_verification: dict[str, list[str]] = Field(default_factory=dict)
    run_metadata: RunMetadata


# --- Delta reporting models ---


class DomainChange(BaseModel):
    """A single field-level change on a domain."""

    field: str
    old: float | bool | str | list[str] | None
    new: float | bool | str | list[str] | None


class ChangedDomain(BaseModel):
    """A domain present in both scans with meaningful differences."""

    domain: str
    changes: list[DomainChange]
    baseline_confidence: float
    current_confidence: float


class DeltaWarning(BaseModel):
    """Warning about conditions affecting delta interpretation."""

    code: str
    message: str


class DeltaSummary(BaseModel):
    """Aggregate counts for a delta report."""

    added: int
    removed: int
    changed: int
    unchanged: int
    baseline_total: int
    current_total: int


class DeltaReport(BaseModel):
    """Complete delta between two ScoutResult runs."""

    added: list[DiscoveredDomain] = Field(default_factory=list)
    removed: list[DiscoveredDomain] = Field(default_factory=list)
    changed: list[ChangedDomain] = Field(default_factory=list)
    summary: DeltaSummary
    warnings: list[DeltaWarning] = Field(default_factory=list)
    baseline_metadata: RunMetadata
    current_metadata: RunMetadata


# --- Internal processing helpers and classes ---


def _normalize_time(val: object) -> str | None:
    """Normalize a datetime or string to ISO string for consistent comparison.

    CT Postgres returns datetime objects, JSON API and cache return strings.
    Normalizing to ISO strings prevents TypeError on mixed-type comparison.
    """
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.isoformat()
    if isinstance(val, str):
        if not val:
            return None
        try:
            return datetime.fromisoformat(val).isoformat()
        except ValueError:
            return val
    return str(val)


def _parse_time(val: str | None) -> datetime | None:
    """Parse an ISO 8601 string back to datetime for Pydantic output."""
    if val is None:
        return None
    return datetime.fromisoformat(val)


class DomainAccumulator:
    """Mutable accumulator for evidence about a single domain."""

    __slots__ = (
        "sources",
        "evidence",
        "cert_org_names",
        "first_seen",
        "last_seen",
        "resolves",
        "rdap_org",
        "confidence",
    )

    def __init__(self) -> None:
        self.sources: set[str] = set()
        self.evidence: list[EvidenceRecord] = []
        self.cert_org_names: set[str] = set()
        self.first_seen: str | None = None
        self.last_seen: str | None = None
        self.resolves: bool = False
        self.rdap_org: str | None = None
        self.confidence: float = 0.0

    def merge(self, other: DomainAccumulator) -> None:
        self.sources |= other.sources
        self.evidence.extend(other.evidence)
        self.cert_org_names |= other.cert_org_names
        o_first = _normalize_time(other.first_seen)
        if o_first and (self.first_seen is None or o_first < self.first_seen):
            self.first_seen = o_first
        o_last = _normalize_time(other.last_seen)
        if o_last and (self.last_seen is None or o_last > self.last_seen):
            self.last_seen = o_last
        self.resolves = self.resolves or other.resolves
        if self.rdap_org is None and other.rdap_org is not None:
            self.rdap_org = other.rdap_org

    def update_times(self, not_before: object, not_after: object) -> None:
        nb = _normalize_time(not_before)
        na = _normalize_time(not_after)
        if nb and (self.first_seen is None or nb < self.first_seen):
            self.first_seen = nb
        if na and (self.last_seen is None or na > self.last_seen):
            self.last_seen = na
