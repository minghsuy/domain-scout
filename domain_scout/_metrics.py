"""Prometheus metrics for domain-scout. No-ops when prometheus-client is not installed."""

from __future__ import annotations

from typing import Any

__all__ = [
    "CONTENT_TYPE_LATEST",
    "CT_CIRCUIT_BREAKER_STATE",
    "CT_FALLBACKS_TOTAL",
    "CT_QUERIES_TOTAL",
    "DOMAINS_FOUND",
    "SCAN_DURATION_SECONDS",
    "SCANS_TOTAL",
    "SOURCE_ERRORS_TOTAL",
    "generate_latest",
    "inc",
    "observe",
    "set_cb_state",
    "set_gauge",
]

try:
    from prometheus_client import CONTENT_TYPE_LATEST as _CONTENT_TYPE_LATEST
    from prometheus_client import Counter, Gauge, Histogram
    from prometheus_client import generate_latest as _generate_latest

    _ENABLED = True
except ImportError:  # pragma: no cover
    _ENABLED = False

CONTENT_TYPE_LATEST: str = _CONTENT_TYPE_LATEST if _ENABLED else ""

SCANS_TOTAL: Counter | None = None
SCAN_DURATION_SECONDS: Histogram | None = None
DOMAINS_FOUND: Histogram | None = None
CT_QUERIES_TOTAL: Counter | None = None
CT_FALLBACKS_TOTAL: Counter | None = None
CT_CIRCUIT_BREAKER_STATE: Gauge | None = None
SOURCE_ERRORS_TOTAL: Counter | None = None

if _ENABLED:
    SCANS_TOTAL = Counter(
        "domain_scout_scans_total",
        "Total scans executed",
        ["status"],
    )
    SCAN_DURATION_SECONDS = Histogram(
        "domain_scout_scan_duration_seconds",
        "Scan duration in seconds",
        buckets=(1, 5, 10, 30, 60, 120, 300),
    )
    DOMAINS_FOUND = Histogram(
        "domain_scout_domains_found",
        "Number of domains found per scan",
        buckets=(0, 1, 5, 10, 25, 50, 100, 250),
    )
    CT_QUERIES_TOTAL = Counter(
        "domain_scout_ct_queries_total",
        "Total CT log queries",
        ["backend", "status"],
    )
    CT_FALLBACKS_TOTAL = Counter(
        "domain_scout_ct_fallbacks_total",
        "Total CT JSON API fallbacks",
    )
    CT_CIRCUIT_BREAKER_STATE = Gauge(
        "domain_scout_ct_circuit_breaker_state",
        "Circuit breaker state (0=closed, 1=open, 2=half_open)",
    )
    SOURCE_ERRORS_TOTAL = Counter(
        "domain_scout_source_errors_total",
        "Total source errors by source type",
        ["source"],
    )

_CB_STATE_MAP = {"closed": 0, "open": 1, "half_open": 2}


def generate_latest() -> bytes:
    """Return Prometheus metrics in text exposition format. Empty bytes if disabled."""
    if not _ENABLED:
        return b""  # pragma: no cover
    return _generate_latest()


def inc(counter: Any, amount: float = 1, **labels: str) -> None:
    """Increment a counter with optional labels. No-op if counter is None."""
    if counter is None:
        return
    if labels:
        counter.labels(**labels).inc(amount)
    else:
        counter.inc(amount)


def observe(histogram: Any, value: float) -> None:
    """Observe a value on a histogram. No-op if histogram is None."""
    if histogram is not None:
        histogram.observe(value)


def set_gauge(gauge: Any, value: float) -> None:
    """Set a gauge value. No-op if gauge is None."""
    if gauge is not None:
        gauge.set(value)


def set_cb_state(state: str) -> None:
    """Update the circuit breaker state gauge."""
    set_gauge(CT_CIRCUIT_BREAKER_STATE, _CB_STATE_MAP.get(state, -1))
