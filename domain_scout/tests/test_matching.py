"""Tests for entity name matching and domain slug generation."""

from __future__ import annotations

from domain_scout.matching.entity_match import (
    _BRAND_ALIAS_PAIRS,
    _extract_initials_from_word,
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
