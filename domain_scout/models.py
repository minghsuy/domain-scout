"""Pydantic models for input, output, and intermediate data."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — Pydantic needs runtime import

from pydantic import BaseModel, Field


class EntityInput(BaseModel):
    """Describes the business entity to search for."""

    company_name: str = Field(min_length=1, max_length=200)
    location: str | None = None
    seed_domain: list[str] = Field(default_factory=list)
    industry: str | None = None


class EvidenceRecord(BaseModel):
    """A single piece of attribution evidence for a discovered domain."""

    source_type: str
    description: str
    seed_domain: str | None = None
    cert_id: int | None = None
    cert_org: str | None = None
    similarity_score: float | None = None


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


# --- Intermediate models (not part of public API) ---


class CertRecord(BaseModel):
    """A certificate record from crt.sh."""

    cert_id: int
    common_name: str
    subject: str
    org_name: str | None = None
    not_before: datetime | None = None
    not_after: datetime | None = None
    san_dns_names: list[str] = Field(default_factory=list)
