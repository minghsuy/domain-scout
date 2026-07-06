"""Evaluation harness for domain-scout: precision/recall against labeled ground truth.

Supports two modes:
  - baseline: load pre-recorded ScoutResult JSON files and compute metrics
  - live: run Scout.discover_async() against real services and compute metrics

Usage:
  python -m domain_scout.eval --mode baseline
  python -m domain_scout.eval --mode live --output json
"""

from __future__ import annotations

import argparse
import asyncio
import math
import sys
from pathlib import Path

from pydantic import BaseModel, Field

from domain_scout.models import ScoutResult

_GROUND_TRUTH_PATH = Path(__file__).parent / "eval_ground_truth.yaml"
_BASELINES_DIR = Path(__file__).parent.parent / "baselines"

_DEFAULT_K_VALUES = (5, 10, 20)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class GroundTruthEntry(BaseModel):
    """A single labeled entity with known owned and not-owned domains."""

    label_id: str
    company_name: str
    seeds: list[str]
    owned_domains: list[str]
    not_owned: list[str] = Field(default_factory=list)


class MetricsAtK(BaseModel):
    """Precision, recall, NDCG, and FP count at a specific k."""

    k: int
    hits: int
    precision: float
    recall: float
    ndcg: float
    false_positives: int


class EntityEvalResult(BaseModel):
    """Evaluation result for a single entity."""

    label_id: str
    company_name: str
    seeds: list[str]
    discovered_count: int
    owned_count: int
    not_owned_count: int
    metrics: list[MetricsAtK]
    false_positive_domains: list[str] = Field(default_factory=list)


class EvalReport(BaseModel):
    """Complete evaluation report across all entities.

    ``entities`` carries the heuristic leg (ranking as recorded in the
    ScoutResult). ``learned_entities`` carries the learned-scorer leg: the
    same results re-scored and re-ranked through the learned model, so
    ``make eval`` exercises both scorer paths pre-ship (issue #183).
    """

    mode: str
    entities: list[EntityEvalResult]
    learned_entities: list[EntityEvalResult] = Field(default_factory=list)
    # Identity of the learned scorer that produced learned_entities,
    # e.g. "learned_lr/v1@2026-03-01+uncal" (None if the leg didn't run).
    learned_scorer: str | None = None


# ---------------------------------------------------------------------------
# Metric computation (pure functions, no I/O)
# ---------------------------------------------------------------------------


def compute_metrics(
    ranked_domains: list[str],
    owned: set[str],
    not_owned: set[str],
    k_values: tuple[int, ...] = _DEFAULT_K_VALUES,
) -> list[MetricsAtK]:
    """Compute precision@k, recall@k, NDCG@k, and FP count for given k values.

    Precision is conservative: domains not in ``owned`` count against precision
    (i.e. unknown = not relevant). This incentivizes expanding labels.

    False positives are domains explicitly in ``not_owned`` that appear in top-k.
    """
    results: list[MetricsAtK] = []
    for k in k_values:
        top_k = ranked_domains[:k]

        # Precision: |top_k ∩ owned| / min(k, discovered)
        # Adaptive denominator: don't penalize entities that discovered fewer
        # than k domains.  Unknown domains in top_k still count against.
        hits = sum(1 for d in top_k if d in owned)
        denom = min(k, len(ranked_domains)) if ranked_domains else 0
        precision = hits / denom if denom > 0 else 0.0

        # Recall: |top_k ∩ owned| / |owned|
        recall = hits / len(owned) if owned else 0.0

        # NDCG@k: normalized discounted cumulative gain
        ndcg = _ndcg_at_k(top_k, owned, k)

        # False positives: explicit not_owned in top-k
        fps = [d for d in top_k if d in not_owned]

        results.append(
            MetricsAtK(
                k=k,
                hits=hits,
                precision=round(precision, 3),
                recall=round(recall, 3),
                ndcg=round(ndcg, 3),
                false_positives=len(fps),
            )
        )
    return results


def _dcg_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    """Discounted cumulative gain at k."""
    dcg = 0.0
    for i, domain in enumerate(ranked[:k]):
        if domain in relevant:
            dcg += 1.0 / math.log2(i + 2)  # standard DCG: log2(rank+1), rank is 1-based
    return dcg


def _ndcg_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    """Normalized DCG at k."""
    dcg = _dcg_at_k(ranked, relevant, k)
    # Ideal DCG: all relevant items at the top
    ideal_count = min(k, len(relevant))
    idcg = sum(1.0 / math.log2(i + 2) for i in range(ideal_count))
    if idcg == 0.0:
        return 0.0
    return dcg / idcg


def collect_false_positives(ranked_domains: list[str], not_owned: set[str]) -> list[str]:
    """Return all explicitly-labeled false positives found anywhere in ranked results."""
    return [d for d in ranked_domains if d in not_owned]


# ---------------------------------------------------------------------------
# Ground truth loading
# ---------------------------------------------------------------------------


def load_ground_truth(path: Path | None = None) -> list[GroundTruthEntry]:
    """Load and validate ground truth labels from YAML."""
    try:
        import yaml
    except ImportError as exc:
        raise ImportError(
            "pyyaml is required for the eval harness. "
            "Install with: pip install domain-scout-ct[eval]"
        ) from exc

    gt_path = path or _GROUND_TRUTH_PATH
    with open(gt_path) as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, list):
        raise ValueError(f"Ground truth file must be a YAML list, got {type(raw).__name__}")

    return [GroundTruthEntry.model_validate(entry) for entry in raw]


# ---------------------------------------------------------------------------
# Per-entity evaluation (shared by both scorer legs)
# ---------------------------------------------------------------------------


def _entity_result(
    gt: GroundTruthEntry,
    ranked: list[str],
    k_values: tuple[int, ...],
) -> EntityEvalResult:
    """Build an EntityEvalResult for one entity from a ranked domain list."""
    owned = set(gt.owned_domains)
    not_owned_set = set(gt.not_owned)

    return EntityEvalResult(
        label_id=gt.label_id,
        company_name=gt.company_name,
        seeds=gt.seeds,
        discovered_count=len(ranked),
        owned_count=len(owned),
        not_owned_count=len(not_owned_set),
        metrics=compute_metrics(ranked, owned, not_owned_set, k_values),
        false_positive_domains=collect_false_positives(ranked, not_owned_set),
    )


def _learned_ranked_domains(scout_result: ScoutResult) -> list[str]:
    """Re-rank a ScoutResult's domains with the learned scorer.

    Mirrors the gating in ``Scout._score_confidence``: the learned path fires
    only for domains with cert org evidence; every other domain keeps its
    recorded (heuristic) confidence — the same mixed ranking production
    produces with ``use_learned_scorer=True``.

    This is an approximation, not a bit-exact replay of a live learned run.
    Production scores from the pre-output ``_DomainAccum`` state; here only
    the persisted ``DiscoveredDomain`` is available, which differs in three
    ways: (1) the post-scoring ``_infra_boost`` +0.05 addend is not
    re-applied; (2) ``sources`` may contain the ``shared_infra`` tag that
    ``_infra_boost`` added *after* production scored (flipping the
    ``has_shared_infra`` feature); (3) ``evidence`` has been through
    ``_dedup_evidence``, so ``evidence_count``/``unique_cert_count`` can be
    lower than what production saw. Good enough to exercise the learned path
    and compare rankings pre-ship; not a substitute for a live
    ``use_learned_scorer=True`` eval.
    """
    from domain_scout.matching.entity_match import org_name_similarity
    from domain_scout.scorer import score_confidence

    company = scout_result.entity.company_name
    scored: list[tuple[float, str]] = []
    for d in scout_result.domains:
        if d.domain and d.cert_org_names:
            best_sim = max(
                (org_name_similarity(org, company) for org in d.cert_org_names),
                default=0.0,
            )
            cert_ids = {ev.cert_id for ev in d.evidence if ev.cert_id is not None}
            rdap_sim = max(
                (
                    ev.similarity_score
                    for ev in d.evidence
                    if ev.source_type == "rdap_registrant_match" and ev.similarity_score is not None
                ),
                default=0.0,
            )
            confidence = score_confidence(
                domain=d.domain,
                company_name=company,
                best_similarity=best_sim,
                sources=set(d.sources),
                cert_org_names=set(d.cert_org_names),
                resolves=d.resolves,
                evidence_count=len(d.evidence),
                unique_cert_count=len(cert_ids),
                rdap_similarity=rdap_sim,
            )
        else:
            confidence = d.confidence
        scored.append((confidence, d.domain))

    # Stable sort: ties keep their recorded relative order.
    scored.sort(key=lambda item: item[0], reverse=True)
    return [domain for _, domain in scored]


def _learned_scorer_identity() -> str:
    """Identity string for the learned leg, e.g. "learned_lr/v1@2026-03-01+uncal"."""
    from domain_scout.scorer import SCORER_ID, scorer_version

    return f"{SCORER_ID}/{scorer_version()}"


# ---------------------------------------------------------------------------
# Baseline evaluation
# ---------------------------------------------------------------------------


def evaluate_baseline(
    ground_truth: list[GroundTruthEntry],
    baselines_dir: Path | None = None,
    k_values: tuple[int, ...] = _DEFAULT_K_VALUES,
) -> EvalReport:
    """Evaluate pre-recorded baseline JSON files against ground truth.

    Runs both scorer legs: the recorded (heuristic) ranking and a learned
    re-scoring of the same evidence.
    """
    bdir = baselines_dir or _BASELINES_DIR
    results: list[EntityEvalResult] = []
    learned_results: list[EntityEvalResult] = []

    for gt in ground_truth:
        baseline_path = bdir / f"{gt.label_id}.json"
        if not baseline_path.exists():
            print(f"WARNING: baseline not found: {baseline_path}", file=sys.stderr)
            continue

        with open(baseline_path) as f:
            scout_result = ScoutResult.model_validate_json(f.read())

        # Ranked domain list (already sorted by confidence desc in ScoutResult)
        ranked = [d.domain for d in scout_result.domains]
        results.append(_entity_result(gt, ranked, k_values))
        learned_results.append(_entity_result(gt, _learned_ranked_domains(scout_result), k_values))

    return EvalReport(
        mode="baseline",
        entities=results,
        learned_entities=learned_results,
        learned_scorer=_learned_scorer_identity() if learned_results else None,
    )


# ---------------------------------------------------------------------------
# Live evaluation
# ---------------------------------------------------------------------------


async def evaluate_live(
    ground_truth: list[GroundTruthEntry],
    k_values: tuple[int, ...] = _DEFAULT_K_VALUES,
) -> EvalReport:
    """Run Scout.discover_async() for each entity and evaluate against ground truth.

    Discovery runs once per entity; both scorer legs are computed from the
    same ScoutResult (the learned leg re-scores the collected evidence).
    """
    from domain_scout.models import EntityInput
    from domain_scout.scout import Scout

    scout = Scout()
    results: list[EntityEvalResult] = []
    learned_results: list[EntityEvalResult] = []

    for gt in ground_truth:
        entity = EntityInput(
            company_name=gt.company_name,
            seed_domain=gt.seeds,
        )
        scout_result = await scout.discover_async(entity)

        ranked = [d.domain for d in scout_result.domains]
        results.append(_entity_result(gt, ranked, k_values))
        learned_results.append(_entity_result(gt, _learned_ranked_domains(scout_result), k_values))

    return EvalReport(
        mode="live",
        entities=results,
        learned_entities=learned_results,
        learned_scorer=_learned_scorer_identity() if learned_results else None,
    )


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


def _format_entity(entity: EntityEvalResult, lines: list[str]) -> None:
    """Append one entity's metrics block to the output lines."""
    seeds_str = ", ".join(entity.seeds)
    lines.append("")
    lines.append(f"{entity.label_id} ({entity.company_name}, seeds={seeds_str})")
    lines.append(
        f"  Discovered: {entity.discovered_count} | "
        f"Ground truth: {entity.owned_count} owned, "
        f"{entity.not_owned_count} not-owned"
    )

    # Table header
    lines.append(f"  {'k':>5}  {'Precision':>9}  {'Found':>9}  {'FPs':>3}  {'NDCG':>5}")
    lines.append(f"  {'─' * 5}  {'─' * 9}  {'─' * 9}  {'─' * 3}  {'─' * 5}")

    for m in entity.metrics:
        found_str = f"{m.hits}/{entity.owned_count}"
        lines.append(
            f"  {m.k:>5}  {m.precision:>9.3f}  {found_str:>9}  {m.false_positives:>3}  "
            f"{m.ndcg:>5.3f}"
        )

    if entity.false_positive_domains:
        lines.append(f"  FP domains (all ranks): {', '.join(entity.false_positive_domains)}")


def format_table(report: EvalReport) -> str:
    """Format an EvalReport as a human-readable table."""
    lines: list[str] = []
    lines.append(f"Evaluation Report ({report.mode} mode)")
    lines.append("=" * 55)

    for entity in report.entities:
        _format_entity(entity, lines)

    if report.learned_entities:
        lines.append("")
        lines.append(f"Learned scorer leg ({report.learned_scorer})")
        lines.append("=" * 55)
        for entity in report.learned_entities:
            _format_entity(entity, lines)

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    """Entry point for python -m domain_scout.eval."""
    parser = argparse.ArgumentParser(
        prog="domain_scout.eval",
        description="Evaluate domain-scout precision/recall against labeled ground truth.",
    )
    parser.add_argument(
        "--mode",
        choices=["baseline", "live"],
        default="baseline",
        help="Evaluation mode: baseline (pre-recorded JSON) or live (real queries)",
    )
    parser.add_argument(
        "--output",
        choices=["table", "json"],
        default="table",
        help="Output format (default: table)",
    )
    parser.add_argument(
        "--ground-truth",
        type=Path,
        default=None,
        help="Path to ground truth YAML (default: built-in eval_ground_truth.yaml)",
    )
    parser.add_argument(
        "--baselines-dir",
        type=Path,
        default=None,
        help="Path to baselines directory (default: baselines/)",
    )
    parser.add_argument(
        "--label",
        type=str,
        default=None,
        help="Evaluate only this label_id (default: all)",
    )

    args = parser.parse_args(argv)

    ground_truth = load_ground_truth(args.ground_truth)

    if args.label:
        ground_truth = [gt for gt in ground_truth if gt.label_id == args.label]
        if not ground_truth:
            print(f"ERROR: label_id '{args.label}' not found in ground truth", file=sys.stderr)
            sys.exit(1)

    if args.mode == "baseline":
        report = evaluate_baseline(ground_truth, args.baselines_dir)
    else:
        report = asyncio.run(evaluate_live(ground_truth))

    if args.output == "json":
        print(report.model_dump_json(indent=2))
    else:
        print(format_table(report))


if __name__ == "__main__":
    main()
