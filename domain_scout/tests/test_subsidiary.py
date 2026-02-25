"""Tests for subsidiary-aware CT search (EDGAR Exhibit 21 integration)."""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from domain_scout.config import ScoutConfig
from domain_scout.scout import Scout, _DomainAccum, _filter_subsidiaries, load_subsidiary_map

# ---------------------------------------------------------------------------
# load_subsidiary_map tests
# ---------------------------------------------------------------------------


class TestLoadSubsidiaryMap:
    def test_loads_csv(self, tmp_path: Path) -> None:
        """Basic CSV loading and grouping by normalized parent name."""
        csv = tmp_path / "subs.csv"
        csv.write_text(
            textwrap.dedent("""\
            parent_ticker,parent_cik,parent_name,subsidiary_name,jurisdiction
            MMM,66740,3M CO,Meguiar's Inc.,California
            MMM,66740,3M CO,Scott Technologies Inc.,Delaware
            MMM,66740,3M CO,3M Canada Company,Canada
        """)
        )
        result = load_subsidiary_map(str(csv))
        # "3M CO" normalizes to "3m" (stripped "co")
        # "3M Canada Company" shares "3m" with parent → filtered out
        # "Meguiar's Inc." and "Scott Technologies" are distinct brands
        assert len(result) == 1
        key = next(iter(result))
        subs = result[key]
        assert any("meguiar" in s.lower() for s in subs)
        assert any("scott" in s.lower() for s in subs)

    def test_missing_file_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_subsidiary_map("/nonexistent/path.csv")

    def test_empty_csv(self, tmp_path: Path) -> None:
        """CSV with only header produces empty map."""
        csv = tmp_path / "empty.csv"
        csv.write_text("parent_ticker,parent_cik,parent_name,subsidiary_name,jurisdiction\n")
        result = load_subsidiary_map(str(csv))
        assert result == {}

    def test_dedup_normalized_subsidiaries(self, tmp_path: Path) -> None:
        """Duplicate subsidiaries (after normalization) are deduplicated."""
        csv = tmp_path / "subs.csv"
        csv.write_text(
            textwrap.dedent("""\
            parent_ticker,parent_cik,parent_name,subsidiary_name,jurisdiction
            XYZ,1234,Acme Corp,Widgetron LLC,Delaware
            XYZ,1234,Acme Corp,Widgetron L.L.C.,Delaware
        """)
        )
        result = load_subsidiary_map(str(csv))
        key = next(iter(result))
        # Both normalize to the same thing — should be deduped
        assert len(result[key]) == 1

    def test_malformed_csv_raises(self, tmp_path: Path) -> None:
        """CSV missing required columns raises KeyError."""
        csv = tmp_path / "bad.csv"
        csv.write_text("ticker,name\nXYZ,Acme\n")
        with pytest.raises(KeyError):
            load_subsidiary_map(str(csv))


# ---------------------------------------------------------------------------
# _filter_subsidiaries tests
# ---------------------------------------------------------------------------


class TestFilterSubsidiaries:
    def test_removes_overlapping_names(self) -> None:
        """Subsidiaries sharing words with parent are filtered out."""
        parent = "jpmorgan chase"
        subs = ["JPMorgan Securities LLC", "Paymentech LLC", "JPMorgan Asset Management"]
        filtered = _filter_subsidiaries(parent, subs)
        # "Paymentech" doesn't overlap with "jpmorgan chase"
        assert any("paymentech" in s.lower() for s in filtered)
        # JPMorgan ones overlap → removed
        assert not any("jpmorgan" in s.lower() for s in filtered)

    def test_removes_pure_shell_companies(self) -> None:
        """Pure legal shell names are filtered out."""
        parent = "acme"
        subs = ["Holdings LLC", "Capital Management LP"]
        filtered = _filter_subsidiaries(parent, subs)
        assert filtered == []

    def test_sorts_by_length(self) -> None:
        """Results are sorted by name length (shorter = more likely real brand)."""
        parent = "acme"
        subs = ["Very Long Brand Name Industries", "ShortCo", "Medium Brand"]
        filtered = _filter_subsidiaries(parent, subs)
        lengths = [len(s) for s in filtered]
        assert lengths == sorted(lengths)

    def test_empty_input(self) -> None:
        assert _filter_subsidiaries("anything", []) == []

    def test_preserves_distinct_brands(self) -> None:
        """Distinct brand subsidiaries pass through."""
        parent = "3m"
        subs = ["Meguiar's Inc.", "Scott Technologies Inc.", "D B Industries LLC"]
        filtered = _filter_subsidiaries(parent, subs)
        assert len(filtered) == 3


# ---------------------------------------------------------------------------
# Scout._match_subsidiaries tests
# ---------------------------------------------------------------------------


class TestMatchSubsidiaries:
    @staticmethod
    def _make_scout_with_subs(subs_map: dict[str, list[str]]) -> Scout:
        """Create a Scout with pre-loaded subsidiary map (no CSV needed)."""
        with patch.object(Scout, "__init__", lambda self: None):
            s = Scout.__new__(Scout)
            s._subsidiaries = subs_map
            return s

    def test_exact_match(self) -> None:
        s = self._make_scout_with_subs({"3m": ["Meguiar's Inc.", "Scott Technologies"]})
        result = s._match_subsidiaries("3M Co")
        assert "Meguiar's Inc." in result

    def test_fuzzy_match(self) -> None:
        s = self._make_scout_with_subs({"jpmorgan chase": ["Paymentech LLC"]})
        # "JPMorgan Chase & Co." normalizes close to "jpmorgan chase"
        result = s._match_subsidiaries("JPMorgan Chase & Co.")
        assert "Paymentech LLC" in result

    def test_no_match(self) -> None:
        s = self._make_scout_with_subs({"3m": ["Meguiar's Inc."]})
        result = s._match_subsidiaries("Totally Unrelated Company")
        assert result == []


# ---------------------------------------------------------------------------
# Integration: subsidiary expansion in discovery pipeline
# ---------------------------------------------------------------------------


class TestSubsidiaryExpansion:
    @staticmethod
    def _make_scout(**kwargs: object) -> Scout:
        """Create a Scout with patched __init__ and optional attribute overrides."""
        with patch.object(Scout, "__init__", lambda self: None):
            s = Scout.__new__(Scout)
            s.config = ScoutConfig()
            s._subsidiaries = {}
            for k, v in kwargs.items():
                setattr(s, k, v)
            return s

    def test_config_fields(self) -> None:
        """Config has subsidiary fields with correct defaults."""
        config = ScoutConfig()
        assert config.subsidiaries_path is None
        assert config.subsidiary_max_queries == 10

    def test_source_tag_in_scoring(self) -> None:
        """ct_subsidiary_match gets scored at 0.80 base."""
        s = self._make_scout()
        accum = _DomainAccum()
        accum.sources.add("ct_subsidiary_match")
        # No resolution -> Level 0 -> -0.05
        score = s._score_confidence(accum, "Test Co", [])
        assert score == 0.75

    def test_source_tag_with_resolution(self) -> None:
        """ct_subsidiary_match with resolution gets Level 1 -> 0.80."""
        s = self._make_scout()
        accum = _DomainAccum()
        accum.sources.add("ct_subsidiary_match")
        accum.resolves = True
        score = s._score_confidence(accum, "Test Co", [])
        assert score == 0.80

    @pytest.mark.asyncio
    async def test_subsidiary_queries_launched(self) -> None:
        """Subsidiary names trigger additional org search tasks."""
        from domain_scout.models import EntityInput

        ct_mock = AsyncMock(search_by_org=AsyncMock(return_value=[]))
        s = self._make_scout(
            _subsidiaries={"walmart": ["Jet.com Inc.", "Bonobos Inc."]},
            _ct=ct_mock,
            _rdap=AsyncMock(),
            _dns=AsyncMock(resolve=AsyncMock(return_value=False)),
        )

        entity = EntityInput(company_name="Walmart Inc.", seed_domain=[])
        await s._discover(entity)

        # Should have called search_by_org for: "Walmart Inc." + "Jet.com Inc." + "Bonobos Inc."
        org_calls = [call.args[0] for call in ct_mock.search_by_org.call_args_list]
        assert "Walmart Inc." in org_calls
        assert "Jet.com Inc." in org_calls
        assert "Bonobos Inc." in org_calls

    def test_max_queries_cap(self) -> None:
        """subsidiary_max_queries caps the number of subsidiary searches."""
        s = self._make_scout(_subsidiaries={"test": [f"Sub{i}" for i in range(50)]})
        result = s._match_subsidiaries("Test Corp")
        # _match_subsidiaries returns all; cap is applied in _discover
        assert len(result) == 50

    @pytest.mark.asyncio
    async def test_max_queries_cap_enforced_in_discover(self) -> None:
        """_discover() only launches subsidiary_max_queries CT searches."""
        from domain_scout.models import EntityInput

        subs = [f"Brand{i} Inc." for i in range(20)]
        ct_mock = AsyncMock(search_by_org=AsyncMock(return_value=[]))
        config = ScoutConfig(subsidiary_max_queries=3)
        s = self._make_scout(
            config=config,
            _subsidiaries={"test": subs},
            _ct=ct_mock,
            _rdap=AsyncMock(),
            _dns=AsyncMock(resolve=AsyncMock(return_value=False)),
        )

        entity = EntityInput(company_name="Test Corp", seed_domain=[])
        await s._discover(entity)

        # org search calls: 1 (parent) + 1 (domain guess) + 3 (capped subsidiaries)
        org_calls = [call.args[0] for call in ct_mock.search_by_org.call_args_list]
        sub_calls = [c for c in org_calls if c.startswith("Brand")]
        assert len(sub_calls) == 3
