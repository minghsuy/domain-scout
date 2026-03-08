"""Unit tests for internal scout functions."""

from domain_scout.scout import _extract_sans


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
