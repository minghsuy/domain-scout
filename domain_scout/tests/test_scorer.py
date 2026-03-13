"""Unit tests for the scorer module."""

from __future__ import annotations

import pytest

from domain_scout.scorer import _clean_name


@pytest.mark.parametrize(
    ("input_name", "expected"),
    [
        ("Apple Inc", "apple"),
        ("Microsoft Corp.", "microsoft"),
        ("Stripe, Inc.", "stripe"),
        ("A B C Data", "data"),
        ("TeStinG LLC", "testing"),
        ("   ", ""),
        ("Data    Systems", "data systems"),
        ("The Company Inc", ""),
        ("Foo Ltd, Bar LLC", "foo bar"),
        ("a", ""),
        ("ab", ""),
    ],
)
def test_clean_name(input_name: str, expected: str) -> None:
    assert _clean_name(input_name) == expected
