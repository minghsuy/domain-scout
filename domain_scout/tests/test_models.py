import pytest
from pydantic import ValidationError

from domain_scout.models import EntityInput


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
    """Reverse lookup: seed_domain without company_name is valid.

    Previously rejected by ``min_length=1`` on company_name. Now accepted
    so that 'what org owns this domain?' queries don't need a brand-name
    hint up front.
    """
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
