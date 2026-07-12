"""Pydantic models for input, output, and intermediate data."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — Pydantic needs runtime import
from typing import Self

from pydantic import BaseModel, Field, model_validator


class EntityInput(BaseModel):
    """Search target: company_name (forward) or seed_domain (reverse) or both."""

    company_name: str = Field(default="", max_length=200)
    location: str | None = None
    seed_domain: list[str] = Field(default_factory=list, max_length=50)
    industry: str | None = None

    @model_validator(mode="after")
    def _require_at_least_one_input(self) -> Self:
        if not self.company_name and not self.seed_domain:
            msg = "either company_name or seed_domain is required"
            raise ValueError(msg)
        return self


class EvidenceRecord(BaseModel):
    """A single piece of attribution evidence for a discovered domain."""

    source_type: str
    description: str
    seed_domain: str | None = None
    cert_id: int | None = None
    cert_org: str | None = None
    similarity_score: float | None = None
    rdap_org: str | None = None
    signal_type: str | None = None
    signal_weight: float | None = None


class ScoringInputs(BaseModel):
    """Score-time inputs for one domain, captured before the pipeline mutates them.

    ``Scout._score_confidence`` runs *before* ``_infra_boost`` (which adds the
    ``shared_infra`` source tag, an evidence record, and a +0.05 confidence
    addend) and before ``_build_output`` (which deduplicates evidence). A
    persisted :class:`DiscoveredDomain` therefore records *post-pipeline* state
    that differs from what the scorer saw. This model snapshots the exact
    evidence-level inputs at scoring time (issue #187) so the eval harness can
    replay production scoring instead of approximating it.

    Captured: evidence-level state (sources, org names, aggregates) exactly as
    ``_score_confidence`` consumed it. Recomputed at replay time: derived
    features such as name similarity — the substrate freezes *evidence*, while
    feature derivation and the scorer itself are the code under test.
    """

    sources: list[str]  # score-time sources (pre-_infra_boost), sorted
    cert_org_names: list[str]  # sorted
    resolves: bool
    evidence_count: int  # len(evidence) at score time (pre-dedup)
    unique_cert_count: int  # distinct cert_ids at score time (pre-dedup)
    rdap_similarity: float  # max rdap_registrant_match similarity at score time
    # True when _infra_boost later added its +0.05 to this domain's confidence.
    infra_boosted: bool = False


class DiscoveredDomain(BaseModel):
    """A single domain discovered during the search."""

    domain: str
    confidence: float = Field(ge=0.0, le=1.0)
    # Identity of the scorer that produced `confidence`. Heuristic ladder scores and
    # learned calibrated probabilities are NOT comparable — consumers must not diff
    # confidence values across differing scorer identities (see delta._diff_domain).
    # Defaults to "unknown" so results persisted before schema 1.1 still validate.
    scorer_id: str = "unknown"
    scorer_version: str = "unknown"
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

    # 1.1: added DiscoveredDomain.scorer_id / scorer_version (additive, issue #184)
    schema_version: str = "1.1"
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
