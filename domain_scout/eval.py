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
    """Complete evaluation report across all entities."""

    mode: str
    entities: list[EntityEvalResult]


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

        # Precision: |top_k ∩ owned| / k  (conservative — unknown != relevant)
        hits = sum(1 for d in top_k if d in owned)
        precision = hits / k if k > 0 else 0.0

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
# Baseline evaluation
# ---------------------------------------------------------------------------


def evaluate_baseline(
    ground_truth: list[GroundTruthEntry],
    baselines_dir: Path | None = None,
    k_values: tuple[int, ...] = _DEFAULT_K_VALUES,
) -> EvalReport:
    """Evaluate pre-recorded baseline JSON files against ground truth."""
    bdir = baselines_dir or _BASELINES_DIR
    results: list[EntityEvalResult] = []

    for gt in ground_truth:
        baseline_path = bdir / f"{gt.label_id}.json"
        if not baseline_path.exists():
            print(f"WARNING: baseline not found: {baseline_path}", file=sys.stderr)
            continue

        with open(baseline_path) as f:
            scout_result = ScoutResult.model_validate_json(f.read())

        # Extract ranked domain list (already sorted by confidence desc in ScoutResult)
        ranked = [d.domain for d in scout_result.domains]
        owned = set(gt.owned_domains)
        not_owned_set = set(gt.not_owned)

        metrics = compute_metrics(ranked, owned, not_owned_set, k_values)
        fps = collect_false_positives(ranked, not_owned_set)

        results.append(
            EntityEvalResult(
                label_id=gt.label_id,
                company_name=gt.company_name,
                seeds=gt.seeds,
                discovered_count=len(ranked),
                owned_count=len(owned),
                not_owned_count=len(not_owned_set),
                metrics=metrics,
                false_positive_domains=fps,
            )
        )

    return EvalReport(mode="baseline", entities=results)


# ---------------------------------------------------------------------------
# Live evaluation
# ---------------------------------------------------------------------------


async def evaluate_live(
    ground_truth: list[GroundTruthEntry],
    k_values: tuple[int, ...] = _DEFAULT_K_VALUES,
) -> EvalReport:
    """Run Scout.discover_async() for each entity and evaluate against ground truth."""
    from domain_scout.models import EntityInput
    from domain_scout.scout import Scout

    scout = Scout()
    results: list[EntityEvalResult] = []

    for gt in ground_truth:
        entity = EntityInput(
            company_name=gt.company_name,
            seed_domain=gt.seeds,
        )
        scout_result = await scout.discover_async(entity)

        ranked = [d.domain for d in scout_result.domains]
        owned = set(gt.owned_domains)
        not_owned_set = set(gt.not_owned)

        metrics = compute_metrics(ranked, owned, not_owned_set, k_values)
        fps = collect_false_positives(ranked, not_owned_set)

        results.append(
            EntityEvalResult(
                label_id=gt.label_id,
                company_name=gt.company_name,
                seeds=gt.seeds,
                discovered_count=len(ranked),
                owned_count=len(owned),
                not_owned_count=len(not_owned_set),
                metrics=metrics,
                false_positive_domains=fps,
            )
        )

    return EvalReport(mode="live", entities=results)


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


def format_table(report: EvalReport) -> str:
    """Format an EvalReport as a human-readable table."""
    lines: list[str] = []
    lines.append(f"Evaluation Report ({report.mode} mode)")
    lines.append("=" * 55)

    for entity in report.entities:
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
