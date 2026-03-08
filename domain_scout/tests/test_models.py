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
