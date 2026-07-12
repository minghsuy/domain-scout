"""Evaluation harness for domain-scout: precision/recall against labeled ground truth.

Supports three modes:
  - baseline: load pre-recorded BaselineRecord JSON files (ScoutResult + the
    score-time ScoringInputs production scoring consumed, #187) and compute metrics
  - live: run Scout.discover_async() against real services and compute metrics
  - record: run live discovery and persist the results as the baseline substrate
    (baselines/{label_id}.json) plus a provenance manifest (baselines/manifest.json)

Usage:
  python -m domain_scout.eval --mode baseline
  python -m domain_scout.eval --mode live --output json
  python -m domain_scout.eval --mode record --limit 3   # regenerate the substrate

The ``baseline`` substrate (``baselines/`` + its manifest) is git-ignored and
generated locally by ``--mode record`` (``make eval-baselines``). It is a
point-in-time snapshot of live CT data: re-running the generator is safe and the
manifest stamps provenance (generated_at, tool_version, scorer identity, git
commit) so decay is *detectable*. It is not byte-identical across runs.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import math
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from domain_scout.models import (  # noqa: TC001 — Pydantic needs runtime imports
    ScoringInputs,
    ScoutResult,
)

_GROUND_TRUTH_PATH = Path(__file__).parent / "eval_ground_truth.yaml"
_BASELINES_DIR = Path(__file__).parent.parent / "baselines"
_MANIFEST_NAME = "manifest.json"

# Substrate schema version, stamped in the manifest by --mode record.
#   1: baseline files were plain ScoutResult JSON (pre-#187). The learned leg
#      could only approximate production scoring from post-pipeline state.
#   2: baseline files are BaselineRecord JSON — the ScoutResult plus per-domain
#      score-time ScoringInputs, so the learned leg replays production exactly.
# evaluate_baseline refuses (loudly) any other version: an old substrate must
# be re-recorded, never silently scored the approximated way.
_SUBSTRATE_SCHEMA = 2

_DEFAULT_K_VALUES = (5, 10, 20)


class EvalSubstrateError(RuntimeError):
    """The baseline substrate (``baselines/`` + manifest) is missing or corrupt.

    Raised instead of silently producing an empty report so that a vanished or
    tampered substrate fails the eval loudly rather than reading as a passing /
    neutral run (issue #188, gate 3).
    """


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
    ScoutResult). ``learned_entities`` carries the learned-scorer leg: the same
    results re-scored through the learned model from the captured score-time
    inputs (an exact replay of production scoring, #187), so ``make eval``
    exercises both scorer paths pre-ship (issue #183).
    """

    mode: str
    entities: list[EntityEvalResult]
    learned_entities: list[EntityEvalResult] = Field(default_factory=list)
    # Identity of the learned scorer that produced learned_entities,
    # e.g. "learned_lr/v1@2026-03-01+uncal" (None if the leg didn't run).
    learned_scorer: str | None = None


class BaselineManifestEntry(BaseModel):
    """One recorded baseline file, keyed to its ground-truth label."""

    label_id: str
    file: str  # filename relative to the baselines dir, e.g. "panw-20260217.json"
    sha256: str  # digest of the file bytes at record time (corruption / drift check)
    domains: int  # number of domains in the recorded ScoutResult


class BaselineManifest(BaseModel):
    """Provenance for a generated baseline substrate.

    Written by ``--mode record`` and read by ``--mode baseline``. It lists
    exactly the files the generator wrote this run, so the eval can fail loudly
    when a referenced file goes absent or is tampered with, and so substrate
    decay is detectable (``generated_at`` / ``scorer`` / ``git_commit``).
    """

    generator: str = "domain_scout.eval --mode record"
    generated_at: str  # ISO-8601 UTC timestamp
    tool_version: str
    scorer: str  # learned-scorer identity at generation time (e.g. learned_lr/v1@...)
    source: str  # provenance of the underlying data, e.g. "live crt.sh discovery"
    mode: str = "live"
    git_commit: str | None = None
    # Defaults to 1 so pre-#187 manifests (which lack the field) validate and
    # are then refused with an explicit re-record message (see _SUBSTRATE_SCHEMA).
    substrate_schema: int = 1
    entries: list[BaselineManifestEntry]


class BaselineRecord(BaseModel):
    """One substrate file (schema 2): the persisted result plus score-time inputs.

    ``scoring_inputs`` maps every scored candidate domain (a superset of
    ``scout_result.domains`` — sub-threshold candidates are captured too) to the
    exact inputs production scoring consumed, captured by ``--mode record``
    before ``_infra_boost``/dedup mutated them (issue #187). The learned eval
    leg replays production scoring from these instead of approximating it from
    the post-pipeline ``DiscoveredDomain`` state.
    """

    scout_result: ScoutResult
    scoring_inputs: dict[str, ScoringInputs] = Field(default_factory=dict)


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


def _learned_scored_domains(record: BaselineRecord) -> list[tuple[float, str]]:
    """Score each recorded domain exactly as production's learned path would.

    Replays ``Scout._score_confidence`` (learned path) plus the post-scoring
    ``_infra_boost`` addend from the score-time :class:`ScoringInputs` captured
    at record time — the same gating (learned fires only when score-time cert
    org evidence exists; every other domain keeps its recorded confidence), the
    same pre-boost sources, the same pre-dedup evidence aggregates, and the same
    boost formula/cap/rounding. This closes the #187 approximation: the eval leg
    now scores the SAME inputs production scores.

    Residual, disclosed differences from a live ``use_learned_scorer=True`` run
    (all downstream of scoring itself, not of the scoring inputs): which
    candidates ``_infra_boost`` *checked* and which domains cleared
    ``inclusion_threshold`` were decided by the recorded run's (heuristic)
    confidences; under a live learned run those selection sets could differ.
    """
    from domain_scout.matching.entity_match import org_name_similarity
    from domain_scout.scorer import score_confidence

    result = record.scout_result
    company = result.entity.company_name
    scored: list[tuple[float, str]] = []
    for d in result.domains:
        inputs = record.scoring_inputs.get(d.domain)
        if inputs is None:
            raise EvalSubstrateError(
                f"Substrate record for {company!r} has no score-time scoring inputs for "
                f"domain {d.domain!r} — the record is incomplete and the learned leg "
                f"cannot replay production scoring. Re-record with `make eval-baselines`."
            )
        if d.domain and inputs.cert_org_names:
            best_sim = max(
                (org_name_similarity(org, company) for org in inputs.cert_org_names),
                default=0.0,
            )
            confidence = score_confidence(
                domain=d.domain,
                company_name=company,
                best_similarity=best_sim,
                sources=set(inputs.sources),
                cert_org_names=set(inputs.cert_org_names),
                resolves=inputs.resolves,
                evidence_count=inputs.evidence_count,
                unique_cert_count=inputs.unique_cert_count,
                rdap_similarity=inputs.rdap_similarity,
            )
            if inputs.infra_boosted:
                # Same formula, cap, and rounding as Scout._infra_boost.
                max_conf = 1.0 if "cross_seed_verified" in inputs.sources else 0.95
                confidence = round(min(max_conf, confidence + 0.05), 2)
        else:
            confidence = d.confidence
        scored.append((confidence, d.domain))
    return scored


def _learned_ranked_domains(record: BaselineRecord) -> list[str]:
    """Rank a substrate record's domains by exact-replayed learned confidence."""
    scored = _learned_scored_domains(record)
    # Stable sort: ties keep their recorded relative order.
    scored.sort(key=lambda item: item[0], reverse=True)
    return [domain for _, domain in scored]


def _learned_scorer_identity() -> str:
    """Identity string for the learned leg, e.g. "learned_lr/v1@2026-03-01+uncal"."""
    from domain_scout.scorer import SCORER_ID, scorer_version

    return f"{SCORER_ID}/{scorer_version()}"


# ---------------------------------------------------------------------------
# Substrate provenance (manifest) helpers
# ---------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    """SHA-256 of a file's bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_commit() -> str | None:
    """Best-effort short-circuiting lookup of the current git commit (or None)."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def _load_manifest(baselines_dir: Path) -> BaselineManifest | None:
    """Load the baseline manifest if present, else None.

    A present-but-unparseable manifest (e.g. truncated by an interrupted write)
    is a loud error, not a fall-through to "no manifest".
    """
    manifest_path = baselines_dir / _MANIFEST_NAME
    if not manifest_path.exists():
        return None
    try:
        return BaselineManifest.model_validate_json(manifest_path.read_text())
    except ValidationError as exc:
        raise EvalSubstrateError(
            f"Baseline manifest at {manifest_path} is present but unparseable "
            f"(truncated or malformed?). Regenerate with `make eval-baselines`. — {exc}"
        ) from exc


def _resolve_manifest_substrate(
    manifest: BaselineManifest,
    baselines_dir: Path,
    gt_by_id: dict[str, GroundTruthEntry],
) -> list[tuple[GroundTruthEntry, BaselineRecord]]:
    """Validate the manifest-referenced files in scope, then load them.

    Validation (existence + sha256) covers exactly the manifest entries whose
    ``label_id`` is in the requested ground truth. An unscoped run passes the
    full ground truth, so the whole substrate is still checked; a ``--label``
    debug run is not blocked by an unrelated entry's missing/corrupt file.

    Fails loudly (``EvalSubstrateError``) if any in-scope referenced file is
    absent or its on-disk digest no longer matches the manifest — a vanished or
    tampered substrate must not read as a passing eval (issue #188, gate 3).
    """
    relevant = [entry for entry in manifest.entries if entry.label_id in gt_by_id]
    if not relevant:
        raise EvalSubstrateError(
            f"Baseline manifest at {baselines_dir / _MANIFEST_NAME} has no entries matching "
            f"the ground truth ({len(manifest.entries)} manifest entries, "
            f"{len(gt_by_id)} ground-truth label(s)). Nothing to evaluate."
        )

    pairs: list[tuple[GroundTruthEntry, BaselineRecord]] = []
    missing: list[str] = []
    corrupt: list[str] = []

    for entry in relevant:
        fpath = baselines_dir / entry.file
        if not fpath.exists():
            missing.append(entry.file)
            continue
        actual = _sha256_file(fpath)
        if actual != entry.sha256:
            corrupt.append(f"{entry.file} (manifest {entry.sha256[:12]}, on-disk {actual[:12]})")
            continue
        pairs.append(
            (gt_by_id[entry.label_id], BaselineRecord.model_validate_json(fpath.read_text()))
        )

    if missing or corrupt:
        details: list[str] = []
        if missing:
            details.append(f"absent: {', '.join(missing)}")
        if corrupt:
            details.append(f"corrupt (sha256 mismatch): {'; '.join(corrupt)}")
        raise EvalSubstrateError(
            f"Baseline manifest at {baselines_dir / _MANIFEST_NAME} references "
            f"{len(missing)} absent and {len(corrupt)} corrupt file(s). "
            f"Regenerate with `make eval-baselines`. — " + " | ".join(details)
        )

    return pairs


# ---------------------------------------------------------------------------
# Baseline evaluation
# ---------------------------------------------------------------------------


def evaluate_baseline(
    ground_truth: list[GroundTruthEntry],
    baselines_dir: Path | None = None,
    k_values: tuple[int, ...] = _DEFAULT_K_VALUES,
) -> EvalReport:
    """Evaluate the recorded baseline substrate against ground truth.

    Runs both scorer legs: the recorded (heuristic) ranking and an exact
    learned-path replay from the score-time inputs captured at record time
    (issue #187). Substrates recorded under an older schema (no captured
    scoring inputs) are refused loudly — re-record rather than silently
    falling back to approximating from post-pipeline state.

    The ``manifest.json`` written by ``--mode record`` is the substrate's single
    source of truth and is *required*: every file it lists must exist and match
    its recorded digest, and exactly those files (that also have a ground-truth
    label) are evaluated. A missing manifest (e.g. a fresh checkout, or a
    ``record`` run interrupted before the manifest was written), a
    missing/corrupt referenced file, or an empty substrate raises
    :class:`EvalSubstrateError` rather than yielding a silent, empty (and
    misleadingly "passing") report — the #188 gate-3 defect.
    """
    bdir = baselines_dir or _BASELINES_DIR
    manifest = _load_manifest(bdir)
    if manifest is None:
        raise EvalSubstrateError(
            f"No baseline manifest ({_MANIFEST_NAME}) in {bdir}. The eval substrate is "
            f"generated locally and git-ignored; run `make eval-baselines` to create it. "
            f"(Loose {{label_id}}.json files without a manifest are not trusted — an "
            f"interrupted `record` run can leave a partial set.)"
        )

    if manifest.substrate_schema != _SUBSTRATE_SCHEMA:
        raise EvalSubstrateError(
            f"Baseline substrate at {bdir} was recorded under substrate schema "
            f"{manifest.substrate_schema}; this eval requires schema {_SUBSTRATE_SCHEMA}, "
            f"which captures score-time scoring inputs at record time so the learned leg "
            f"replays production scoring exactly instead of approximating it from "
            f"post-pipeline state (issue #187). Re-record with `make eval-baselines`."
        )

    gt_by_id = {gt.label_id: gt for gt in ground_truth}
    pairs = _resolve_manifest_substrate(manifest, bdir, gt_by_id)
    current_scorer = _learned_scorer_identity()
    if manifest.scorer != current_scorer:
        print(
            f"WARNING: baseline substrate was scored under {manifest.scorer} but the "
            f"current scorer is {current_scorer}; learned-leg deltas may not reflect the "
            f"shipping scorer. Regenerate with `make eval-baselines`.",
            file=sys.stderr,
        )

    results: list[EntityEvalResult] = []
    learned_results: list[EntityEvalResult] = []
    for gt, record in pairs:
        # Ranked domain list (already sorted by confidence desc in ScoutResult)
        ranked = [d.domain for d in record.scout_result.domains]
        results.append(_entity_result(gt, ranked, k_values))
        learned_results.append(_entity_result(gt, _learned_ranked_domains(record), k_values))

    return EvalReport(
        mode="baseline",
        entities=results,
        learned_entities=learned_results,
        learned_scorer=_learned_scorer_identity() if learned_results else None,
    )


# ---------------------------------------------------------------------------
# Baseline substrate generation (record mode)
# ---------------------------------------------------------------------------


async def record_baselines(
    ground_truth: list[GroundTruthEntry],
    baselines_dir: Path | None = None,
    *,
    limit: int | None = None,
    source: str = "live crt.sh discovery",
) -> BaselineManifest:
    """Run live discovery for each entity and persist the substrate + manifest.

    Deterministic in *process* (re-runnable, same inputs -> same file layout),
    not in *bytes*: the underlying CT data is point-in-time, so successive runs
    can differ. The manifest stamps provenance so that drift is detectable.

    ``limit`` records only the first N ground-truth entries (a smoke-scale
    substrate); the manifest then references only the files this run wrote.
    Uses the default :class:`ScoutConfig` (``use_learned_scorer=False``), so the
    recorded ranking is the heuristic leg production emits by default.
    """
    from domain_scout import __version__
    from domain_scout.models import EntityInput
    from domain_scout.scout import Scout

    bdir = baselines_dir or _BASELINES_DIR
    bdir.mkdir(parents=True, exist_ok=True)
    targets = ground_truth[:limit] if limit is not None else ground_truth

    scout = Scout()
    entries: list[BaselineManifestEntry] = []
    for gt in targets:
        entity = EntityInput(company_name=gt.company_name, seed_domain=gt.seeds)
        # Capture score-time scoring inputs alongside the result (issue #187):
        # the learned eval leg replays production scoring from these instead of
        # approximating it from the post-pipeline DiscoveredDomain state.
        scoring_capture: dict[str, ScoringInputs] = {}
        scout_result = await scout.discover_async(entity, scoring_capture=scoring_capture)
        record = BaselineRecord(scout_result=scout_result, scoring_inputs=scoring_capture)

        fname = f"{gt.label_id}.json"
        fpath = bdir / fname
        fpath.write_text(record.model_dump_json(indent=2))
        entries.append(
            BaselineManifestEntry(
                label_id=gt.label_id,
                file=fname,
                sha256=_sha256_file(fpath),
                domains=len(scout_result.domains),
            )
        )
        print(f"recorded {gt.label_id}: {len(scout_result.domains)} domains", file=sys.stderr)

    entries.sort(key=lambda e: e.label_id)
    manifest = BaselineManifest(
        generated_at=datetime.now(UTC).isoformat(),
        tool_version=__version__,
        scorer=_learned_scorer_identity(),
        source=source,
        mode="live",
        git_commit=_git_commit(),
        substrate_schema=_SUBSTRATE_SCHEMA,
        entries=entries,
    )
    # Write the manifest atomically (tmp + rename) and last: readers treat the
    # manifest as proof of a completed run, so it must never appear truncated or
    # ahead of the files it references (matches the repo's atomic-write pattern).
    manifest_path = bdir / _MANIFEST_NAME
    tmp_path = manifest_path.with_suffix(".json.tmp")
    tmp_path.write_text(manifest.model_dump_json(indent=2) + "\n")
    tmp_path.replace(manifest_path)
    return manifest


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
        scoring_capture: dict[str, ScoringInputs] = {}
        scout_result = await scout.discover_async(entity, scoring_capture=scoring_capture)
        record = BaselineRecord(scout_result=scout_result, scoring_inputs=scoring_capture)

        ranked = [d.domain for d in scout_result.domains]
        results.append(_entity_result(gt, ranked, k_values))
        learned_results.append(_entity_result(gt, _learned_ranked_domains(record), k_values))

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
        choices=["baseline", "live", "record"],
        default="baseline",
        help=(
            "baseline (pre-recorded JSON), live (real queries), or record "
            "(run live discovery and (re)generate the baselines/ substrate + manifest)"
        ),
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
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="record mode: record only the first N ground-truth entries (smoke-scale substrate)",
    )

    args = parser.parse_args(argv)

    ground_truth = load_ground_truth(args.ground_truth)

    if args.label:
        ground_truth = [gt for gt in ground_truth if gt.label_id == args.label]
        if not ground_truth:
            print(f"ERROR: label_id '{args.label}' not found in ground truth", file=sys.stderr)
            sys.exit(1)

    if args.mode == "record":
        manifest = asyncio.run(record_baselines(ground_truth, args.baselines_dir, limit=args.limit))
        bdir = args.baselines_dir or _BASELINES_DIR
        print(
            f"Wrote {len(manifest.entries)} baseline(s) + {_MANIFEST_NAME} to {bdir}",
            file=sys.stderr,
        )
        return

    try:
        if args.mode == "baseline":
            report = evaluate_baseline(ground_truth, args.baselines_dir)
        else:
            report = asyncio.run(evaluate_live(ground_truth))
    except EvalSubstrateError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.output == "json":
        print(report.model_dump_json(indent=2))
    else:
        print(format_table(report))


if __name__ == "__main__":
    main()
