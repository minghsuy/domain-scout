from __future__ import annotations

import pytest
from pydantic import ValidationError

from domain_scout.models import DiscoveredDomain, EntityInput


def test_entity_input_seed_domain_max_length() -> None:
    # Valid input with 50 seed domains
    try:
        EntityInput(
            company_name="Valid Company",
            seed_domain=["example.com"] * 50,
        )
    except ValidationError:
        pytest.fail("ValidationError raised unexpectedly for 50 seed domains")

    # Invalid input with 51 seed domains
    with pytest.raises(ValidationError) as exc_info:
        EntityInput(
            company_name="Invalid Company",
            seed_domain=["example.com"] * 51,
        )

    assert "List should have at most 50 items after validation" in str(exc_info.value)


def test_entity_input_company_name_only() -> None:
    """Forward discovery: company_name without seed_domain is valid."""
    entity = EntityInput(company_name="Coalition Inc")
    assert entity.company_name == "Coalition Inc"
    assert entity.seed_domain == []


def test_entity_input_seed_domain_only() -> None:
    """seed_domain alone is valid (reverse-lookup flow)."""
    entity = EntityInput(seed_domain=["coalition.com"])
    assert entity.company_name == ""
    assert entity.seed_domain == ["coalition.com"]


def test_entity_input_both_fields_valid() -> None:
    """Both fields provided is also valid — scout uses both signals."""
    entity = EntityInput(
        company_name="Coalition Inc",
        seed_domain=["coalition.com", "coalitioninc.com"],
    )
    assert entity.company_name == "Coalition Inc"
    assert len(entity.seed_domain) == 2


def test_entity_input_neither_field_rejected() -> None:
    """Empty company_name AND empty seed_domain is the misuse case."""
    with pytest.raises(ValidationError) as exc_info:
        EntityInput()
    assert "either company_name or seed_domain is required" in str(exc_info.value)


def test_entity_input_empty_string_and_empty_list_rejected() -> None:
    """Explicit empty values for both fields hit the same validator."""
    with pytest.raises(ValidationError) as exc_info:
        EntityInput(company_name="", seed_domain=[])
    assert "either company_name or seed_domain is required" in str(exc_info.value)


def test_entity_input_company_name_max_length_still_enforced() -> None:
    """The 200-char cap on company_name is still active."""
    with pytest.raises(ValidationError) as exc_info:
        EntityInput(company_name="x" * 201)
    assert "at most 200" in str(exc_info.value)


# --- DiscoveredDomain scorer identity (issue #184, schema 1.1) ---


def test_discovered_domain_scorer_fields_round_trip() -> None:
    """scorer_id/scorer_version survive JSON serialization."""
    d = DiscoveredDomain(
        domain="example.com",
        confidence=0.62,
        scorer_id="learned_lr",
        scorer_version="v1@2026-03-01",
    )
    payload = d.model_dump_json()
    assert '"scorer_id":"learned_lr"' in payload
    assert '"scorer_version":"v1@2026-03-01"' in payload
    restored = DiscoveredDomain.model_validate_json(payload)
    assert restored.scorer_id == "learned_lr"
    assert restored.scorer_version == "v1@2026-03-01"


def test_discovered_domain_legacy_payload_defaults_to_unknown() -> None:
    """Pre-1.1 results (no scorer fields) must still validate, as 'unknown'."""
    legacy = {"domain": "example.com", "confidence": 0.85}
    d = DiscoveredDomain.model_validate(legacy)
    assert d.scorer_id == "unknown"
    assert d.scorer_version == "unknown"
