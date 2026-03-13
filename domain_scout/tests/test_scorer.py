from __future__ import annotations

import pytest

from domain_scout.scorer import (
    _clean_name,
    _domain_has_company_token,
    _entity_name_in_org,
    _tld_is_country,
)


@pytest.mark.parametrize(
    "domain, expected",
    [
        ("example.uk", 1),
        ("example.de", 1),
        ("example.jp", 1),
        ("example.us", 1),
        ("sub.example.co.uk", 1),
        ("example.com", 0),
        ("example.net", 0),
        ("example.org", 0),
        ("example.gov", 0),
        ("example.edu", 0),
        ("example.io", 1),
        ("example.ai", 1),
        ("example.UK", 1),
        ("EXAMPLE.DE", 1),
    ],
)
def test_tld_is_country_happy_paths(domain: str, expected: int) -> None:
    assert _tld_is_country(domain) == expected


def test_tld_is_country_no_dots() -> None:
    assert _tld_is_country("uk") == 1
    assert _tld_is_country("com") == 0


def test_tld_is_country_empty_string() -> None:
    assert _tld_is_country("") == 0


def test_tld_is_country_trailing_dot() -> None:
    assert _tld_is_country("example.uk.") == 0


@pytest.mark.parametrize(
    "domain, company_name, expected",
    [
        ("acme-services.com", "Acme Corp", 1),
        ("something-else.com", "Acme Corp", 0),
        ("acme.com", "The Acme Company Inc.", 1),
        ("abc.com", "A B C", 0),
    ],
)
def test_domain_has_company_token(domain: str, company_name: str, expected: int) -> None:
    assert _domain_has_company_token(domain, company_name) == expected


@pytest.mark.parametrize(
    "name, expected",
    [
        ("The Acme Company Inc.", "acme"),
        ("Palo Alto Networks", "palo alto networks"),
    ],
)
def test_clean_name(name: str, expected: str) -> None:
    assert _clean_name(name) == expected


@pytest.mark.parametrize(
    "company_name, cert_org_names, expected",
    [
        ("Acme Corp", {"Acme Services", "Other Corp"}, 1),
        ("Acme", {"Acme"}, 0),
        ("abc", {"abc corp"}, 0),
    ],
)
def test_entity_name_in_org(company_name: str, cert_org_names: set[str], expected: int) -> None:
    assert _entity_name_in_org(company_name, cert_org_names) == expected
