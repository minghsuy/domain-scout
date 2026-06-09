"""Unit tests for internal helper functions in scout.py."""

from __future__ import annotations

from datetime import datetime

from domain_scout.scout import _DomainAccum, _extract_sans, _normalize_time, _parse_time


def test_extract_sans_missing_key() -> None:
    rec: dict[str, object] = {}
    assert _extract_sans(rec) == []


def test_extract_sans_none_value() -> None:
    rec: dict[str, object] = {"san_dns_names": None}
    assert _extract_sans(rec) == []


def test_extract_sans_string_value() -> None:
    rec: dict[str, object] = {"san_dns_names": "example.com"}
    assert _extract_sans(rec) == []


def test_extract_sans_list_of_strings() -> None:
    rec: dict[str, object] = {"san_dns_names": ["example.com", "test.com"]}
    assert _extract_sans(rec) == ["example.com", "test.com"]


def test_extract_sans_empty_list() -> None:
    rec: dict[str, object] = {"san_dns_names": []}
    assert _extract_sans(rec) == []


def test_domain_accum_update_times_none() -> None:
    accum = _DomainAccum()
    accum.update_times(None, None)
    assert accum.earliest_cert is None
    assert accum.latest_cert is None


def test_domain_accum_update_times_initial() -> None:
    accum = _DomainAccum()
    nb = "2023-01-01T00:00:00"
    na = "2023-12-31T23:59:59"
    accum.update_times(nb, na)
    assert accum.earliest_cert == _parse_time(_normalize_time(nb))
    assert accum.latest_cert == _parse_time(_normalize_time(na))


def test_domain_accum_update_times_earlier_earliest_cert() -> None:
    accum = _DomainAccum()
    accum.earliest_cert = _parse_time(_normalize_time("2023-06-01T00:00:00"))
    accum.latest_cert = _parse_time(_normalize_time("2023-12-31T23:59:59"))

    accum.update_times("2023-01-01T00:00:00", "2023-12-31T23:59:59")
    assert accum.earliest_cert == _parse_time(_normalize_time("2023-01-01T00:00:00"))
    assert accum.latest_cert == _parse_time(_normalize_time("2023-12-31T23:59:59"))


def test_domain_accum_update_times_later_earliest_cert() -> None:
    accum = _DomainAccum()
    accum.earliest_cert = _parse_time(_normalize_time("2023-06-01T00:00:00"))
    accum.latest_cert = _parse_time(_normalize_time("2023-12-31T23:59:59"))

    accum.update_times("2023-08-01T00:00:00", "2023-12-31T23:59:59")
    assert accum.earliest_cert == _parse_time(_normalize_time("2023-06-01T00:00:00"))
    assert accum.latest_cert == _parse_time(_normalize_time("2023-12-31T23:59:59"))


def test_domain_accum_update_times_later_latest_cert() -> None:
    accum = _DomainAccum()
    accum.earliest_cert = _parse_time(_normalize_time("2023-01-01T00:00:00"))
    accum.latest_cert = _parse_time(_normalize_time("2023-06-01T00:00:00"))

    accum.update_times("2023-01-01T00:00:00", "2023-12-31T23:59:59")
    assert accum.earliest_cert == _parse_time(_normalize_time("2023-01-01T00:00:00"))
    assert accum.latest_cert == _parse_time(_normalize_time("2023-12-31T23:59:59"))


def test_domain_accum_update_times_earlier_latest_cert() -> None:
    accum = _DomainAccum()
    accum.earliest_cert = _parse_time(_normalize_time("2023-01-01T00:00:00"))
    accum.latest_cert = _parse_time(_normalize_time("2023-06-01T00:00:00"))

    accum.update_times("2023-01-01T00:00:00", "2023-03-01T00:00:00")
    assert accum.earliest_cert == _parse_time(_normalize_time("2023-01-01T00:00:00"))
    assert accum.latest_cert == _parse_time(_normalize_time("2023-06-01T00:00:00"))


def test_domain_accum_update_times_datetime_input() -> None:
    accum = _DomainAccum()
    nb = datetime(2023, 1, 1)
    na = datetime(2023, 12, 31, 23, 59, 59)
    accum.update_times(nb, na)
    assert accum.earliest_cert == _parse_time(_normalize_time(nb))
    assert accum.latest_cert == _parse_time(_normalize_time(na))


def test_domain_accum_update_times_empty_string_input() -> None:
    accum = _DomainAccum()
    accum.earliest_cert = _parse_time(_normalize_time("2023-06-01T00:00:00"))
    accum.latest_cert = _parse_time(_normalize_time("2023-06-01T00:00:00"))

    accum.update_times("", "")
    assert accum.earliest_cert == _parse_time(_normalize_time("2023-06-01T00:00:00"))
    assert accum.latest_cert == _parse_time(_normalize_time("2023-06-01T00:00:00"))


def test_scout_close_releases_local_source_and_gleif() -> None:
    """Scout.close() closes the local CT source and GLEIF connection (issue #164)."""
    from unittest.mock import MagicMock

    from domain_scout.scout import Scout
    from domain_scout.sources.local_parquet import LocalParquetSource

    s = Scout()
    s._ct = MagicMock(spec=LocalParquetSource)
    gleif = MagicMock()
    s._gleif_con = gleif

    s.close()
    s._ct.close.assert_called_once()
    gleif.close.assert_called_once()

    # Second close is safe and doesn't re-close GLEIF
    s.close()
    gleif.close.assert_called_once()


def test_scout_close_noop_for_remote_only() -> None:
    """Remote-only Scout holds no persistent resources; close() is a no-op."""
    from domain_scout.scout import Scout

    s = Scout()
    s.close()
    s.close()
