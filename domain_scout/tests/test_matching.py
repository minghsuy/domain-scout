"""Tests for entity name matching and domain slug generation."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from hypothesis import given
from hypothesis import strategies as st

if TYPE_CHECKING:
    from collections.abc import Callable

from domain_scout.matching.entity_match import (
    _BRAND_ALIAS_PAIRS,
    _extract_initials_from_word,
    domain_from_company_name,
    normalize_org_name,
    org_name_similarity,
    strict_org_name_match,
)

# Best-effort import of the insurance-market-db reference (features.entity_name_match)
# for the differential-parity guard below. domain-scout is public/MIT and MUST NOT
# hard-depend on that private repo — the import is optional and the differential
# test skips cleanly when the reference is absent (CI, other machines). Set
# DOMAIN_SCOUT_REF_FEATURES to the reference `scripts/` dir to override the default.
_reference_entity_name_match: Callable[[str, str], bool] | None = None
try:  # pragma: no cover - environment-dependent
    import os as _os
    import sys as _sys
    from pathlib import Path as _Path

    _ref_scripts = _Path(
        _os.environ.get(
            "DOMAIN_SCOUT_REF_FEATURES", _Path.home() / "insurance-market-db" / "scripts"
        )
    )
    if _ref_scripts.is_dir():
        _sys.path.insert(0, str(_ref_scripts))
        from shared.features import entity_name_match as _entity_name_match

        _reference_entity_name_match = _entity_name_match
except Exception:  # pragma: no cover - reference optional
    _reference_entity_name_match = None


class TestNormalize:
    def test_strips_inc(self) -> None:
        assert normalize_org_name("Acme Inc.") == "acme"

    def test_inc_not_stripped_from_words(self) -> None:
        """Inc suffix must not be stripped from words like Income, Inclusive."""
        assert normalize_org_name("Realty Income Corporation") == "realty income"
        assert normalize_org_name("Inclusive Design") == "inclusive design"
        assert normalize_org_name("Incannex Healthcare Inc.") == "incannex healthcare"

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

    # --- European/Asian legal suffixes ---

    def test_strips_se(self) -> None:
        assert normalize_org_name("SAP SE") == "sap"

    def test_strips_nv(self) -> None:
        assert normalize_org_name("ASML Holding N.V.") == "asml"

    def test_strips_spa(self) -> None:
        assert normalize_org_name("Eni S.p.A.") == "eni"

    def test_strips_ab(self) -> None:
        assert normalize_org_name("Volvo AB") == "volvo"

    def test_strips_oyj(self) -> None:
        assert normalize_org_name("Nokia Oyj") == "nokia"

    def test_strips_asa(self) -> None:
        assert normalize_org_name("Equinor ASA") == "equinor"

    def test_strips_kk(self) -> None:
        assert normalize_org_name("Toyota K.K.") == "toyota"

    def test_strips_bhd(self) -> None:
        assert normalize_org_name("Petronas Bhd") == "petronas"

    # --- DBA stripping ---

    def test_dba_strips_after_clause(self) -> None:
        assert normalize_org_name("ACME LLC DBA ACME CLOUD") == "acme"

    def test_dba_doing_business_as(self) -> None:
        assert normalize_org_name("Alpha Inc. doing business as Alpha Cloud") == "alpha"

    def test_dba_trading_as(self) -> None:
        assert normalize_org_name("Beta Ltd trading as BetaNet") == "beta"

    def test_dba_slash_variant(self) -> None:
        assert normalize_org_name("Gamma Corp d/b/a Gamma Services") == "gamma"

    def test_dba_at_start_preserved(self) -> None:
        """If DBA appears at position 0, keep the full string (no content before it)."""
        result = normalize_org_name("DBA Something Else")
        assert "something" in result

    # --- Subsidiary stripping ---

    def test_subsidiary_stripped(self) -> None:
        result = normalize_org_name("Alpha Inc., a subsidiary of Beta Holdings")
        assert result == "alpha"

    def test_division_stripped(self) -> None:
        result = normalize_org_name("Cloud Services, a division of Big Corp")
        assert result == "cloud services"

    # --- International preserved ---

    def test_international_preserved(self) -> None:
        assert normalize_org_name("International Paper") == "international paper"

    # --- Positional suffix anchoring ---

    def test_group_preserved_at_start(self) -> None:
        assert normalize_org_name("Group Nine Media") == "group nine media"

    def test_sa_preserved_at_start(self) -> None:
        assert normalize_org_name("SA Power Networks") == "sa power networks"

    def test_ab_preserved_at_start(self) -> None:
        assert normalize_org_name("AB InBev") == "ab inbev"

    def test_se_preserved_at_start(self) -> None:
        assert normalize_org_name("SE Health") == "se health"

    def test_ag_preserved_at_start(self) -> None:
        assert normalize_org_name("AG Insurance") == "ag insurance"

    def test_nv_preserved_at_start(self) -> None:
        assert normalize_org_name("NV Energy") == "nv energy"

    # --- Trailing chain stripping ---

    def test_trailing_chain(self) -> None:
        assert normalize_org_name("Acme Holdings Group") == "acme"

    def test_trailing_chain_with_unambiguous(self) -> None:
        assert normalize_org_name("Acme Group Holdings AG Inc") == "acme"

    # --- Dotted vs dotless SA/NV/Co ---

    def test_sa_dotted_stripped_everywhere(self) -> None:
        """S.A. with dot is unambiguous — stripped at any position."""
        assert normalize_org_name("S.A. Utilities Inc") == "utilities"

    def test_co_dotted_stripped(self) -> None:
        """Co. with dot is unambiguous — stripped at any position."""
        assert normalize_org_name("Samsung Electronics Co.") == "samsung electronics"

    # --- Abbreviation expansion ---

    def test_abbrev_intl(self) -> None:
        assert normalize_org_name("Acme Intl") == "acme international"

    def test_abbrev_tech(self) -> None:
        assert normalize_org_name("Acme Tech") == "acme technology"

    def test_abbrev_svcs(self) -> None:
        assert normalize_org_name("Global Svcs") == "global services"

    def test_abbrev_mgmt(self) -> None:
        assert normalize_org_name("Acme Mgmt") == "acme management"


class TestSimilarity:
    def test_exact_match(self) -> None:
        assert org_name_similarity("Palo Alto Networks", "Palo Alto Networks") == 1.0

    def test_with_suffix(self) -> None:
        score = org_name_similarity("Palo Alto Networks", "Palo Alto Networks, Inc.")
        assert score >= 0.95

    def test_reordered(self) -> None:
        score = org_name_similarity("Palo Alto Networks", "Networks Palo Alto")
        assert score >= 0.85

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

    # --- Acronym detection ---

    def test_acronym_ibm(self) -> None:
        score = org_name_similarity("International Business Machines", "IBM")
        assert score == 0.85

    def test_acronym_hp(self) -> None:
        score = org_name_similarity("Hewlett Packard", "HP")
        assert score == 0.85

    def test_acronym_ge(self) -> None:
        score = org_name_similarity("General Electric", "GE")
        assert score == 0.85

    def test_acronym_aig(self) -> None:
        """AIG = American International Group — Group kept for initials."""
        score = org_name_similarity("American International Group", "AIG")
        assert score == 0.85

    def test_acronym_single_letter_rejected(self) -> None:
        """Single-letter 'acronyms' should not trigger acronym detection."""
        score = org_name_similarity("Apple", "A")
        # Partial ratio coincidentally gives 0.85; verify no acronym boost above that
        assert score <= 0.85

    def test_acronym_ticker_not_matched(self) -> None:
        """AMEX is a ticker, not an acronym of American Express."""
        score = org_name_similarity("American Express", "AMEX")
        assert score < 0.85

    # --- CamelCase acronym detection ---

    def test_acronym_camelcase_uhg(self) -> None:
        """UHG = UnitedHealth Group — CamelCase split needed."""
        score = org_name_similarity("UnitedHealth Group Incorporated", "UHG")
        assert score == 0.85

    def test_acronym_camelcase_jpmc(self) -> None:
        """JPMC = JPMorgan Chase — CamelCase + uppercase split needed."""
        score = org_name_similarity("JPMorgan Chase & Co.", "JPMC")
        assert score == 0.85

    def test_acronym_camelcase_gsk(self) -> None:
        """GSK = GlaxoSmithKline — CamelCase split needed."""
        score = org_name_similarity("GlaxoSmithKline PLC", "GSK")
        assert score == 0.85

    # --- Legal suffix kept for initials ---

    def test_acronym_legal_suffix_tsmc(self) -> None:
        """TSMC — Company is part of the acronym."""
        score = org_name_similarity("Taiwan Semiconductor Manufacturing Company", "TSMC")
        assert score == 0.85

    def test_acronym_legal_suffix_smbc(self) -> None:
        """SMBC — Corporation is part of the acronym."""
        score = org_name_similarity("Sumitomo Mitsui Banking Corporation", "SMBC")
        assert score == 0.85

    def test_acronym_legal_suffix_hul(self) -> None:
        """HUL — Limited is part of the acronym."""
        score = org_name_similarity("Hindustan Unilever Limited", "HUL")
        assert score == 0.85

    # --- Stop word removal ---

    def test_acronym_stop_word_cba(self) -> None:
        """CBA = Commonwealth Bank of Australia — 'of' skipped."""
        score = org_name_similarity("Commonwealth Bank of Australia", "CBA")
        assert score == 0.85

    def test_acronym_stop_word_sbi(self) -> None:
        """SBI = State Bank of India — 'of' skipped."""
        score = org_name_similarity("State Bank of India", "SBI")
        assert score == 0.85

    def test_acronym_stop_word_icbc(self) -> None:
        """ICBC — 'and' + 'of' skipped."""
        score = org_name_similarity("Industrial and Commercial Bank of China", "ICBC")
        assert score == 0.85

    def test_acronym_stop_word_ntt(self) -> None:
        """NTT — 'and' skipped, Corporation stripped."""
        score = org_name_similarity("Nippon Telegraph and Telephone Corporation", "NTT")
        assert score == 0.85

    # --- Combined: CamelCase + legal suffix ---

    def test_acronym_mufg(self) -> None:
        """MUFG — standalone UFJ kept as single initial."""
        score = org_name_similarity("Mitsubishi UFJ Financial Group, Inc.", "MUFG")
        assert score == 0.85

    # --- Brand alias matching ---

    def test_alias_petrobras(self) -> None:
        score = org_name_similarity("Petrobras", "Petróleo Brasileiro S.A.")
        assert score >= 0.95

    def test_alias_petrobras_reverse(self) -> None:
        """Alias works in both directions."""
        score = org_name_similarity("Petróleo Brasileiro S.A.", "Petrobras")
        assert score >= 0.95

    def test_alias_foxconn(self) -> None:
        score = org_name_similarity("Foxconn", "Hon Hai Precision Industry Co., Ltd.")
        assert score >= 0.85

    def test_alias_etisalat(self) -> None:
        score = org_name_similarity(
            "Emirates Telecommunications Group Company",
            "Etisalat",
        )
        assert score >= 0.85

    def test_alias_singtel(self) -> None:
        score = org_name_similarity("Singapore Telecommunications Limited", "SingTel")
        assert score >= 0.85

    # --- CamelCase edge cases (documenting known behavior) ---

    def test_acronym_camelcase_mcdonald(self) -> None:
        """McDonald splits to Mc+Donald — 'md' is a prefix of 'mdsc'
        initials. Accepted: 2-letter acronym at 0.85 requires additional
        validation (DNS, RDAP) to reach high confidence."""
        score = org_name_similarity("McDonald's Corporation", "MD")
        assert score == 0.85

    def test_acronym_embedded_uppercase_stays_single(self) -> None:
        """Standalone uppercase tokens (UFJ, XML) are NOT expanded to
        individual letters — only CamelCase-extracted segments are."""
        # Standalone all-uppercase: single initial
        assert _extract_initials_from_word("UFJ") == "u"
        assert _extract_initials_from_word("XML") == "x"
        # CamelCase-extracted uppercase: each letter is an initial
        assert _extract_initials_from_word("JPMorgan") == "jpm"

    # --- Known limitations: 2-letter acronym ambiguity ---

    def test_short_acronym_matches_any_matching_initials(self) -> None:
        """2-letter acronyms are inherently ambiguous — 'GE' matches any
        company with initials G+E. This is accepted because 0.85 is below
        the confidence boost threshold (0.9), so matches still require
        additional validation (DNS, RDAP)."""
        score = org_name_similarity("Global Energy Corp", "GE")
        assert score == 0.85  # matches, by design

    # --- Stop word 'de' behavior ---

    def test_de_preserved_in_normalization(self) -> None:
        """'de' is NOT stripped from normalized names — only from
        acronym initials."""
        assert normalize_org_name("De Beers Group") == "de beers"
        assert normalize_org_name("Banco de Chile S.A.") == "banco de chile"

    def test_de_beers_fuzzy_match(self) -> None:
        """De Beers matches perfectly via fuzzy path despite 'de' being
        skipped in acronym initials."""
        score = org_name_similarity("De Beers Group", "De Beers")
        assert score >= 0.95

    # --- Abbreviation similarity ---

    def test_similarity_intl_expansion(self) -> None:
        """'Acme Intl' and 'Acme International' should match perfectly after expansion."""
        score = org_name_similarity("Acme Intl", "Acme International")
        assert score == 1.0

    def test_similarity_tech_expansion(self) -> None:
        score = org_name_similarity("Acme Tech Inc", "Acme Technology")
        assert score >= 0.95

    def test_similarity_dba_stripped(self) -> None:
        score = org_name_similarity("ACME LLC DBA ACME CLOUD", "Acme")
        assert score >= 0.95

    def test_similarity_subsidiary_stripped(self) -> None:
        score = org_name_similarity("Alpha Inc., a subsidiary of Beta Holdings", "Alpha")
        assert score >= 0.95

    def test_dba_matches_operating_name(self) -> None:
        """DBA dual-match: the operating name should also be compared."""
        score = org_name_similarity("ACME LLC DBA ACME CLOUD", "Acme Cloud")
        assert score >= 0.95

    def test_dba_matches_legal_name(self) -> None:
        """DBA dual-match: the legal name (before DBA) still matches."""
        score = org_name_similarity("ACME LLC DBA ACME CLOUD", "Acme")
        assert score >= 0.95

    # --- Conglomerate disambiguation (different entities, same brand) ---

    def test_conglomerate_samsung(self) -> None:
        score = org_name_similarity("Samsung Electronics Co., Ltd.", "Samsung SDI Co., Ltd.")
        assert score < 0.65

    def test_conglomerate_deutsche(self) -> None:
        score = org_name_similarity("Deutsche Bank AG", "Deutsche Telekom AG")
        assert score < 0.65

    def test_conglomerate_general(self) -> None:
        score = org_name_similarity("General Electric", "General Motors")
        assert score < 0.65

    def test_conglomerate_american(self) -> None:
        score = org_name_similarity("American Express", "American Airlines")
        assert score < 0.65

    def test_conglomerate_mitsubishi(self) -> None:
        score = org_name_similarity("Mitsubishi Electric", "Mitsubishi Chemical")
        assert score < 0.65

    def test_conglomerate_liberty(self) -> None:
        score = org_name_similarity("Liberty Mutual Group", "Liberty Media")
        assert score < 0.65

    def test_conglomerate_similar_unique_tokens_no_penalty(self) -> None:
        """Unique tokens that ARE similar (typo/abbreviation) should NOT
        trigger the conglomerate penalty."""
        score = org_name_similarity("Acme Financial", "Acme Finance")
        assert score >= 0.80

    # --- Goldman Sachs: prefix match for acronym initials ---

    def test_acronym_gs_prefix_match(self) -> None:
        """GS = Goldman Sachs — prefix of 'gsgi' initials from
        'Goldman Sachs Group, Inc.' Trailing suffixes don't block match."""
        score = org_name_similarity("Goldman Sachs Group, Inc.", "GS")
        assert score == 0.85

    # --- Length-ratio guard on partial_ratio ---

    def test_short_substring_penalized(self) -> None:
        """Short names matching inside long names should not get inflated
        scores from partial_ratio or token_set_ratio."""
        score = org_name_similarity("Bank", "Deutsche Bank AG")
        assert score < 0.80

    # --- Brand alias roundtrip ---

    def test_alias_keys_survive_normalization(self) -> None:
        """All brand alias keys must be stable under normalize_org_name()."""
        for a, b in _BRAND_ALIAS_PAIRS:
            assert normalize_org_name(a) == a, f"alias key {a!r} changed"
            assert normalize_org_name(b) == b, f"alias key {b!r} changed"


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

    def test_international_in_name(self) -> None:
        """International is preserved (not stripped as suffix), producing
        correct slugs like 'internationalpaper' instead of just 'paper'."""
        slugs = domain_from_company_name("International Paper Co.")
        assert "internationalpaper" in slugs
        assert "international-paper" in slugs

    def test_suffix_only(self) -> None:
        assert domain_from_company_name("Inc.") == []


class TestPropertyBased:
    """Property-based tests for matching invariants."""

    # Alphabet of letters, digits, and spaces for realistic company names
    _COMPANY_CHARS = st.sampled_from(
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 "
    )

    @given(
        a=st.text(min_size=1, max_size=50, alphabet=_COMPANY_CHARS),
        b=st.text(min_size=1, max_size=50, alphabet=_COMPANY_CHARS),
    )
    def test_similarity_symmetric(self, a: str, b: str) -> None:
        """similarity(a, b) == similarity(b, a)."""
        assert org_name_similarity(a, b) == org_name_similarity(b, a)

    @given(name=st.text(min_size=0, max_size=100))
    def test_normalize_idempotent(self, name: str) -> None:
        """normalize(normalize(x)) == normalize(x)."""
        once = normalize_org_name(name)
        twice = normalize_org_name(once)
        assert once == twice

    @given(
        a=st.text(min_size=0, max_size=50),
        b=st.text(min_size=0, max_size=50),
    )
    def test_similarity_range(self, a: str, b: str) -> None:
        """Score always in [0.0, 1.0]."""
        score = org_name_similarity(a, b)
        assert 0.0 <= score <= 1.0

    _LETTER_CHARS = st.sampled_from("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")

    @given(name=st.text(min_size=1, max_size=50, alphabet=_LETTER_CHARS))
    def test_self_similarity(self, name: str) -> None:
        """Name compared to itself scores 1.0 (or 0.0 if empty after normalization)."""
        score = org_name_similarity(name, name)
        normalized = normalize_org_name(name)
        if normalized:
            assert score == 1.0
        else:
            assert score == 0.0


class TestStrictOrgNameMatch:
    """Word-bounded precision gate for the ct_org_match source (issue #174).

    Ported from insurance-market-db#200 (tests/test_shared_features.py::
    test_entity_name_match). Signature is strict_org_name_match(entity, text)
    where ``entity`` is the target company and ``text`` is the certificate org.
    """

    # The insurance-market-db#200 regression matrix, ported verbatim.
    @pytest.mark.parametrize(
        "entity,text,expected",
        [
            # single-brand entities: the bounded core must appear
            ("Chubb", "© 2026 Chubb Limited. All rights reserved.", True),
            ("Chubb", "© 2026 Acme Holdings", False),
            ("Allianz SE", "© Allianz 2026. All Rights Reserved.", True),
            # multi-word: bounded core AND >=2 significant words
            ("Zurich Insurance Group", "Zurich Insurance Group Ltd", True),
            # SUBSTRING false positives must never match (word boundaries):
            ("AXA", "© 2026 MAXAIR Industries", False),
            ("Generali", "© The Generalist", False),
            ("Aviva", "© Avivagen Animal Health", False),
            ("ERGO Group", "© Allergology Ergonomics Group", False),
            # generic-suffix overlap alone must never match:
            ("Zurich Insurance Group", "© 2026 Allianz Insurance Group", False),
            # city/brand collisions: bare city word is not the entity
            ("Zurich Insurance Group", "© 2026 Zurich Airport", False),
            ("Munich Re", "© 2026 Munich Airport", False),
            ("Munich Re", "UniCredit Bank GmbH", False),
            ("Munich Re", "© 2026 Munich Re", True),
            # bounded phrase: 'munich re' is not a prefix-match of 'reinsurance'
            ("Munich Re", "Munich Reinsurance content page", False),
            # sibling legal entities: core phrase must match exactly
            ("AXA Insurance UK plc", "© AXA Insurance dac", False),
            # accent folding must apply to BOTH sides
            ("Länsförsäkringar AB", "© Länsförsäkringar AB 2026", True),
            # CJK: no word boundaries in unspaced scripts — containment is match
            ("東京海上ホールディングス", "© 東京海上ホールディングス株式会社 2026", True),
            ("東京海上ホールディングス", "© 損保ジャパン株式会社 2026", False),
            ("", "anything", False),
            ("Chubb", "", False),
        ],
    )
    def test_regression_matrix(self, entity: str, text: str, expected: bool) -> None:
        assert strict_org_name_match(entity, text) is expected

    # domain-scout's own documented wrong-owner attributions (#174 / #56):
    # cert_org is the certificate subject org; entity is our search target.
    @pytest.mark.parametrize(
        "entity,cert_org",
        [
            ("Aon", "kaonavi Inc"),  # substring: 'aon' inside 'kaonavi'
            ("Munich Re", "UniCredit Bank GmbH"),  # → 26 HVB/UniCredit domains
            ("Munich Re", "Hypo Vereinsbank"),  # HVB, different industry
            ("Promutuel Insurance", "Liberty Mutual Insurance"),  # → 13 Liberty
            ("Everest", "Pinterest"),  # substring: 'everest' inside 'pinterest'
        ],
    )
    def test_wrong_owner_pairs_rejected(self, entity: str, cert_org: str) -> None:
        assert strict_org_name_match(entity, cert_org) is False

    # Legitimate matches must survive (TP preservation — pulled from the
    # existing acceptance/similarity fixtures so we don't over-tighten).
    @pytest.mark.parametrize(
        "entity,cert_org",
        [
            ("Walmart", "Walmart Inc."),  # test_acceptance fixture
            ("Walmart", "Walmart Canada Corp."),  # test_acceptance fixture
            ("Palo Alto Networks", "Palo Alto Networks, Inc."),  # test_real_world
            ("Guidewire Software", "Guidewire Software, Inc."),  # test_real_world
            ("Munich Re", "Munich Re Group"),  # generic-suffix positive
            ("Zurich Insurance Group", "Zurich Insurance Company Ltd"),
        ],
    )
    def test_legitimate_matches_preserved(self, entity: str, cert_org: str) -> None:
        assert strict_org_name_match(entity, cert_org) is True

    # (a) Exact-name round-trip property: a name must always match itself. This
    # is the guard that would have caught #174 finding 1 — the hyphen-preserving
    # core vs hyphen-folding text made byte-identical names return False.
    @pytest.mark.parametrize(
        "name",
        [
            # hyphenated brands (finding 1)
            "Coca-Cola",
            "Hewlett-Packard",
            "Mercedes-Benz",
            "Anheuser-Busch",
            "Rolls-Royce",
            "Bristol-Myers",
            # abbreviation forms (finding 2)
            "Palo Alto Tech",
            "Acme Intl",
            # single-word brands
            "Walmart",
            "Chubb",
            "Visa",
            # multi-word
            "Zurich Insurance Group",
            "Munich Re",
            "Berkshire Hathaway",
            # accented + CJK (folding must apply on both sides)
            "Länsförsäkringar",
            "東京海上ホールディングス",
        ],
    )
    def test_exact_name_roundtrip(self, name: str) -> None:
        assert strict_org_name_match(name, name) is True

    # (b) Hyphenated-brand and abbreviation-form true positives (findings 1 & 2):
    # the distinctive core must match once hyphens fold to spaces and the core is
    # built without abbreviation expansion.
    @pytest.mark.parametrize(
        "entity,cert_org",
        [
            ("Coca-Cola", "The Coca-Cola Company"),
            ("Hewlett-Packard", "Hewlett-Packard Enterprise"),
            ("Mercedes-Benz", "Mercedes-Benz Group AG"),
            ("Rolls-Royce", "Rolls-Royce Holdings plc"),
            ("Bristol-Myers", "Bristol-Myers Squibb"),
            ("Palo Alto Tech", "Palo Alto Tech Inc"),
            ("Acme Intl", "Acme Intl Ltd"),
        ],
    )
    def test_hyphenated_and_abbrev_positives(self, entity: str, cert_org: str) -> None:
        assert strict_org_name_match(entity, cert_org) is True

    # (c) Differential parity against the insurance-market-db reference
    # (features.entity_name_match). This is the guard that would have caught
    # findings 1 & 2: every pair below MUST produce the same boolean in both the
    # port and the reference. Skips cleanly when the private reference is absent.
    #
    # NOTE — intentional divergence NOT asserted here: on hyphen+multiword
    # entities the reference under-matches (its significant-word tokenizer joins
    # hyphens — "anheuserbusch" — then searches word-bounded, so it can never
    # hit the space-folded text). The port's tokenizer splits hyphens, so e.g.
    # strict_org_name_match("Anheuser-Busch InBev", "Anheuser-Busch InBev SA")
    # is True (correct) while the reference returns False. The port is the more
    # correct side; those cases are deliberately excluded from this parity set.
    @pytest.mark.skipif(
        _reference_entity_name_match is None,
        reason="insurance-market-db reference (features.entity_name_match) not available",
    )
    @pytest.mark.parametrize(
        "entity,text",
        [
            # finding-1 hyphenated positives
            ("Coca-Cola", "Coca-Cola"),
            ("Coca-Cola", "Coca-Cola Company"),
            ("Hewlett-Packard", "Hewlett-Packard"),
            ("Mercedes-Benz", "Mercedes-Benz Group"),
            ("Rolls-Royce", "Rolls-Royce Holdings plc"),
            ("Bristol-Myers", "Bristol-Myers Squibb"),
            # finding-2 abbreviation positives
            ("Palo Alto Tech", "Palo Alto Tech Inc"),
            ("Acme Intl", "Acme Intl Ltd"),
            # exact / legal-suffix true positives
            ("Walmart", "Walmart Inc."),
            ("Chubb", "© 2026 Chubb Limited"),
            # substring / word-boundary false positives
            ("AXA", "MAXAIR Industries"),
            ("Aon", "kaonavi Inc"),
            ("Generali", "The Generalist"),
            ("Everest", "Pinterest"),
            ("Munich Re", "UniCredit Bank GmbH"),
            ("Promutuel Insurance", "Liberty Mutual Insurance"),
            # generic-overlap + city-collision false positives (the ≥2 rule)
            ("Zurich Insurance Group", "© 2026 Allianz Insurance Group"),
            ("Zurich Insurance Group", "© 2026 Zurich Airport"),
            # finding-3 shared recall limitation (both return False)
            ("Sony Group", "Sony Corporation"),
            ("AXA Group", "AXA S.A."),
            ("The Hartford", "Hartford Insurance Company"),
            # accent folding on both sides
            ("Länsförsäkringar AB", "© Länsförsäkringar AB 2026"),
            # CJK containment
            ("東京海上ホールディングス", "© 東京海上ホールディングス株式会社"),
            ("東京海上ホールディングス", "© 損保ジャパン株式会社"),
        ],
    )
    def test_differential_parity_with_reference(self, entity: str, text: str) -> None:
        assert _reference_entity_name_match is not None  # for type-checkers
        assert strict_org_name_match(entity, text) is _reference_entity_name_match(entity, text)
