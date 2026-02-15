"""Pydantic models for input, output, and intermediate data."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — Pydantic needs runtime import

from pydantic import BaseModel, Field


class EntityInput(BaseModel):
    """Describes the business entity to search for."""

    company_name: str
    location: str | None = None
    seed_domain: str | None = None
    industry: str | None = None


class DiscoveredDomain(BaseModel):
    """A single domain discovered during the search."""

    domain: str
    confidence: float = Field(ge=0.0, le=1.0)
    sources: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    cert_org_names: list[str] = Field(default_factory=list)
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    resolves: bool = False
    is_seed: bool = False


class ScoutResult(BaseModel):
    """Complete result of a domain-scout run."""

    entity: EntityInput
    domains: list[DiscoveredDomain] = Field(default_factory=list)
    seed_domain_assessment: str | None = None  # confirmed | suspicious | invalid | not_provided
    search_metadata: dict[str, object] = Field(default_factory=dict)


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
