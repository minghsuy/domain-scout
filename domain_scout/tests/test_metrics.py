"""Tests for the _metrics module."""

from __future__ import annotations

from typing import Any

from domain_scout._metrics import (
    _CB_STATE_MAP,
    _ENABLED,
    CT_CIRCUIT_BREAKER_STATE,
    CT_FALLBACKS_TOTAL,
    CT_QUERIES_TOTAL,
    DOMAINS_FOUND,
    SCAN_DURATION_SECONDS,
    SCANS_TOTAL,
    SOURCE_ERRORS_TOTAL,
    generate_latest,
    inc,
    observe,
    set_cb_state,
    set_gauge,
)


def _labeled_value(counter: Any, labels: tuple[str, ...]) -> float:
    """Read the current numeric value of a labeled Prometheus counter."""
    child = counter._metrics.get(labels)
    if child is None:
        return 0.0
    return float(child._value.get())


def _unlabeled_value(metric: Any) -> float:
    """Read the current numeric value of an unlabeled Prometheus metric."""
    return float(metric._value.get())


class TestMetricsEnabled:
    """Verify metrics are functional when prometheus-client is installed."""

    def test_enabled(self) -> None:
        assert _ENABLED is True

    def test_all_metrics_defined(self) -> None:
        assert SCANS_TOTAL is not None
        assert SCAN_DURATION_SECONDS is not None
        assert DOMAINS_FOUND is not None
        assert CT_QUERIES_TOTAL is not None
        assert CT_FALLBACKS_TOTAL is not None
        assert CT_CIRCUIT_BREAKER_STATE is not None
        assert SOURCE_ERRORS_TOTAL is not None

    def test_inc_labeled_counter(self) -> None:
        before = _labeled_value(SCANS_TOTAL, ("ok",))
        inc(SCANS_TOTAL, status="ok")
        after = _labeled_value(SCANS_TOTAL, ("ok",))
        assert after == before + 1

    def test_inc_unlabeled_counter(self) -> None:
        before = _unlabeled_value(CT_FALLBACKS_TOTAL)
        inc(CT_FALLBACKS_TOTAL)
        assert _unlabeled_value(CT_FALLBACKS_TOTAL) == before + 1

    def test_observe_histogram(self) -> None:
        observe(SCAN_DURATION_SECONDS, 1.5)
        observe(DOMAINS_FOUND, 10.0)

    def test_set_gauge(self) -> None:
        set_gauge(CT_CIRCUIT_BREAKER_STATE, 42.0)
        assert _unlabeled_value(CT_CIRCUIT_BREAKER_STATE) == 42.0

    def test_set_cb_state(self) -> None:
        for state, expected in _CB_STATE_MAP.items():
            set_cb_state(state)
            assert _unlabeled_value(CT_CIRCUIT_BREAKER_STATE) == expected

    def test_generate_latest(self) -> None:
        output = generate_latest()
        assert isinstance(output, bytes)
        assert b"domain_scout_scans_total" in output


class TestNoOpSafety:
    """Verify that inc/observe/set_gauge silently no-op on None."""

    def test_inc_none(self) -> None:
        inc(None, status="ok")

    def test_observe_none(self) -> None:
        observe(None, 1.0)

    def test_set_gauge_none(self) -> None:
        set_gauge(None, 1.0)
