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


def test_scorer_identity_constants() -> None:
    """The learned scorer exposes a stable id and an artifact-derived version."""
    from domain_scout.scorer import SCORER_ID, _get_model, scorer_version

    assert SCORER_ID == "learned_lr"
    model = _get_model()
    assert scorer_version() == f"{model['version']}@{model['training_date']}"
    # Pin the shipped artifact so a silent retrain can't reuse an old identity.
    assert scorer_version() == "v1@2026-03-01"
