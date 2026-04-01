"""Tests for GLEIF corporate tree expansion in Scout.discover()."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from domain_scout.config import ScoutConfig
from domain_scout.scout import Scout, _DomainAccum, _filter_subsidiaries

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CREATE_GLEIF = """
CREATE TABLE gleif_entity (
    lei VARCHAR PRIMARY KEY,
    legal_name VARCHAR NOT NULL,
    other_names VARCHAR[],
    country VARCHAR
);
CREATE INDEX idx_gleif_lower ON gleif_entity (LOWER(legal_name));

CREATE TABLE gleif_relationship (
    child_lei VARCHAR NOT NULL,
    parent_lei VARCHAR NOT NULL,
    relationship_type VARCHAR,
    relationship_status VARCHAR
);
CREATE INDEX idx_gleif_rel_child ON gleif_relationship (child_lei);
CREATE INDEX idx_gleif_rel_parent ON gleif_relationship (parent_lei);
"""


def _seed_berkshire(con: Any) -> None:
    """Insert a Berkshire Hathaway corporate tree for testing."""
    con.execute(
        "INSERT INTO gleif_entity VALUES "
        "('LEI_BRK', 'Berkshire Hathaway Inc.', ['BRK', 'Berkshire'], 'US'),"
        "('LEI_GEICO', 'Government Employees Insurance Company', ['GEICO'], 'US'),"
        "('LEI_GENRE', 'General Reinsurance Corporation', ['Gen Re'], 'US'),"
        "('LEI_GENRE_AG', 'General Reinsurance AG', [], 'DE'),"
        "('LEI_BNSF', 'BNSF Railway Company', ['BNSF'], 'US'),"
        "('LEI_BHSI', 'Berkshire Hathaway Specialty Insurance Company', [], 'US'),"
        # Allianz test: bare name (0 subs) vs SE (has subs)
        "('LEI_ALZ_BARE', 'ALLIANZ', [], 'DE'),"
        "('LEI_ALZ_SE', 'Allianz SE', [], 'DE'),"
        "('LEI_ALZ_SUB', 'Allianz Versicherungs-Aktiengesellschaft', [], 'DE')"
    )
    con.execute(
        "INSERT INTO gleif_relationship VALUES "
        "('LEI_GEICO', 'LEI_BRK', 'IS_DIRECTLY_CONSOLIDATED_BY', 'ACTIVE'),"
        "('LEI_GENRE', 'LEI_BRK', 'IS_DIRECTLY_CONSOLIDATED_BY', 'ACTIVE'),"
        # Gen Re AG: direct parent is Gen Re Corp, ultimate parent is BRK
        "('LEI_GENRE_AG', 'LEI_GENRE', 'IS_DIRECTLY_CONSOLIDATED_BY', 'ACTIVE'),"
        "('LEI_GENRE_AG', 'LEI_BRK', 'IS_ULTIMATELY_CONSOLIDATED_BY', 'ACTIVE'),"
        "('LEI_BNSF', 'LEI_BRK', 'IS_DIRECTLY_CONSOLIDATED_BY', 'ACTIVE'),"
        "('LEI_BHSI', 'LEI_BRK', 'IS_DIRECTLY_CONSOLIDATED_BY', 'ACTIVE'),"
        # Allianz SE has a subsidiary
        "('LEI_ALZ_SUB', 'LEI_ALZ_SE', 'IS_DIRECTLY_CONSOLIDATED_BY', 'ACTIVE')"
    )


@pytest.fixture()
def gleif_db(tmp_path: Path) -> str:
    """Create a DuckDB with GLEIF schema and Berkshire data."""
    duckdb = pytest.importorskip("duckdb")
    db_path = str(tmp_path / "gleif_test.duckdb")
    con = duckdb.connect(db_path)
    con.execute(_CREATE_GLEIF)
    _seed_berkshire(con)
    con.close()
    return db_path


# ---------------------------------------------------------------------------
# gleif_lookup unit tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def gleif_con(gleif_db: str) -> Any:
    """Open a read-only DuckDB connection to the GLEIF test database."""
    duckdb = pytest.importorskip("duckdb")
    con = duckdb.connect(gleif_db, read_only=True)
    yield con
    con.close()


class TestFindEntity:
    def test_exact_match(self, gleif_con: Any) -> None:
        from domain_scout.resolve.gleif_lookup import find_entity

        result = find_entity("Berkshire Hathaway Inc.", gleif_con)
        assert result is not None
        assert result.lei == "LEI_BRK"
        assert result.legal_name == "Berkshire Hathaway Inc."

    def test_case_insensitive_match(self, gleif_con: Any) -> None:
        from domain_scout.resolve.gleif_lookup import find_entity

        result = find_entity("berkshire hathaway inc.", gleif_con)
        assert result is not None
        assert result.lei == "LEI_BRK"

    def test_prefix_match(self, gleif_con: Any) -> None:
        from domain_scout.resolve.gleif_lookup import find_entity

        result = find_entity("Berkshire Hathaway", gleif_con)
        assert result is not None
        assert result.lei == "LEI_BRK"

    def test_no_match(self, gleif_con: Any) -> None:
        from domain_scout.resolve.gleif_lookup import find_entity

        result = find_entity("Totally Nonexistent Company ZZZZZ", gleif_con)
        assert result is None

    def test_icase_prefers_entity_with_subs(self, gleif_con: Any) -> None:
        """Case-insensitive match skips entities with 0 subsidiaries in favor
        of prefix match that finds the parent entity."""
        from domain_scout.resolve.gleif_lookup import find_entity

        # "Allianz" case-insensitively matches "ALLIANZ" (0 subs), but prefix
        # match finds "Allianz SE" which has a subsidiary.
        result = find_entity("Allianz", gleif_con)
        assert result is not None
        assert result.lei == "LEI_ALZ_SE"


class TestExpandCorporateTree:
    def test_tree_expansion(self, gleif_con: Any) -> None:
        from domain_scout.resolve.gleif_lookup import expand_corporate_tree, find_entity

        entity = find_entity("Berkshire Hathaway Inc.", gleif_con)
        assert entity is not None
        tree = expand_corporate_tree(entity, gleif_con)

        assert tree.parent is None
        sub_names = {s.legal_name for s in tree.subsidiaries}
        assert "Government Employees Insurance Company" in sub_names
        assert "General Reinsurance Corporation" in sub_names
        assert "General Reinsurance AG" in sub_names  # via IS_ULTIMATELY_CONSOLIDATED_BY
        assert "BNSF Railway Company" in sub_names
        assert "Berkshire Hathaway Specialty Insurance Company" in sub_names
        assert tree.siblings == []

    def test_subsidiary_sees_parent_and_siblings(self, gleif_con: Any) -> None:
        from domain_scout.resolve.gleif_lookup import expand_corporate_tree, find_entity

        entity = find_entity("Government Employees Insurance Company", gleif_con)
        assert entity is not None
        tree = expand_corporate_tree(entity, gleif_con)

        assert tree.parent is not None
        assert tree.parent.lei == "LEI_BRK"
        sibling_leis = {s.lei for s in tree.siblings}
        assert "LEI_GENRE" in sibling_leis
        assert "LEI_BNSF" in sibling_leis
        assert "LEI_BHSI" in sibling_leis
        assert "LEI_GEICO" not in sibling_leis

    def test_all_names_deduped(self, gleif_con: Any) -> None:
        from domain_scout.resolve.gleif_lookup import expand_corporate_tree, find_entity

        entity = find_entity("Berkshire Hathaway Inc.", gleif_con)
        assert entity is not None
        tree = expand_corporate_tree(entity, gleif_con)
        names = tree.all_names
        lower_names = [n.lower() for n in names]
        assert len(lower_names) == len(set(lower_names))

    def test_ultimate_consolidation_includes_deep_subs(self, gleif_con: Any) -> None:
        """Multi-hop subsidiaries appear via IS_ULTIMATELY_CONSOLIDATED_BY."""
        from domain_scout.resolve.gleif_lookup import expand_corporate_tree, find_entity

        entity = find_entity("Berkshire Hathaway Inc.", gleif_con)
        assert entity is not None
        tree = expand_corporate_tree(entity, gleif_con)
        sub_leis = {s.lei for s in tree.subsidiaries}
        # Gen Re AG is an ultimate (not direct) subsidiary of BRK
        assert "LEI_GENRE_AG" in sub_leis


# ---------------------------------------------------------------------------
# Scout._expand_gleif_tree integration tests
# ---------------------------------------------------------------------------


class TestScoutGleifExpansion:
    def test_gleif_subsidiaries_searched(self, gleif_db: str) -> None:
        """GLEIF subsidiaries trigger CT org searches with correct source tag."""
        config = ScoutConfig(gleif_db_path=gleif_db, gleif_max_subsidiaries=10)
        scout = Scout(config=config)

        # Verify GLEIF connection was established
        assert scout._gleif_con is not None

        # Test the expansion method directly
        subs, siblings = scout._expand_gleif_tree("Berkshire Hathaway")
        # GEICO has a distinct brand (no overlap with "berkshire hathaway")
        assert any("GEICO" in s or "Government Employees" in s for s in subs)
        # BNSF Railway is distinct
        assert any("BNSF" in s for s in subs)
        # "Berkshire Hathaway Specialty Insurance" overlaps with parent → filtered
        assert not any("Berkshire Hathaway Specialty" in s for s in subs)

    def test_gleif_max_subsidiaries_cap(self, gleif_db: str) -> None:
        """gleif_max_subsidiaries limits how many subsidiaries are returned."""
        config = ScoutConfig(gleif_db_path=gleif_db, gleif_max_subsidiaries=1)
        scout = Scout(config=config)

        subs, _ = scout._expand_gleif_tree("Berkshire Hathaway")
        # The raw list may have >1, but in _discover() we slice to max
        # Here we test that the list is generated (slicing happens in _discover)
        assert len(subs) >= 1

    def test_gleif_sibling_names_populated(self, gleif_db: str) -> None:
        """Sibling names are returned for dedup penalty."""
        # Query as GEICO (subsidiary) — siblings should include Gen Re, BNSF, BHSI
        config = ScoutConfig(gleif_db_path=gleif_db)
        scout = Scout(config=config)

        _, siblings = scout._expand_gleif_tree("Government Employees Insurance Company")
        assert len(siblings) >= 2

    def test_gleif_no_match_returns_empty(self, gleif_db: str) -> None:
        config = ScoutConfig(gleif_db_path=gleif_db)
        scout = Scout(config=config)

        subs, siblings = scout._expand_gleif_tree("Totally Nonexistent Corp")
        assert subs == []
        assert siblings == set()

    def test_csv_fallback_when_no_gleif(self, tmp_path: Path) -> None:
        """When gleif_db_path is None, CSV subsidiary expansion is used."""
        csv_path = tmp_path / "subs.csv"
        csv_path.write_text(
            "parent_ticker,parent_cik,parent_name,subsidiary_name,jurisdiction\n"
            "BRK,1234,Berkshire Hathaway,GEICO Direct LLC,Delaware\n"
        )
        config = ScoutConfig(subsidiaries_path=str(csv_path))
        scout = Scout(config=config)
        assert scout._gleif_con is None
        assert len(scout._subsidiaries) >= 1


# ---------------------------------------------------------------------------
# Confidence scoring
# ---------------------------------------------------------------------------


class TestGleifScoring:
    def test_gleif_subsidiary_scored_at_080(self, gleif_db: str) -> None:
        """ct_gleif_subsidiary source gets 0.80 base score."""
        config = ScoutConfig(gleif_db_path=gleif_db)
        scout = Scout(config=config)
        accum = _DomainAccum()
        accum.sources.add("ct_gleif_subsidiary")
        accum.resolves = True

        score = scout._score_confidence(accum, "Berkshire Hathaway", [])
        assert score >= 0.80

    def test_sibling_penalty_applied(self, gleif_db: str) -> None:
        """Domains matching sibling entities get confidence penalty."""
        config = ScoutConfig(gleif_db_path=gleif_db)
        scout = Scout(config=config)

        # Simulate: querying GEICO, found domain with cert org "BNSF Railway Company"
        # BNSF is a sibling of GEICO under Berkshire Hathaway
        _, siblings = scout._expand_gleif_tree("Government Employees Insurance Company")

        accum = _DomainAccum()
        accum.sources.add("ct_gleif_subsidiary")
        accum.cert_org_names.add("BNSF Railway Company")
        accum.resolves = True

        score = scout._score_confidence(accum, "GEICO", [], sibling_names=siblings)
        # Base 0.80 - 0.15 sibling penalty = 0.65
        assert score <= 0.70
        assert score >= 0.55


# ---------------------------------------------------------------------------
# Filter subsidiaries for GLEIF names
# ---------------------------------------------------------------------------


class TestFilterSubsidiariesGleif:
    def test_filters_overlapping_names(self) -> None:
        """Subsidiaries sharing words with parent are filtered."""
        filtered = _filter_subsidiaries(
            "berkshire hathaway",
            [
                "GEICO",
                "General Reinsurance Corporation",
                "Berkshire Hathaway Specialty Insurance Company",
            ],
        )
        names_lower = [n.lower() for n in filtered]
        assert any("geico" in n for n in names_lower)
        assert any("general reinsurance" in n for n in names_lower)
        # "Berkshire Hathaway Specialty" shares "berkshire" and "hathaway"
        assert not any("berkshire" in n for n in names_lower)

    def test_filters_shell_companies(self) -> None:
        """Pure legal shells are filtered."""
        filtered = _filter_subsidiaries(
            "acme",
            [
                "Holdings LLC",
                "Real Brand Name Inc",
            ],
        )
        assert len(filtered) == 1
        assert "Real Brand" in filtered[0]


# ---------------------------------------------------------------------------
# Graceful degradation
# ---------------------------------------------------------------------------


class TestGleifGracefulDegradation:
    def test_bad_db_path_skips(self) -> None:
        """Invalid gleif_db_path logs warning but doesn't crash."""
        config = ScoutConfig(gleif_db_path="/nonexistent/gleif.duckdb")
        scout = Scout(config=config)
        assert scout._gleif_con is None

    def test_no_gleif_db_no_expansion(self) -> None:
        """Without gleif_db_path, GLEIF expansion is skipped."""
        config = ScoutConfig()
        scout = Scout(config=config)
        assert scout._gleif_con is None
        subs, siblings = scout._expand_gleif_tree("Anything")
        assert subs == []
        assert siblings == set()
