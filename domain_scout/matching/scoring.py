"""Confidence scoring for discovered domains."""

from __future__ import annotations

from typing import TYPE_CHECKING

from domain_scout.matching.entity_match import org_name_similarity

if TYPE_CHECKING:
    from domain_scout.config import ScoutConfig
    from domain_scout.models import DomainAccumulator


class ConfidenceScorer:
    """Orchestrates confidence scoring using heuristic or learned models."""

    def __init__(self, config: ScoutConfig) -> None:
        self.config = config

    def score(
        self,
        accum: DomainAccumulator,
        company_name: str,
        seed_domains: list[str],
        domain: str = "",
    ) -> float:
        """Calculate confidence score for a domain discovery."""
        if self.config.use_learned_scorer and domain and accum.cert_org_names:
            return self._score_learned(accum, company_name, domain)

        return self._score_heuristic(accum, company_name)

    def _score_learned(
        self,
        accum: DomainAccumulator,
        company_name: str,
        domain: str,
    ) -> float:
        """Score using the learned logistic regression model."""
        from domain_scout.scorer import score_confidence as _learned_score

        best_sim = max(
            (org_name_similarity(cert_org, company_name) for cert_org in accum.cert_org_names),
            default=0.0,
        )

        # Count unique cert IDs for evidence_density
        cert_ids: set[int] = set()
        for ev in accum.evidence:
            if ev.cert_id is not None:
                cert_ids.add(ev.cert_id)

        # Extract max RDAP registrant similarity from evidence
        rdap_sim = max(
            (
                ev.similarity_score
                for ev in accum.evidence
                if ev.source_type == "rdap_registrant_match" and ev.similarity_score is not None
            ),
            default=0.0,
        )

        return _learned_score(
            domain=domain,
            company_name=company_name,
            best_similarity=best_sim,
            sources=accum.sources,
            cert_org_names=accum.cert_org_names,
            resolves=accum.resolves,
            evidence_count=len(accum.evidence),
            unique_cert_count=len(cert_ids),
            rdap_similarity=rdap_sim,
        )

    def _score_heuristic(self, accum: DomainAccumulator, company_name: str) -> float:
        """Score using rule-based heuristics."""
        # Phase 1: base score from source type
        score = 0.0

        if "cross_seed_verified" in accum.sources:
            score = max(score, 0.90)
        if "ct_org_match" in accum.sources:
            score = max(score, 0.85)
        if "ct_subsidiary_match" in accum.sources:
            score = max(score, 0.80)
        if any(s.startswith("ct_san_expansion:") for s in accum.sources):
            score = max(score, 0.80)
        if any(s.startswith("ct_seed_subdomain:") for s in accum.sources):
            score = max(score, 0.75)
        if any(s.startswith("ct_seed_related:") for s in accum.sources):
            score = max(score, 0.40)
        if "dns_guess" in accum.sources and "ct_org_match" not in accum.sources:
            score = max(score, 0.30)

        # Phase 2: corroboration level adjustment
        # dns_guess bypasses corroboration — it already implies resolution
        if score <= 0.30:
            return round(score, 2)

        has_resolves = accum.resolves
        has_rdap = "rdap_registrant_match" in accum.sources

        best_sim = max(
            (org_name_similarity(cert_org, company_name) for cert_org in accum.cert_org_names),
            default=0.0,
        )
        has_high_sim = best_sim > 0.9

        has_multi_source = len(accum.sources) >= 3

        if has_resolves and (has_rdap or has_high_sim) and has_multi_source:
            adjustment = 0.10  # Level 3: strong corroboration
        elif has_resolves and (has_rdap or has_high_sim or has_multi_source):
            adjustment = 0.05  # Level 2: moderate corroboration
        elif has_resolves:
            adjustment = 0.00  # Level 1: resolves only
        else:
            adjustment = -0.05  # Level 0: no resolution

        score = min(1.0, max(0.0, score + adjustment))

        return round(score, 2)
