"""Tests for delta reporting (compute_delta, CLI diff, API /diff)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from domain_scout.cli import app
from domain_scout.delta import _CONFIDENCE_EPSILON, compute_delta
from domain_scout.models import (
    DeltaReport,
    DeltaSummary,
    DiscoveredDomain,
    EntityInput,
    RunMetadata,
    ScoutResult,
)

if TYPE_CHECKING:
    from pathlib import Path

# --- Test helpers ---

_NOW = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)


def _make_domain(
    domain: str = "example.com",
    confidence: float = 0.85,
    resolves: bool = True,
    sources: list[str] | None = None,
    rdap_org: str | None = None,
) -> DiscoveredDomain:
    return DiscoveredDomain(
        domain=domain,
        confidence=confidence,
        resolves=resolves,
        sources=sources or ["ct_san"],
        rdap_org=rdap_org,
    )


def _make_result(
    domains: list[DiscoveredDomain] | None = None,
    company_name: str = "TestCorp",
    seeds: list[str] | None = None,
    timed_out: bool = False,
    config: dict[str, object] | None = None,
    schema_version: str = "1.0",
    warnings: list[str] | None = None,
) -> ScoutResult:
    return ScoutResult(
        entity=EntityInput(
            company_name=company_name,
            seed_domain=seeds or ["test.com"],
        ),
        domains=domains or [],
        run_metadata=RunMetadata(
            schema_version=schema_version,
            tool_version="0.3.1",
            timestamp=_NOW,
            elapsed_seconds=5.0,
            domains_found=len(domains) if domains else 0,
            timed_out=timed_out,
            warnings=warnings or [],
            config=config or {"total_timeout": 120},
        ),
    )


# --- TestComputeDelta ---


class TestComputeDelta:
    def test_identical_results_no_changes(self) -> None:
        d = _make_domain()
        result = compute_delta(_make_result([d]), _make_result([d]))
        assert result.summary.added == 0
        assert result.summary.removed == 0
        assert result.summary.changed == 0
        assert result.summary.unchanged == 1

    def test_added_domain(self) -> None:
        baseline = _make_result([])
        current = _make_result([_make_domain("new.com")])
        result = compute_delta(baseline, current)
        assert result.summary.added == 1
        assert result.added[0].domain == "new.com"

    def test_removed_domain(self) -> None:
        baseline = _make_result([_make_domain("old.com")])
        current = _make_result([])
        result = compute_delta(baseline, current)
        assert result.summary.removed == 1
        assert result.removed[0].domain == "old.com"

    def test_confidence_above_epsilon(self) -> None:
        old = _make_domain(confidence=0.80)
        new = _make_domain(confidence=0.90)
        result = compute_delta(_make_result([old]), _make_result([new]))
        assert result.summary.changed == 1
        assert result.changed[0].changes[0].field == "confidence"

    def test_confidence_below_epsilon(self) -> None:
        old = _make_domain(confidence=0.85)
        new = _make_domain(confidence=0.85 + _CONFIDENCE_EPSILON - 0.001)
        result = compute_delta(_make_result([old]), _make_result([new]))
        assert result.summary.changed == 0
        assert result.summary.unchanged == 1

    def test_confidence_at_epsilon(self) -> None:
        old = _make_domain(confidence=0.80)
        new = _make_domain(confidence=0.80 + _CONFIDENCE_EPSILON)
        result = compute_delta(_make_result([old]), _make_result([new]))
        assert result.summary.changed == 1

    def test_resolves_flip_true_to_false(self) -> None:
        old = _make_domain(resolves=True)
        new = _make_domain(resolves=False)
        result = compute_delta(_make_result([old]), _make_result([new]))
        changes = result.changed[0].changes
        assert any(c.field == "resolves" and c.old is True and c.new is False for c in changes)

    def test_resolves_flip_false_to_true(self) -> None:
        old = _make_domain(resolves=False)
        new = _make_domain(resolves=True)
        result = compute_delta(_make_result([old]), _make_result([new]))
        changes = result.changed[0].changes
        assert any(c.field == "resolves" for c in changes)

    def test_sources_changed(self) -> None:
        old = _make_domain(sources=["ct_san"])
        new = _make_domain(sources=["ct_san", "ct_org_match"])
        result = compute_delta(_make_result([old]), _make_result([new]))
        changes = result.changed[0].changes
        assert any(c.field == "sources" for c in changes)

    def test_sources_same_different_order(self) -> None:
        old = _make_domain(sources=["ct_org_match", "ct_san"])
        new = _make_domain(sources=["ct_san", "ct_org_match"])
        result = compute_delta(_make_result([old]), _make_result([new]))
        assert result.summary.unchanged == 1

    def test_rdap_org_none_to_value(self) -> None:
        old = _make_domain(rdap_org=None)
        new = _make_domain(rdap_org="TestCorp Inc.")
        result = compute_delta(_make_result([old]), _make_result([new]))
        changes = result.changed[0].changes
        assert any(c.field == "rdap_org" and c.old is None for c in changes)

    def test_rdap_org_value_to_none(self) -> None:
        old = _make_domain(rdap_org="TestCorp Inc.")
        new = _make_domain(rdap_org=None)
        result = compute_delta(_make_result([old]), _make_result([new]))
        changes = result.changed[0].changes
        assert any(c.field == "rdap_org" and c.new is None for c in changes)

    def test_rdap_org_value_to_different_value(self) -> None:
        old = _make_domain(rdap_org="OldCorp")
        new = _make_domain(rdap_org="NewCorp")
        result = compute_delta(_make_result([old]), _make_result([new]))
        changes = result.changed[0].changes
        assert any(c.field == "rdap_org" for c in changes)

    def test_multiple_changes_same_domain(self) -> None:
        old = _make_domain(confidence=0.80, resolves=True, rdap_org=None)
        new = _make_domain(confidence=0.90, resolves=False, rdap_org="NewCorp")
        result = compute_delta(_make_result([old]), _make_result([new]))
        assert len(result.changed[0].changes) == 3

    def test_empty_baseline_empty_current(self) -> None:
        result = compute_delta(_make_result([]), _make_result([]))
        assert result.summary == DeltaSummary(
            added=0,
            removed=0,
            changed=0,
            unchanged=0,
            baseline_total=0,
            current_total=0,
        )

    def test_empty_baseline(self) -> None:
        domains = [_make_domain("a.com"), _make_domain("b.com")]
        result = compute_delta(_make_result([]), _make_result(domains))
        assert result.summary.added == 2

    def test_empty_current(self) -> None:
        domains = [_make_domain("a.com"), _make_domain("b.com")]
        result = compute_delta(_make_result(domains), _make_result([]))
        assert result.summary.removed == 2

    def test_summary_counts(self) -> None:
        baseline = _make_result(
            [
                _make_domain("kept.com", confidence=0.85),
                _make_domain("changed.com", confidence=0.70),
                _make_domain("removed.com"),
            ]
        )
        current = _make_result(
            [
                _make_domain("kept.com", confidence=0.85),
                _make_domain("changed.com", confidence=0.90),
                _make_domain("added.com"),
            ]
        )
        result = compute_delta(baseline, current)
        assert result.summary.added == 1
        assert result.summary.removed == 1
        assert result.summary.changed == 1
        assert result.summary.unchanged == 1
        assert result.summary.baseline_total == 3
        assert result.summary.current_total == 3

    def test_alphabetical_ordering(self) -> None:
        baseline = _make_result([_make_domain("z.com"), _make_domain("a.com")])
        current = _make_result([_make_domain("m.com"), _make_domain("b.com")])
        result = compute_delta(baseline, current)
        assert [d.domain for d in result.added] == ["b.com", "m.com"]
        assert [d.domain for d in result.removed] == ["a.com", "z.com"]


# --- TestDeltaWarnings ---


class TestDeltaWarnings:
    def test_no_warnings_identical_context(self) -> None:
        result = compute_delta(_make_result([]), _make_result([]))
        assert result.warnings == []

    def test_company_name_changed(self) -> None:
        result = compute_delta(
            _make_result(company_name="OldCorp"),
            _make_result(company_name="NewCorp"),
        )
        codes = [w.code for w in result.warnings]
        assert "company_name_changed" in codes

    def test_seeds_changed(self) -> None:
        result = compute_delta(
            _make_result(seeds=["a.com"]),
            _make_result(seeds=["b.com"]),
        )
        codes = [w.code for w in result.warnings]
        assert "seeds_changed" in codes

    def test_seeds_same_different_order_no_warning(self) -> None:
        result = compute_delta(
            _make_result(seeds=["b.com", "a.com"]),
            _make_result(seeds=["a.com", "b.com"]),
        )
        codes = [w.code for w in result.warnings]
        assert "seeds_changed" not in codes

    def test_config_changed(self) -> None:
        result = compute_delta(
            _make_result(config={"total_timeout": 120}),
            _make_result(config={"total_timeout": 180}),
        )
        codes = [w.code for w in result.warnings]
        assert "config_changed" in codes
        msg = next(w.message for w in result.warnings if w.code == "config_changed")
        assert "total_timeout" in msg

    def test_baseline_timed_out(self) -> None:
        result = compute_delta(
            _make_result(timed_out=True),
            _make_result(timed_out=False),
        )
        codes = [w.code for w in result.warnings]
        assert "baseline_timed_out" in codes
        assert "current_timed_out" not in codes

    def test_current_timed_out(self) -> None:
        result = compute_delta(
            _make_result(timed_out=False),
            _make_result(timed_out=True),
        )
        codes = [w.code for w in result.warnings]
        assert "current_timed_out" in codes
        assert "baseline_timed_out" not in codes

    def test_schema_version_mismatch(self) -> None:
        result = compute_delta(
            _make_result(schema_version="1.0"),
            _make_result(schema_version="2.0"),
        )
        codes = [w.code for w in result.warnings]
        assert "schema_version_mismatch" in codes

    def test_ct_fallback_asymmetry_baseline_only(self) -> None:
        fb_warn = [
            "CT Postgres unavailable, used JSON fallback for 2 queries (org-name matching degraded)"
        ]
        result = compute_delta(
            _make_result(warnings=fb_warn),
            _make_result(),
        )
        codes = [w.code for w in result.warnings]
        assert "ct_fallback_asymmetry" in codes

    def test_ct_fallback_asymmetry_current_only(self) -> None:
        fb_warn = [
            "CT Postgres unavailable, used JSON fallback for 1 query (org-name matching degraded)"
        ]
        result = compute_delta(
            _make_result(),
            _make_result(warnings=fb_warn),
        )
        codes = [w.code for w in result.warnings]
        assert "ct_fallback_asymmetry" in codes

    def test_ct_fallback_asymmetry_both_no_warning(self) -> None:
        fb_warn = [
            "CT Postgres unavailable, used JSON fallback for 1 query (org-name matching degraded)"
        ]
        result = compute_delta(
            _make_result(warnings=fb_warn),
            _make_result(warnings=fb_warn),
        )
        codes = [w.code for w in result.warnings]
        assert "ct_fallback_asymmetry" not in codes

    def test_ct_fallback_asymmetry_neither_no_warning(self) -> None:
        result = compute_delta(_make_result(), _make_result())
        codes = [w.code for w in result.warnings]
        assert "ct_fallback_asymmetry" not in codes


# --- TestDeltaSerialization ---


class TestDeltaSerialization:
    def test_json_roundtrip(self) -> None:
        baseline = _make_result(
            [
                _make_domain("kept.com", confidence=0.85),
                _make_domain("removed.com"),
            ]
        )
        current = _make_result(
            [
                _make_domain("kept.com", confidence=0.90),
                _make_domain("added.com"),
            ]
        )
        report = compute_delta(baseline, current)
        json_str = report.model_dump_json()
        restored = DeltaReport.model_validate_json(json_str)
        assert restored.summary == report.summary
        assert len(restored.added) == len(report.added)
        assert len(restored.removed) == len(report.removed)
        assert len(restored.changed) == len(report.changed)


# --- TestDiffCLI ---


runner = CliRunner()


class TestDiffCLI:
    def test_json_output(self, tmp_path: Path) -> None:
        baseline = _make_result([_make_domain("a.com")])
        current = _make_result([_make_domain("a.com"), _make_domain("b.com")])
        (tmp_path / "baseline.json").write_text(baseline.model_dump_json())
        (tmp_path / "current.json").write_text(current.model_dump_json())

        result = runner.invoke(
            app,
            ["diff", str(tmp_path / "baseline.json"), str(tmp_path / "current.json"), "-o", "json"],
        )
        assert result.exit_code == 0
        report = DeltaReport.model_validate_json(result.output)
        assert report.summary.added == 1

    def test_table_output(self, tmp_path: Path) -> None:
        baseline = _make_result([_make_domain("a.com")])
        current = _make_result([_make_domain("b.com")])
        (tmp_path / "baseline.json").write_text(baseline.model_dump_json())
        (tmp_path / "current.json").write_text(current.model_dump_json())

        result = runner.invoke(
            app,
            ["diff", str(tmp_path / "baseline.json"), str(tmp_path / "current.json")],
        )
        assert result.exit_code == 0

    def test_missing_file(self, tmp_path: Path) -> None:
        (tmp_path / "current.json").write_text(_make_result([]).model_dump_json())
        result = runner.invoke(
            app,
            ["diff", str(tmp_path / "missing.json"), str(tmp_path / "current.json")],
        )
        assert result.exit_code == 1

    def test_invalid_json(self, tmp_path: Path) -> None:
        (tmp_path / "baseline.json").write_text("not valid json")
        (tmp_path / "current.json").write_text(_make_result([]).model_dump_json())
        result = runner.invoke(
            app,
            ["diff", str(tmp_path / "baseline.json"), str(tmp_path / "current.json")],
        )
        assert result.exit_code == 1


# --- TestDiffAPI ---


class TestDiffAPI:
    @pytest.fixture()
    def client(self) -> TestClient:
        from domain_scout.api import create_app

        return TestClient(create_app(cache=None))

    def test_diff_valid(self, client: TestClient) -> None:
        baseline = _make_result([_make_domain("a.com")])
        current = _make_result([_make_domain("a.com"), _make_domain("b.com")])
        body = {
            "baseline": baseline.model_dump(mode="json"),
            "current": current.model_dump(mode="json"),
        }
        resp = client.post("/diff", json=body)
        assert resp.status_code == 200
        report = DeltaReport.model_validate(resp.json())
        assert report.summary.added == 1

    def test_diff_invalid_body(self, client: TestClient) -> None:
        resp = client.post("/diff", json={"baseline": "not a result"})
        assert resp.status_code == 422
