"""Tests for the evaluation harness (domain_scout.eval)."""

from __future__ import annotations

import hashlib
import textwrap
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest

from domain_scout.config import ScoutConfig
from domain_scout.eval import (
    BaselineManifest,
    BaselineManifestEntry,
    BaselineRecord,
    EntityEvalResult,
    EvalReport,
    EvalSubstrateError,
    GroundTruthEntry,
    MetricsAtK,
    collect_false_positives,
    compute_metrics,
    evaluate_baseline,
    evaluate_live,
    format_table,
    load_ground_truth,
    record_baselines,
)
from domain_scout.models import (
    DiscoveredDomain,
    EntityInput,
    EvidenceRecord,
    RunMetadata,
    ScoringInputs,
    ScoutResult,
)
from domain_scout.scout import Scout

if TYPE_CHECKING:
    from collections.abc import Callable

    import httpx

# ---------------------------------------------------------------------------
# compute_metrics tests
# ---------------------------------------------------------------------------


class TestComputeMetrics:
    """Test the pure metric computation function."""

    def test_perfect_precision_and_recall(self) -> None:
        """All top-k domains are owned and all owned are in top-k."""
        ranked = ["a.com", "b.com", "c.com", "d.com", "e.com"]
        owned = {"a.com", "b.com", "c.com", "d.com", "e.com"}
        results = compute_metrics(ranked, owned, set(), k_values=(5,))
        assert len(results) == 1
        m = results[0]
        assert m.k == 5
        assert m.hits == 5
        assert m.precision == 1.0
        assert m.recall == 1.0
        assert m.ndcg == 1.0
        assert m.false_positives == 0

    def test_zero_precision(self) -> None:
        """No owned domains in top-k."""
        ranked = ["x.com", "y.com", "z.com"]
        owned = {"a.com", "b.com"}
        results = compute_metrics(ranked, owned, set(), k_values=(3,))
        m = results[0]
        assert m.precision == 0.0
        assert m.recall == 0.0
        assert m.false_positives == 0

    def test_partial_precision_and_recall(self) -> None:
        """Some owned in top-k, some not."""
        ranked = ["a.com", "x.com", "b.com", "y.com", "c.com"]
        owned = {"a.com", "b.com", "c.com", "d.com"}
        results = compute_metrics(ranked, owned, set(), k_values=(3, 5))

        # k=3: top-3 = [a, x, b] -> hits=2, precision=2/3, recall=2/4
        m3 = results[0]
        assert m3.k == 3
        assert m3.precision == pytest.approx(0.667, abs=0.001)
        assert m3.recall == 0.5

        # k=5: top-5 = [a, x, b, y, c] -> hits=3, precision=3/5, recall=3/4
        m5 = results[1]
        assert m5.k == 5
        assert m5.precision == 0.6
        assert m5.recall == 0.75

    def test_false_positives_counted(self) -> None:
        """Explicit not_owned domains show up in FP count."""
        ranked = ["a.com", "bad.com", "b.com"]
        owned = {"a.com", "b.com"}
        not_owned = {"bad.com"}
        results = compute_metrics(ranked, owned, not_owned, k_values=(3,))
        assert results[0].false_positives == 1

    def test_k_larger_than_results(self) -> None:
        """When k > number of ranked domains, precision uses adaptive denominator."""
        ranked = ["a.com", "b.com"]
        owned = {"a.com", "b.com", "c.com"}
        results = compute_metrics(ranked, owned, set(), k_values=(5,))
        m = results[0]
        # precision = 2/min(5,2) = 2/2 = 1.0 (adaptive: don't penalize small result sets)
        assert m.precision == 1.0
        # recall = 2/3
        assert m.recall == pytest.approx(0.667, abs=0.001)

    def test_empty_ranked_list(self) -> None:
        """No results at all."""
        results = compute_metrics([], {"a.com"}, set(), k_values=(5,))
        m = results[0]
        assert m.precision == 0.0
        assert m.recall == 0.0
        assert m.ndcg == 0.0
        assert m.false_positives == 0

    def test_empty_owned_set(self) -> None:
        """No owned domains (edge case)."""
        results = compute_metrics(["a.com", "b.com"], set(), set(), k_values=(2,))
        m = results[0]
        assert m.precision == 0.0
        assert m.recall == 0.0
        assert m.ndcg == 0.0

    def test_multiple_k_values(self) -> None:
        """Multiple k values produce correct number of results."""
        ranked = ["a.com", "b.com", "c.com"]
        owned = {"a.com", "b.com", "c.com"}
        results = compute_metrics(ranked, owned, set(), k_values=(1, 2, 3))
        assert len(results) == 3
        assert [m.k for m in results] == [1, 2, 3]

    def test_ndcg_rewards_top_ranking(self) -> None:
        """NDCG should be higher when relevant docs appear earlier."""
        owned = {"a.com", "b.com"}

        # Good ranking: relevant at positions 1, 2
        good = compute_metrics(["a.com", "b.com", "x.com"], owned, set(), k_values=(3,))
        # Bad ranking: relevant at positions 2, 3
        bad = compute_metrics(["x.com", "a.com", "b.com"], owned, set(), k_values=(3,))

        assert good[0].ndcg > bad[0].ndcg

    def test_ndcg_perfect_is_one(self) -> None:
        """Perfect ranking should give NDCG=1.0."""
        ranked = ["a.com", "b.com", "c.com"]
        owned = {"a.com", "b.com", "c.com"}
        results = compute_metrics(ranked, owned, set(), k_values=(3,))
        assert results[0].ndcg == 1.0


# ---------------------------------------------------------------------------
# collect_false_positives tests
# ---------------------------------------------------------------------------


class TestCollectFalsePositives:
    def test_finds_fps(self) -> None:
        ranked = ["a.com", "bad1.com", "b.com", "bad2.com"]
        fps = collect_false_positives(ranked, {"bad1.com", "bad2.com"})
        assert fps == ["bad1.com", "bad2.com"]

    def test_no_fps(self) -> None:
        assert collect_false_positives(["a.com", "b.com"], {"bad.com"}) == []


# ---------------------------------------------------------------------------
# load_ground_truth tests
# ---------------------------------------------------------------------------


class TestLoadGroundTruth:
    def test_loads_builtin(self) -> None:
        """Built-in ground truth file loads and validates."""
        entries = load_ground_truth()
        assert len(entries) >= 3
        ids = {e.label_id for e in entries}
        assert "walmart-seed1" in ids
        assert "walmart-seed2" in ids
        assert "panw-20260217" in ids

    def test_validates_entries(self) -> None:
        entries = load_ground_truth()
        for e in entries:
            assert e.company_name
            assert e.owned_domains

    def test_custom_path(self, tmp_path: Path) -> None:
        """Loading from a custom YAML path works."""
        yaml_content = textwrap.dedent("""\
        - label_id: test-1
          company_name: "Test Corp"
          seeds: ["test.com"]
          owned_domains: ["test.com", "test.net"]
          not_owned: ["bad.com"]
        """)
        gt_file = tmp_path / "gt.yaml"
        gt_file.write_text(yaml_content)
        entries = load_ground_truth(gt_file)
        assert len(entries) == 1
        assert entries[0].label_id == "test-1"
        assert entries[0].not_owned == ["bad.com"]


# ---------------------------------------------------------------------------
# evaluate_baseline tests
# ---------------------------------------------------------------------------


def _make_scout_result(
    domains: list[str],
    company_name: str = "Test",
    seeds: list[str] | None = None,
) -> ScoutResult:
    """Helper to create a minimal ScoutResult for testing."""
    return ScoutResult(
        entity=EntityInput(
            company_name=company_name,
            seed_domain=seeds or [],
        ),
        domains=[
            DiscoveredDomain(
                domain=d,
                confidence=round(0.95 - i * 0.02, 2),
            )
            for i, d in enumerate(domains)
        ],
        run_metadata=RunMetadata(
            tool_version="0.4.0",
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            elapsed_seconds=1.0,
            domains_found=len(domains),
        ),
    )


def _capture_for(result: ScoutResult) -> dict[str, ScoringInputs]:
    """Score-time inputs equal to the post-pipeline state.

    Valid for synthetic fixtures where nothing was infra-boosted or deduped, so
    score-time state and persisted state coincide by construction.
    """
    capture: dict[str, ScoringInputs] = {}
    for d in result.domains:
        cert_ids = {ev.cert_id for ev in d.evidence if ev.cert_id is not None}
        rdap_similarity = max(
            (
                ev.similarity_score
                for ev in d.evidence
                if ev.source_type == "rdap_registrant_match" and ev.similarity_score is not None
            ),
            default=0.0,
        )
        capture[d.domain] = ScoringInputs(
            sources=list(d.sources),
            cert_org_names=list(d.cert_org_names),
            resolves=d.resolves,
            evidence_count=len(d.evidence),
            unique_cert_count=len(cert_ids),
            rdap_similarity=rdap_similarity,
        )
    return capture


def _record_for(result: ScoutResult) -> BaselineRecord:
    """Wrap a fixture ScoutResult into a schema-2 BaselineRecord."""
    return BaselineRecord(scout_result=result, scoring_inputs=_capture_for(result))


def _mock_discover(results_fn: Callable[[EntityInput], ScoutResult]) -> Callable[..., Any]:
    """Side effect for a mocked discover_async that fills scoring_capture.

    Mirrors the real pipeline contract: when the caller passes a capture dict,
    every returned domain gets a score-time ScoringInputs entry.
    """

    async def _side_effect(
        entity: EntityInput, *, scoring_capture: dict[str, ScoringInputs] | None = None
    ) -> ScoutResult:
        result = results_fn(entity)
        if scoring_capture is not None:
            scoring_capture.update(_capture_for(result))
        return result

    return _side_effect


class TestEvaluateBaseline:
    def test_with_temp_fixtures(self, tmp_path: Path) -> None:
        """Evaluate baseline from temp directory with synthetic data."""
        # Create ground truth
        gt = [
            GroundTruthEntry(
                label_id="test-entity",
                company_name="TestCo",
                seeds=["test.com"],
                owned_domains=["test.com", "test.net", "test.org"],
                not_owned=["evil.com"],
            )
        ]

        # Create baseline JSON
        result = _make_scout_result(
            ["test.com", "test.net", "evil.com", "test.org", "unknown.com"],
            company_name="TestCo",
            seeds=["test.com"],
        )
        _write_baseline_with_manifest(tmp_path, {"test-entity": result})

        report = evaluate_baseline(gt, baselines_dir=tmp_path, k_values=(3, 5))
        assert report.mode == "baseline"
        assert len(report.entities) == 1

        entity = report.entities[0]
        assert entity.label_id == "test-entity"
        assert entity.discovered_count == 5
        assert entity.owned_count == 3

        # k=3: [test.com, test.net, evil.com] -> 2 owned, 1 FP
        m3 = entity.metrics[0]
        assert m3.k == 3
        assert m3.precision == pytest.approx(0.667, abs=0.001)
        assert m3.false_positives == 1

        # k=5: [all 5] -> 3 owned
        m5 = entity.metrics[1]
        assert m5.k == 5
        assert m5.precision == 0.6
        assert m5.recall == 1.0

        assert "evil.com" in entity.false_positive_domains

    def test_empty_substrate_no_manifest_fails_loudly(self, tmp_path: Path) -> None:
        """A directory with no manifest and no baseline files is a loud error (#188)."""
        gt = [
            GroundTruthEntry(
                label_id="nonexistent",
                company_name="Ghost",
                seeds=["ghost.com"],
                owned_domains=["ghost.com"],
            )
        ]
        with pytest.raises(EvalSubstrateError, match="No baseline manifest"):
            evaluate_baseline(gt, baselines_dir=tmp_path)

    def test_loose_files_without_manifest_fail_loudly(self, tmp_path: Path) -> None:
        """Baseline files present but no manifest is a loud error (interrupted record).

        The manifest is proof of a completed `record` run; without it a partial
        set of {label_id}.json files must not read as a passing partial report.
        """
        gt = [
            GroundTruthEntry(
                label_id="test-entity",
                company_name="TestCo",
                seeds=["test.com"],
                owned_domains=["test.com"],
            )
        ]
        # A loose baseline file, but no manifest.json alongside it.
        (tmp_path / "test-entity.json").write_text(
            _make_scout_result(["test.com"], company_name="TestCo").model_dump_json()
        )
        with pytest.raises(EvalSubstrateError, match="No baseline manifest"):
            evaluate_baseline(gt, baselines_dir=tmp_path)

    def test_unparseable_manifest_fails_loudly(self, tmp_path: Path) -> None:
        """A present-but-truncated manifest is a loud error, not a fall-through."""
        gt = [
            GroundTruthEntry(
                label_id="test-entity",
                company_name="TestCo",
                seeds=["test.com"],
                owned_domains=["test.com"],
            )
        ]
        (tmp_path / "manifest.json").write_text('{"entries": [')  # truncated JSON
        with pytest.raises(EvalSubstrateError, match="unparseable"):
            evaluate_baseline(gt, baselines_dir=tmp_path)

    def test_manifest_evaluates_only_listed_files(self, tmp_path: Path) -> None:
        """With a manifest present, exactly the listed (and ground-truthed) files run."""
        gt = [
            GroundTruthEntry(
                label_id="test-entity",
                company_name="TestCo",
                seeds=["test.com"],
                owned_domains=["test.com", "test.net"],
            ),
            GroundTruthEntry(
                label_id="unrecorded",
                company_name="Other",
                seeds=["other.com"],
                owned_domains=["other.com"],
            ),
        ]
        result = _make_scout_result(["test.com", "test.net"], company_name="TestCo")
        _write_baseline_with_manifest(tmp_path, {"test-entity": result})

        report = evaluate_baseline(gt, baselines_dir=tmp_path, k_values=(2,))
        # Only the manifest-listed label is evaluated; "unrecorded" is not in the
        # substrate and is silently absent (not an error — it just wasn't recorded).
        assert [e.label_id for e in report.entities] == ["test-entity"]

    def test_manifest_missing_file_fails_loudly(self, tmp_path: Path) -> None:
        """A manifest that references an absent file is a loud error (#188, gate 3)."""
        gt = [
            GroundTruthEntry(
                label_id="test-entity",
                company_name="TestCo",
                seeds=["test.com"],
                owned_domains=["test.com"],
            )
        ]
        result = _make_scout_result(["test.com"], company_name="TestCo")
        _write_baseline_with_manifest(tmp_path, {"test-entity": result})
        # Delete the file the manifest still references.
        (tmp_path / "test-entity.json").unlink()

        with pytest.raises(EvalSubstrateError, match="absent"):
            evaluate_baseline(gt, baselines_dir=tmp_path)

    def test_manifest_corrupt_file_fails_loudly(self, tmp_path: Path) -> None:
        """A manifest whose file digest no longer matches is a loud error (#188, gate 3)."""
        gt = [
            GroundTruthEntry(
                label_id="test-entity",
                company_name="TestCo",
                seeds=["test.com"],
                owned_domains=["test.com"],
            )
        ]
        result = _make_scout_result(["test.com"], company_name="TestCo")
        _write_baseline_with_manifest(tmp_path, {"test-entity": result})
        # Mutate the file so its sha256 diverges from the manifest.
        (tmp_path / "test-entity.json").write_text('{"tampered": true}')

        with pytest.raises(EvalSubstrateError, match="corrupt"):
            evaluate_baseline(gt, baselines_dir=tmp_path)

    def test_label_scoped_run_ignores_out_of_scope_corruption(self, tmp_path: Path) -> None:
        """--label debugging isn't blocked by an unrelated entry's absent/corrupt file.

        Validation is scoped to the requested ground truth: a single-label run
        over a substrate with unrelated damage succeeds, while the unscoped run
        over the same substrate still fails loudly.
        """

        def _gt(label_id: str) -> GroundTruthEntry:
            return GroundTruthEntry(
                label_id=label_id,
                company_name=label_id.title(),
                seeds=[f"{label_id}.com"],
                owned_domains=[f"{label_id}.com"],
            )

        _write_baseline_with_manifest(
            tmp_path,
            {
                "alpha": _make_scout_result(["alpha.com"], company_name="Alpha"),
                "beta": _make_scout_result(["beta.com"], company_name="Beta"),
                "gamma": _make_scout_result(["gamma.com"], company_name="Gamma"),
            },
        )
        # Damage the two entries that are NOT in scope: one absent, one tampered.
        (tmp_path / "beta.json").unlink()
        (tmp_path / "gamma.json").write_text('{"tampered": true}')

        # Scoped run (mirrors main()'s --label filtering): succeeds on alpha alone.
        report = evaluate_baseline([_gt("alpha")], baselines_dir=tmp_path, k_values=(1,))
        assert [e.label_id for e in report.entities] == ["alpha"]

        # Unscoped run over the same substrate: the damage is in scope -> loud.
        with pytest.raises(EvalSubstrateError, match="1 absent and 1 corrupt"):
            evaluate_baseline([_gt("alpha"), _gt("beta"), _gt("gamma")], baselines_dir=tmp_path)

    def test_old_schema_substrate_fails_loudly(self, tmp_path: Path) -> None:
        """A pre-#187 substrate (schema 1, no score-time inputs) is refused loudly.

        The learned leg must never silently fall back to approximating from
        post-pipeline state — an old substrate gets an explicit re-record
        message instead.
        """
        gt = [
            GroundTruthEntry(
                label_id="test-entity",
                company_name="TestCo",
                seeds=["test.com"],
                owned_domains=["test.com"],
            )
        ]
        result = _make_scout_result(["test.com"], company_name="TestCo")
        _write_baseline_with_manifest(tmp_path, {"test-entity": result}, substrate_schema=1)

        with pytest.raises(EvalSubstrateError, match="substrate schema 1.*Re-record"):
            evaluate_baseline(gt, baselines_dir=tmp_path)

    def test_missing_scoring_inputs_fails_loudly(self, tmp_path: Path) -> None:
        """A schema-2 record lacking a domain's score-time inputs is a loud error."""
        gt = [
            GroundTruthEntry(
                label_id="test-entity",
                company_name="TestCo",
                seeds=["test.com"],
                owned_domains=["test.com", "test.net"],
            )
        ]
        result = _make_scout_result(["test.com", "test.net"], company_name="TestCo")
        record = _record_for(result)
        del record.scoring_inputs["test.net"]
        _write_baseline_with_manifest(tmp_path, {"test-entity": record})

        with pytest.raises(EvalSubstrateError, match="no score-time scoring inputs"):
            evaluate_baseline(gt, baselines_dir=tmp_path)

    def test_manifest_no_matching_ground_truth_fails_loudly(self, tmp_path: Path) -> None:
        """A manifest with no entry matching the ground truth is a loud error."""
        gt = [
            GroundTruthEntry(
                label_id="other",
                company_name="Other",
                seeds=["other.com"],
                owned_domains=["other.com"],
            )
        ]
        result = _make_scout_result(["test.com"], company_name="TestCo")
        _write_baseline_with_manifest(tmp_path, {"test-entity": result})

        with pytest.raises(EvalSubstrateError, match="no entries matching"):
            evaluate_baseline(gt, baselines_dir=tmp_path)

    def test_manifest_scorer_drift_warns(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A substrate recorded under a different scorer emits a visible warning."""
        gt = [
            GroundTruthEntry(
                label_id="test-entity",
                company_name="TestCo",
                seeds=["test.com"],
                owned_domains=["test.com"],
            )
        ]
        result = _make_scout_result(["test.com"], company_name="TestCo")
        _write_baseline_with_manifest(
            tmp_path, {"test-entity": result}, scorer="stale/v0@2000-01-01"
        )

        evaluate_baseline(gt, baselines_dir=tmp_path, k_values=(1,))
        assert "current scorer is" in capsys.readouterr().err

    @pytest.mark.integration
    def test_real_baselines(self) -> None:
        """Evaluate against the real locally-generated substrate if it exists.

        The concrete baselines/ substrate is git-ignored and regenerated by
        ``make eval-baselines`` (issue #188), so this asserts the manifest
        contract holds rather than hardcoding domains from a since-decayed
        2026-02-24 snapshot.
        """
        bdir = _make_baselines_dir()
        if not (bdir / "manifest.json").exists():
            pytest.skip("baselines/manifest.json not found (run `make eval-baselines`)")

        gt = load_ground_truth()
        report = evaluate_baseline(gt)
        # Every evaluated entity corresponds to a ground-truth label.
        gt_ids = {g.label_id for g in gt}
        assert report.entities  # non-empty: a missing substrate would have raised
        assert all(e.label_id in gt_ids for e in report.entities)


def _make_baselines_dir() -> Path:
    """Return the real baselines directory path."""
    return Path(__file__).parent.parent.parent / "baselines"


def _write_baseline_with_manifest(
    baselines_dir: Path,
    results: dict[str, ScoutResult | BaselineRecord],
    scorer: str | None = None,
    substrate_schema: int = 2,
) -> BaselineManifest:
    """Write baseline JSON files plus a matching manifest into ``baselines_dir``.

    Plain ScoutResult fixtures are wrapped into schema-2 BaselineRecords with
    score-time inputs equal to their post-pipeline state (valid for synthetic
    fixtures — nothing was boosted or deduped). Defaults the manifest scorer to
    the current identity so metric tests don't emit spurious scorer-drift
    warnings; the drift test passes an explicit stale value. The schema-gate
    test passes an explicit old ``substrate_schema``.
    """
    if scorer is None:
        from domain_scout.eval import _learned_scorer_identity

        scorer = _learned_scorer_identity()
    entries: list[BaselineManifestEntry] = []
    for label_id, result in results.items():
        record = result if isinstance(result, BaselineRecord) else _record_for(result)
        fname = f"{label_id}.json"
        fpath = baselines_dir / fname
        fpath.write_text(record.model_dump_json(indent=2))
        entries.append(
            BaselineManifestEntry(
                label_id=label_id,
                file=fname,
                sha256=hashlib.sha256(fpath.read_bytes()).hexdigest(),
                domains=len(record.scout_result.domains),
            )
        )
    manifest = BaselineManifest(
        generated_at="2026-02-24T00:00:00+00:00",
        tool_version="0.0.0-test",
        scorer=scorer,
        source="test fixture",
        substrate_schema=substrate_schema,
        entries=entries,
    )
    (baselines_dir / "manifest.json").write_text(manifest.model_dump_json(indent=2))
    return manifest


# ---------------------------------------------------------------------------
# evaluate_live tests
# ---------------------------------------------------------------------------


class TestEvaluateLive:
    @pytest.mark.asyncio
    async def test_with_mock_scout(self) -> None:
        """evaluate_live runs Scout.discover_async and computes metrics."""
        gt = [
            GroundTruthEntry(
                label_id="mock-entity",
                company_name="MockCo",
                seeds=["mock.com"],
                owned_domains=["mock.com", "mock.net"],
                not_owned=["evil.com"],
            )
        ]

        mock_result = _make_scout_result(
            ["mock.com", "evil.com", "mock.net"],
            company_name="MockCo",
            seeds=["mock.com"],
        )

        with patch("domain_scout.scout.Scout") as mock_scout_cls:
            mock_scout = mock_scout_cls.return_value
            mock_scout.discover_async = AsyncMock(side_effect=_mock_discover(lambda _: mock_result))

            report = await evaluate_live(gt, k_values=(3,))

        assert report.mode == "live"
        assert len(report.entities) == 1
        entity = report.entities[0]
        assert entity.label_id == "mock-entity"
        assert entity.discovered_count == 3
        assert entity.metrics[0].precision == pytest.approx(0.667, abs=0.001)
        assert entity.metrics[0].false_positives == 1
        assert "evil.com" in entity.false_positive_domains


# ---------------------------------------------------------------------------
# record_baselines (substrate generation) tests
# ---------------------------------------------------------------------------


class TestRecordBaselines:
    @pytest.mark.asyncio
    async def test_writes_files_and_manifest(self, tmp_path: Path) -> None:
        """record_baselines persists one file per entity and a matching manifest."""
        gt = [
            GroundTruthEntry(
                label_id="alpha",
                company_name="Alpha",
                seeds=["alpha.com"],
                owned_domains=["alpha.com"],
            ),
            GroundTruthEntry(
                label_id="beta",
                company_name="Beta",
                seeds=["beta.com"],
                owned_domains=["beta.com"],
            ),
        ]
        results = {
            "Alpha": _make_scout_result(["alpha.com", "alpha.net"], company_name="Alpha"),
            "Beta": _make_scout_result(["beta.com"], company_name="Beta"),
        }

        with patch("domain_scout.scout.Scout") as mock_scout_cls:
            mock_scout = mock_scout_cls.return_value
            mock_scout.discover_async = AsyncMock(
                side_effect=_mock_discover(lambda entity: results[entity.company_name])
            )
            manifest = await record_baselines(gt, baselines_dir=tmp_path)

        # Files written per entity.
        assert (tmp_path / "alpha.json").exists()
        assert (tmp_path / "beta.json").exists()
        # Manifest references exactly those files, sorted by label_id, with digests
        # that match the bytes on disk.
        assert [e.label_id for e in manifest.entries] == ["alpha", "beta"]
        assert manifest.scorer  # scorer identity stamped
        assert manifest.git_commit is None or isinstance(manifest.git_commit, str)
        assert (
            manifest.model_dump()
            == BaselineManifest.model_validate_json(
                (tmp_path / "manifest.json").read_text()
            ).model_dump()
        )

        # The written substrate round-trips through evaluate_baseline (loud path).
        report = evaluate_baseline(gt, baselines_dir=tmp_path, k_values=(1,))
        assert {e.label_id for e in report.entities} == {"alpha", "beta"}

    @pytest.mark.asyncio
    async def test_limit_records_smoke_subset(self, tmp_path: Path) -> None:
        """--limit records only the first N entities; the manifest lists only those."""
        gt = [
            GroundTruthEntry(
                label_id=f"e{i}",
                company_name=f"Co{i}",
                seeds=[f"co{i}.com"],
                owned_domains=[f"co{i}.com"],
            )
            for i in range(5)
        ]
        with patch("domain_scout.scout.Scout") as mock_scout_cls:
            mock_scout = mock_scout_cls.return_value
            mock_scout.discover_async = AsyncMock(
                side_effect=_mock_discover(
                    lambda entity: _make_scout_result(
                        [f"{entity.company_name.lower()}.com"], company_name=entity.company_name
                    )
                )
            )
            manifest = await record_baselines(gt, baselines_dir=tmp_path, limit=2)

        assert [e.label_id for e in manifest.entries] == ["e0", "e1"]
        assert not (tmp_path / "e2.json").exists()

    @pytest.mark.asyncio
    async def test_rerun_manifest_references_only_current_run(self, tmp_path: Path) -> None:
        """A narrower re-run overwrites the manifest to reference only its outputs."""
        gt = [
            GroundTruthEntry(
                label_id=f"e{i}",
                company_name=f"Co{i}",
                seeds=[f"co{i}.com"],
                owned_domains=[f"co{i}.com"],
            )
            for i in range(3)
        ]
        with patch("domain_scout.scout.Scout") as mock_scout_cls:
            mock_scout = mock_scout_cls.return_value
            mock_scout.discover_async = AsyncMock(
                side_effect=_mock_discover(
                    lambda entity: _make_scout_result(
                        [f"{entity.company_name.lower()}.com"], company_name=entity.company_name
                    )
                )
            )
            await record_baselines(gt, baselines_dir=tmp_path)  # records e0,e1,e2
            manifest = await record_baselines(gt, baselines_dir=tmp_path, limit=1)  # e0 only

        assert [e.label_id for e in manifest.entries] == ["e0"]


# ---------------------------------------------------------------------------
# format_table tests
# ---------------------------------------------------------------------------


class TestFormatTable:
    def test_produces_output(self) -> None:
        report = EvalReport(
            mode="baseline",
            entities=[
                EntityEvalResult(
                    label_id="test",
                    company_name="Test",
                    seeds=["test.com"],
                    discovered_count=5,
                    owned_count=3,
                    not_owned_count=1,
                    metrics=[
                        MetricsAtK(
                            k=5,
                            hits=3,
                            precision=0.8,
                            recall=1.0,
                            ndcg=0.95,
                            false_positives=1,
                        )
                    ],
                    false_positive_domains=["bad.com"],
                )
            ],
        )
        table = format_table(report)
        assert "baseline" in table
        assert "test" in table
        assert "0.800" in table
        assert "3/3" in table
        assert "FP domains (all ranks): bad.com" in table

    def test_empty_report(self) -> None:
        report = EvalReport(mode="baseline", entities=[])
        table = format_table(report)
        assert "baseline" in table


# ---------------------------------------------------------------------------
# EvalReport JSON round-trip
# ---------------------------------------------------------------------------


class TestEvalReportJson:
    def test_json_round_trip(self) -> None:
        report = EvalReport(
            mode="test",
            entities=[
                EntityEvalResult(
                    label_id="rt",
                    company_name="RT Corp",
                    seeds=["rt.com"],
                    discovered_count=2,
                    owned_count=2,
                    not_owned_count=0,
                    metrics=[
                        MetricsAtK(
                            k=5,
                            hits=2,
                            precision=1.0,
                            recall=1.0,
                            ndcg=1.0,
                            false_positives=0,
                        )
                    ],
                )
            ],
        )
        json_str = report.model_dump_json()
        restored = EvalReport.model_validate_json(json_str)
        assert restored.mode == "test"
        assert len(restored.entities) == 1
        assert restored.entities[0].label_id == "rt"
        # Pre-#183 reports (no learned leg) still validate.
        assert restored.learned_entities == []
        assert restored.learned_scorer is None


# ---------------------------------------------------------------------------
# Learned-scorer leg (issue #183: eval must exercise both scorer paths)
# ---------------------------------------------------------------------------


def _make_learned_fixture() -> ScoutResult:
    """ScoutResult where the learned scorer disagrees with the recorded ranking.

    - filler.com: no cert evidence -> learned leg keeps recorded 0.6
      (mirrors Scout's fallback when cert_org_names is empty).
    - acme-cert.com: recorded 0.2, but cert org == company + resolves, so the
      learned model scores it high (~0.98) and it should re-rank to the top.
    """
    return ScoutResult(
        entity=EntityInput(company_name="Acme Corp", seed_domain=["acme.com"]),
        domains=[
            DiscoveredDomain(domain="filler.com", confidence=0.6),
            DiscoveredDomain(
                domain="acme-cert.com",
                confidence=0.2,
                sources=["ct_org_match"],
                cert_org_names=["Acme Corp"],
                resolves=True,
                evidence=[
                    EvidenceRecord(
                        source_type="ct_org_match",
                        description="cert org match",
                        cert_id=1,
                        cert_org="Acme Corp",
                        similarity_score=0.98,
                    )
                ],
            ),
        ],
        run_metadata=RunMetadata(
            tool_version="0.4.0",
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            elapsed_seconds=1.0,
            domains_found=2,
        ),
    )


class TestLearnedScorerLeg:
    def test_learned_leg_reranks_by_learned_confidence(self) -> None:
        from domain_scout.eval import _learned_ranked_domains

        record = _record_for(_make_learned_fixture())
        # Recorded ranking: filler.com (0.6) ahead of acme-cert.com (0.2).
        assert [d.domain for d in record.scout_result.domains] == ["filler.com", "acme-cert.com"]
        # Learned leg: acme-cert.com re-scored high, filler.com keeps 0.6.
        assert _learned_ranked_domains(record) == ["acme-cert.com", "filler.com"]

    def test_baseline_runs_both_scorer_legs(self, tmp_path: Path) -> None:
        gt = [
            GroundTruthEntry(
                label_id="acme",
                company_name="Acme Corp",
                seeds=["acme.com"],
                owned_domains=["acme-cert.com"],
                not_owned=["filler.com"],
            )
        ]
        _write_baseline_with_manifest(tmp_path, {"acme": _make_learned_fixture()})

        report = evaluate_baseline(gt, baselines_dir=tmp_path, k_values=(1,))

        assert len(report.entities) == 1
        assert len(report.learned_entities) == 1
        # #185 identity of the path that actually ran: raw LR (calibration
        # gated off by the artifact's own metrics), hence the +uncal suffix.
        assert report.learned_scorer == "learned_lr/v1@2026-03-01+uncal"

        # Heuristic leg: recorded ranking puts the labeled FP at rank 1.
        heuristic = report.entities[0].metrics[0]
        assert heuristic.precision == 0.0
        assert heuristic.false_positives == 1

        # Learned leg: re-ranked, the owned domain is at rank 1.
        learned = report.learned_entities[0].metrics[0]
        assert learned.precision == 1.0
        assert learned.false_positives == 0

    def test_learned_leg_without_cert_orgs_matches_recorded(self, tmp_path: Path) -> None:
        """No cert evidence anywhere -> learned leg falls back to recorded ranking."""
        gt = [
            GroundTruthEntry(
                label_id="test-entity",
                company_name="TestCo",
                seeds=["test.com"],
                owned_domains=["test.com", "test.net"],
                not_owned=["evil.com"],
            )
        ]
        result = _make_scout_result(
            ["test.com", "evil.com", "test.net"],
            company_name="TestCo",
            seeds=["test.com"],
        )
        _write_baseline_with_manifest(tmp_path, {"test-entity": result})

        report = evaluate_baseline(gt, baselines_dir=tmp_path, k_values=(3,))
        assert report.learned_entities[0].metrics == report.entities[0].metrics
        assert (
            report.learned_entities[0].false_positive_domains
            == report.entities[0].false_positive_domains
        )

    def test_no_learned_scorer_identity_on_empty_report(self) -> None:
        """An empty EvalReport carries no learned-scorer identity."""
        # Constructed directly: evaluate_baseline now raises on an empty substrate
        # rather than returning an empty report (see
        # TestEvaluateBaseline.test_empty_substrate_no_manifest_fails_loudly).
        report = EvalReport(mode="baseline", entities=[])
        assert report.learned_entities == []
        assert report.learned_scorer is None

    def test_format_table_includes_learned_section(self, tmp_path: Path) -> None:
        gt = [
            GroundTruthEntry(
                label_id="acme",
                company_name="Acme Corp",
                seeds=["acme.com"],
                owned_domains=["acme-cert.com"],
                not_owned=["filler.com"],
            )
        ]
        _write_baseline_with_manifest(tmp_path, {"acme": _make_learned_fixture()})

        report = evaluate_baseline(gt, baselines_dir=tmp_path, k_values=(1,))
        table = format_table(report)
        assert "Learned scorer leg (learned_lr/v1@2026-03-01+uncal)" in table
        # Both legs render the entity block.
        assert table.count("acme (Acme Corp, seeds=acme.com)") == 2

    @pytest.mark.asyncio
    async def test_live_runs_both_scorer_legs(self) -> None:
        gt = [
            GroundTruthEntry(
                label_id="acme",
                company_name="Acme Corp",
                seeds=["acme.com"],
                owned_domains=["acme-cert.com"],
                not_owned=["filler.com"],
            )
        ]
        with patch("domain_scout.scout.Scout") as mock_scout_cls:
            mock_scout = mock_scout_cls.return_value
            mock_scout.discover_async = AsyncMock(
                side_effect=_mock_discover(lambda _: _make_learned_fixture())
            )

            report = await evaluate_live(gt, k_values=(1,))

        assert report.mode == "live"
        assert report.entities[0].metrics[0].precision == 0.0
        assert report.learned_entities[0].metrics[0].precision == 1.0
        assert report.learned_scorer == "learned_lr/v1@2026-03-01+uncal"


# ---------------------------------------------------------------------------
# Learned-leg parity: exact replay of the production learned path (issue #187)
# ---------------------------------------------------------------------------

# Fixture designed so the pipeline mutates post-scoring state in every way #187
# names: acme-cloud.com appears in three certs with the same org (dedup will
# collapse its evidence), and shares infrastructure with the seed (_infra_boost
# will add the shared_infra tag + 0.05 after scoring).
_PARITY_CT_DOMAIN: dict[str, list[dict[str, object]]] = {
    "acme.com": [
        {
            "cert_id": 11,
            "common_name": "acme.com",
            "subject": "O=Acme Corp",
            "org_name": "Acme Corp",
            "not_before": "2024-01-01T00:00:00",
            "not_after": "2025-01-01T00:00:00",
            "san_dns_names": ["acme.com", "www.acme.com", "acme-cloud.com"],
        },
        {
            "cert_id": 12,
            "common_name": "acme.com",
            "subject": "O=Acme Corp",
            "org_name": "Acme Corp",
            "not_before": "2024-06-01T00:00:00",
            "not_after": "2025-06-01T00:00:00",
            "san_dns_names": ["acme.com", "acme-cloud.com"],
        },
    ],
}

_PARITY_CT_ORG: list[dict[str, object]] = [
    {
        "cert_id": 13,
        "common_name": "acme.net",
        "subject": "O=Acme Corp",
        "org_name": "Acme Corp",
        "not_before": "2024-02-01T00:00:00",
        "not_after": "2025-02-01T00:00:00",
        "san_dns_names": ["acme.net", "acme-cloud.com"],
    },
]

_PARITY_DNS: dict[str, bool] = {
    "acme.com": True,
    "www.acme.com": True,
    "acme-cloud.com": True,
    "acme.net": True,
}


def _make_parity_scout(*, use_learned_scorer: bool) -> Scout:
    """Full pipeline Scout with deterministic mocked sources (no network)."""
    config = ScoutConfig(
        use_learned_scorer=use_learned_scorer,
        # Include every scored domain so the two runs' output sets match by
        # construction (learned probabilities need not clear the default 0.6).
        inclusion_threshold=0.0,
    )
    scout = Scout(config=config)

    async def ct_search_by_domain(
        domain: str, client: httpx.AsyncClient | None = None
    ) -> list[dict[str, object]]:
        return _PARITY_CT_DOMAIN.get(domain, [])

    async def ct_search_by_org(
        org_name: str, *, verify_org: bool = True, client: httpx.AsyncClient | None = None
    ) -> list[dict[str, object]]:
        return list(_PARITY_CT_ORG)

    scout._ct.search_by_domain = AsyncMock(side_effect=ct_search_by_domain)  # type: ignore[method-assign]
    scout._ct.search_by_org = AsyncMock(side_effect=ct_search_by_org)  # type: ignore[method-assign]
    scout._ct.get_cert_org = AsyncMock(return_value="Acme Corp")  # type: ignore[method-assign]

    scout._rdap.get_registrant_org = AsyncMock(return_value="Acme Corp")  # type: ignore[method-assign]
    scout._rdap.get_registrant_info = AsyncMock(  # type: ignore[method-assign]
        return_value={"org": "Acme Corp", "name": None, "country": "US"}
    )

    async def dns_resolves(domain: str) -> bool:
        return _PARITY_DNS.get(domain, False)

    async def dns_bulk_resolve(domains: list[str]) -> dict[str, bool]:
        return {d: _PARITY_DNS.get(d, False) for d in domains}

    async def dns_shares_infrastructure(ref: str, other: str) -> bool:
        # Deterministic and scorer-independent: only acme-cloud.com shares
        # infrastructure with the reference, in both pipeline runs.
        return other == "acme-cloud.com"

    scout._dns.resolves = AsyncMock(side_effect=dns_resolves)  # type: ignore[method-assign]
    scout._dns.bulk_resolve = AsyncMock(side_effect=dns_bulk_resolve)  # type: ignore[method-assign]
    scout._dns.shares_infrastructure = AsyncMock(side_effect=dns_shares_infrastructure)  # type: ignore[method-assign]

    return scout


class TestLearnedLegParity:
    """The eval learned leg scores the SAME inputs production scores (#187)."""

    @pytest.mark.asyncio
    async def test_replay_matches_production_learned_run(self) -> None:
        """Exact parity: replayed confidences == a use_learned_scorer=True run.

        Run A is production with the learned scorer on. Run B is the recorded
        (heuristic) run with score-time capture — what --mode record persists.
        The eval's learned leg replayed from Run B must reproduce Run A's
        confidences exactly, including for the infra-boosted domain, while the
        pre-#187 approximation (re-scoring Run B's post-pipeline state)
        provably cannot.
        """
        from domain_scout.eval import _learned_scored_domains

        entity = EntityInput(company_name="Acme Corp", seed_domain=["acme.com"])

        # Run A: production learned run.
        result_prod = await _make_parity_scout(use_learned_scorer=True).discover_async(entity)
        prod_conf = {d.domain: d.confidence for d in result_prod.domains}

        # Run B: recorded heuristic run with score-time capture (--mode record).
        capture: dict[str, ScoringInputs] = {}
        result_rec = await _make_parity_scout(use_learned_scorer=False).discover_async(
            entity, scoring_capture=capture
        )
        record = BaselineRecord(scout_result=result_rec, scoring_inputs=capture)

        # Same domains came out of both runs (inclusion_threshold=0.0).
        assert {d.domain for d in result_rec.domains} == set(prod_conf)

        # The fixture actually exercises the #187 mutations: the boost fired...
        boosted = next(d for d in result_rec.domains if d.domain == "acme-cloud.com")
        assert "shared_infra" in boosted.sources
        snapshot = capture["acme-cloud.com"]
        assert snapshot.infra_boosted is True
        assert "shared_infra" not in snapshot.sources  # score-time sources are pre-boost
        # ...and dedup shrank the persisted evidence below the score-time count
        # (the +1 post-scoring shared_infra evidence record notwithstanding).
        assert snapshot.evidence_count > len(boosted.evidence)

        # Exact parity, every domain, including the boosted one.
        replayed = {domain: conf for conf, domain in _learned_scored_domains(record)}
        assert replayed == prod_conf

        # The pre-#187 approximation (post-pipeline state, no boost re-applied)
        # does NOT reproduce production for the boosted domain — the defect this
        # substrate schema exists to close.
        approx_record = BaselineRecord(
            scout_result=result_rec, scoring_inputs=_capture_for(result_rec)
        )
        approx = {domain: conf for conf, domain in _learned_scored_domains(approx_record)}
        assert approx["acme-cloud.com"] != prod_conf["acme-cloud.com"]

    @pytest.mark.asyncio
    async def test_record_replay_roundtrip_through_files(self, tmp_path: Path) -> None:
        """record_baselines -> evaluate_baseline replays exactly through disk."""
        from domain_scout.eval import _learned_scored_domains

        gt = [
            GroundTruthEntry(
                label_id="acme",
                company_name="Acme Corp",
                seeds=["acme.com"],
                owned_domains=["acme.com", "acme.net", "acme-cloud.com"],
            )
        ]
        scout = _make_parity_scout(use_learned_scorer=False)
        with patch("domain_scout.scout.Scout", return_value=scout):
            manifest = await record_baselines(gt, baselines_dir=tmp_path)

        assert manifest.substrate_schema == 2
        # The persisted record round-trips: score-time inputs survive disk, and
        # the boosted domain's snapshot still replays the boost.
        loaded = BaselineRecord.model_validate_json((tmp_path / "acme.json").read_text())
        assert loaded.scoring_inputs["acme-cloud.com"].infra_boosted is True

        report = evaluate_baseline(gt, baselines_dir=tmp_path, k_values=(3,))
        assert report.learned_entities  # learned leg ran from the loaded record

        # Parity holds through the disk roundtrip: replaying the loaded record
        # reproduces a live use_learned_scorer=True run exactly.
        entity = EntityInput(company_name="Acme Corp", seed_domain=["acme.com"])
        result_prod = await _make_parity_scout(use_learned_scorer=True).discover_async(entity)
        prod_conf = {d.domain: d.confidence for d in result_prod.domains}
        replayed = {domain: conf for conf, domain in _learned_scored_domains(loaded)}
        assert replayed == prod_conf
