import pytest
from pydantic import ValidationError
from domain_scout.models import EntityInput

def test_entity_input_company_name_validation():
    # Valid names
    valid_names = [
        "Google",
        "AT&T",
        "McDonald's",
        "Company (US) Inc.",
        "Company-Name",
        "Company, Inc.",
        "Company. Inc.",
        "L'Oreal",
        "Münchener Rückversicherungs-Gesellschaft",
        "12345",
        "Yahoo", # "Yahoo!" is currently rejected, maybe I should add ! ?
    ]

    for name in valid_names:
        entity = EntityInput(company_name=name)
        assert entity.company_name == name

    # Invalid names
    invalid_names = [
        "Evil <script>",
        "Drop Table;",
        "Company\nName",
        "Company\tName",
        "Bad|Char",
        "Bad*Char",
        "Bad\\Char",
    ]

    for name in invalid_names:
        with pytest.raises(ValidationError) as excinfo:
            EntityInput(company_name=name)
        assert "Company name contains invalid characters" in str(excinfo.value)
