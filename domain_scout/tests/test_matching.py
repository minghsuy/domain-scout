"""Tests for entity name matching and domain slug generation."""

from __future__ import annotations

from domain_scout.matching.entity_match import (
    domain_from_company_name,
    normalize_org_name,
    org_name_similarity,
)


class TestNormalize:
    def test_strips_inc(self) -> None:
        assert normalize_org_name("Acme Inc.") == "acme"

    def test_strips_llc(self) -> None:
        assert normalize_org_name("Acme Solutions LLC") == "acme solutions"

    def test_strips_ltd(self) -> None:
        assert normalize_org_name("Acme Ltd") == "acme"

    def test_strips_lp_dotted(self) -> None:
        assert normalize_org_name("Acme L.L.C.") == "acme"

    def test_strips_the(self) -> None:
        assert normalize_org_name("The Acme Corporation") == "acme"

    def test_unicode(self) -> None:
        result = normalize_org_name("Ünîcödé Tëst GmbH")
        assert "unicode" in result

    def test_collapses_whitespace(self) -> None:
        assert normalize_org_name("  Acme   Solutions   Inc  ") == "acme solutions"

    def test_empty(self) -> None:
        assert normalize_org_name("") == ""

    def test_only_suffix(self) -> None:
        assert normalize_org_name("Inc.") == ""


class TestSimilarity:
    def test_exact_match(self) -> None:
        assert org_name_similarity("Palo Alto Networks", "Palo Alto Networks") == 1.0

    def test_with_suffix(self) -> None:
        score = org_name_similarity("Palo Alto Networks", "Palo Alto Networks, Inc.")
        assert score >= 0.95

    def test_reordered(self) -> None:
        score = org_name_similarity("Palo Alto Networks", "Networks Palo Alto")
        assert score >= 0.85

    def test_abbreviation(self) -> None:
        score = org_name_similarity("International Business Machines", "IBM")
        # These are very different strings; score should be low
        assert score < 0.5

    def test_completely_different(self) -> None:
        score = org_name_similarity("Google", "Microsoft")
        assert score < 0.35

    def test_partial_overlap(self) -> None:
        score = org_name_similarity("Alphabet Inc", "Alphabet Holdings")
        assert score >= 0.7

    def test_llc_vs_dotted(self) -> None:
        score = org_name_similarity("Acme LLC", "Acme L.L.C.")
        assert score >= 0.95

    def test_empty_string(self) -> None:
        assert org_name_similarity("", "Something") == 0.0

    def test_both_empty(self) -> None:
        assert org_name_similarity("", "") == 0.0

    def test_real_world_panw(self) -> None:
        score = org_name_similarity("Palo Alto Networks", "Palo Alto Networks Inc.")
        assert score >= 0.95

    def test_real_world_guidewire(self) -> None:
        score = org_name_similarity("Guidewire Software", "Guidewire Software, Inc.")
        assert score >= 0.95


class TestDomainFromCompanyName:
    def test_basic(self) -> None:
        slugs = domain_from_company_name("Acme Solutions, Inc.")
        assert "acmesolutions" in slugs
        assert "acme-solutions" in slugs
        assert "acme" in slugs

    def test_single_word(self) -> None:
        slugs = domain_from_company_name("Google")
        assert "google" in slugs

    def test_three_words(self) -> None:
        slugs = domain_from_company_name("United Parcel Service")
        assert "unitedparcelservice" in slugs
        assert "united-parcel-service" in slugs
        assert "united" in slugs
        assert "unitedservice" in slugs

    def test_empty(self) -> None:
        assert domain_from_company_name("") == []

    def test_suffix_only(self) -> None:
        assert domain_from_company_name("Inc.") == []
