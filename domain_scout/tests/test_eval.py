"""Tests for the evaluation harness (domain_scout.eval)."""

from __future__ import annotations

import textwrap
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from domain_scout.eval import (
    EntityEvalResult,
    EvalReport,
    GroundTruthEntry,
    MetricsAtK,
    collect_false_positives,
    compute_metrics,
    evaluate_baseline,
    evaluate_live,
    format_table,
    load_ground_truth,
)
from domain_scout.models import (
    DiscoveredDomain,
    EntityInput,
    EvidenceRecord,
    RunMetadata,
    ScoutResult,
)

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
        baseline_path = tmp_path / "test-entity.json"
        baseline_path.write_text(result.model_dump_json(indent=2))

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

    def test_missing_baseline_skipped(self, tmp_path: Path) -> None:
        """Missing baseline file is skipped with warning."""
        gt = [
            GroundTruthEntry(
                label_id="nonexistent",
                company_name="Ghost",
                seeds=["ghost.com"],
                owned_domains=["ghost.com"],
            )
        ]
        report = evaluate_baseline(gt, baselines_dir=tmp_path)
        assert len(report.entities) == 0

    @pytest.mark.integration
    def test_real_baselines(self) -> None:
        """Evaluate against real baselines if they exist."""
        if not _make_baselines_dir().exists():
            pytest.skip("baselines/ directory not found")

        gt = load_ground_truth()
        report = evaluate_baseline(gt)
        assert len(report.entities) >= 3  # at least the original 3

        # walmart-seed1: all 17 are TPs, no FPs
        ws1 = next(e for e in report.entities if e.label_id == "walmart-seed1")
        assert ws1.false_positive_domains == []

        # walmart-seed2: hubspot.com and exacttarget.com are FPs
        ws2 = next(e for e in report.entities if e.label_id == "walmart-seed2")
        assert set(ws2.false_positive_domains) == {"hubspot.com", "exacttarget.com"}

        # panw: all 26 are TPs
        panw = next(e for e in report.entities if e.label_id == "panw-20260217")
        assert panw.false_positive_domains == []


def _make_baselines_dir() -> Path:
    """Return the real baselines directory path."""
    return Path(__file__).parent.parent.parent / "baselines"


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
            mock_scout.discover_async = AsyncMock(return_value=mock_result)

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

        result = _make_learned_fixture()
        # Recorded ranking: filler.com (0.6) ahead of acme-cert.com (0.2).
        assert [d.domain for d in result.domains] == ["filler.com", "acme-cert.com"]
        # Learned leg: acme-cert.com re-scored high, filler.com keeps 0.6.
        assert _learned_ranked_domains(result) == ["acme-cert.com", "filler.com"]

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
        baseline_path = tmp_path / "acme.json"
        baseline_path.write_text(_make_learned_fixture().model_dump_json())

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
        (tmp_path / "test-entity.json").write_text(result.model_dump_json())

        report = evaluate_baseline(gt, baselines_dir=tmp_path, k_values=(3,))
        assert report.learned_entities[0].metrics == report.entities[0].metrics
        assert (
            report.learned_entities[0].false_positive_domains
            == report.entities[0].false_positive_domains
        )

    def test_no_baselines_no_learned_scorer_identity(self, tmp_path: Path) -> None:
        gt = [
            GroundTruthEntry(
                label_id="missing",
                company_name="Missing Corp",
                seeds=["missing.com"],
                owned_domains=["missing.com"],
            )
        ]
        report = evaluate_baseline(gt, baselines_dir=tmp_path)
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
        (tmp_path / "acme.json").write_text(_make_learned_fixture().model_dump_json())

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
            mock_scout.discover_async = AsyncMock(return_value=_make_learned_fixture())

            report = await evaluate_live(gt, k_values=(1,))

        assert report.mode == "live"
        assert report.entities[0].metrics[0].precision == 0.0
        assert report.learned_entities[0].metrics[0].precision == 1.0
        assert report.learned_scorer == "learned_lr/v1@2026-03-01+uncal"
