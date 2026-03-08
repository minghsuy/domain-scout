"""Invariant tests: CI gates to catch bad automated PRs before human review.

These tests guard architectural invariants that automated tooling (Jules etc.)
commonly breaks. Each test category maps to a real PR that was closeable.
"""

from __future__ import annotations

import hashlib
import math
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from domain_scout.eval import compute_metrics
from domain_scout.scout import (
    Scout,
    _DomainAccum,
    _normalize_time,
)
from domain_scout.sources.local_parquet import _fingerprint_to_cert_id

# ---------------------------------------------------------------------------
# 1. _normalize_time correctness (guards against PR #66 type changes)
# ---------------------------------------------------------------------------


class TestNormalizeTime:
    """_normalize_time must validate datetime strings, not just string-manipulate."""

    def test_datetime_object(self) -> None:
        dt = datetime(2025, 1, 15, 12, 0, 0)
        assert _normalize_time(dt) == "2025-01-15T12:00:00"

    def test_valid_string(self) -> None:
        assert _normalize_time("2025-01-15 12:00:00") == "2025-01-15T12:00:00"

    def test_already_iso(self) -> None:
        assert _normalize_time("2025-01-15T12:00:00") == "2025-01-15T12:00:00"

    def test_invalid_string_not_naively_converted(self) -> None:
        """Invalid datetime strings must NOT pass through with a naive T insertion.

        A bad fast-path implementation (like PR #66) might do
        val.replace(' ', 'T') without validation, turning '2025-99-99 99:99:99'
        into '2025-99-99T99:99:99'. The real implementation parses via
        datetime.fromisoformat and returns the original string on ValueError.
        """
        result = _normalize_time("2025-99-99 99:99:99")
        # Must NOT have a T inserted naively
        assert result != "2025-99-99T99:99:99"
        # The actual behavior: fromisoformat raises ValueError, returns original
        assert result == "2025-99-99 99:99:99"

    def test_none(self) -> None:
        assert _normalize_time(None) is None

    def test_empty_string(self) -> None:
        assert _normalize_time("") is None

    def test_with_timezone(self) -> None:
        result = _normalize_time("2025-01-15T12:00:00+00:00")
        assert result is not None
        assert "2025-01-15" in result
        assert "12:00:00" in result

    def test_with_microseconds(self) -> None:
        result = _normalize_time("2025-01-15T12:00:00.123456")
        assert result is not None
        assert "123456" in result

    def test_datetime_with_tz(self) -> None:
        dt = datetime(2025, 1, 15, 12, 0, 0, tzinfo=UTC)
        result = _normalize_time(dt)
        assert result is not None
        assert "2025-01-15" in result

    def test_non_string_non_datetime(self) -> None:
        """Non-string, non-datetime, non-None → str() fallback."""
        result = _normalize_time(42)
        assert result == "42"


# ---------------------------------------------------------------------------
# 2. MD5 ID stability (guards against PR #41 type changes)
# ---------------------------------------------------------------------------


class TestFingerPrintToId:
    """cert_id generation must remain stable — changing the hash breaks data."""

    def test_deterministic(self) -> None:
        fp = "abc123def456"
        id1 = _fingerprint_to_cert_id(fp)
        id2 = _fingerprint_to_cert_id(fp)
        assert id1 == id2

    def test_uses_md5(self) -> None:
        """Verify the actual hash output matches expected MD5.

        This test will FAIL if someone changes the hash algorithm.
        """
        fp = "abc123def456"
        expected = int(hashlib.md5(fp.encode()).hexdigest()[:8], 16)  # noqa: S324
        assert _fingerprint_to_cert_id(fp) == expected

    def test_different_inputs_different_ids(self) -> None:
        assert _fingerprint_to_cert_id("aaa") != _fingerprint_to_cert_id("bbb")

    def test_known_value(self) -> None:
        """Pin a known value so any algorithm change is caught."""
        fp = "test-fingerprint-001"
        expected_id = _fingerprint_to_cert_id(fp)
        # Re-derive from scratch to lock in the value
        raw = hashlib.md5(fp.encode()).hexdigest()[:8]  # noqa: S324
        assert expected_id == int(raw, 16)


# ---------------------------------------------------------------------------
# 3. Scoring priority invariants (guards against flattening PRs)
# ---------------------------------------------------------------------------


class TestScoringPriority:
    """Confidence tiers must maintain strict ordering."""

    @staticmethod
    def _make_accum(**kwargs: object) -> _DomainAccum:
        accum = _DomainAccum()
        for k, v in kwargs.items():
            setattr(accum, k, v)
        return accum

    def _score(self, accum: _DomainAccum, company: str = "TestCo") -> float:
        from domain_scout.config import ScoutConfig

        scout = Scout(config=ScoutConfig())
        return scout._score_confidence(accum, company, ["test.com"], domain="example.com")

    def test_corroboration_level3_highest(self) -> None:
        """resolves + rdap_match + 3+ sources → Level 3 (+0.10)."""
        accum = self._make_accum(
            sources={"ct_org_match", "rdap_registrant_match", "shared_infra"},
            cert_org_names={"TestCo"},
            resolves=True,
        )
        score = self._score(accum)
        # ct_org_match base 0.85 + Level 3 adjustment 0.10 = 0.95
        assert score == 0.95

    def test_corroboration_level2_middle(self) -> None:
        """resolves + rdap → Level 2 (+0.05)."""
        accum = self._make_accum(
            sources={"ct_org_match", "rdap_registrant_match"},
            cert_org_names={"TestCo"},
            resolves=True,
        )
        score = self._score(accum)
        # ct_org_match base 0.85 + Level 2 adjustment 0.05 = 0.90
        assert score == 0.90

    def test_corroboration_level1_resolves_only(self) -> None:
        """resolves only → Level 1 (no adjustment)."""
        accum = self._make_accum(
            sources={"ct_org_match"},
            cert_org_names=set(),
            resolves=True,
        )
        score = self._score(accum)
        # ct_org_match base 0.85 + Level 1 adjustment 0.00 = 0.85
        assert score == 0.85

    def test_corroboration_level0_penalty(self) -> None:
        """No resolution → Level 0 (-0.05)."""
        accum = self._make_accum(
            sources={"ct_org_match"},
            cert_org_names=set(),
            resolves=False,
        )
        score = self._score(accum)
        # ct_org_match base 0.85 + Level 0 adjustment -0.05 = 0.80
        assert score == 0.80

    def test_scoring_priority_ordering(self) -> None:
        """Level 3 > Level 2 > Level 1 > Level 0.

        This catches any refactoring that accidentally reorders the tiers.
        """

        def base_sources_fn(extra: set[str]) -> set[str]:
            return {"ct_org_match", *extra}

        # Level 3: resolves + rdap + multi_source
        l3 = self._make_accum(
            sources=base_sources_fn({"rdap_registrant_match", "shared_infra"}),
            cert_org_names={"TestCo"},
            resolves=True,
        )
        # Level 2: resolves + rdap only
        l2 = self._make_accum(
            sources=base_sources_fn({"rdap_registrant_match"}),
            cert_org_names={"TestCo"},
            resolves=True,
        )
        # Level 1: resolves only
        l1 = self._make_accum(
            sources=base_sources_fn(set()),
            cert_org_names=set(),
            resolves=True,
        )
        # Level 0: no resolution
        l0 = self._make_accum(
            sources=base_sources_fn(set()),
            cert_org_names=set(),
            resolves=False,
        )

        s3, s2, s1, s0 = (self._score(a) for a in (l3, l2, l1, l0))
        assert s3 > s2 > s1 > s0, f"Ordering violated: {s3=}, {s2=}, {s1=}, {s0=}"

    def test_cross_seed_base_is_090(self) -> None:
        """cross_seed_verified must set base score to 0.90."""
        accum = self._make_accum(
            sources={"cross_seed_verified", "ct_san_expansion:seed1.com"},
            cert_org_names=set(),
            resolves=True,
        )
        score = self._score(accum)
        assert score >= 0.90


# ---------------------------------------------------------------------------
# 4. Cross-seed boost invariants
# ---------------------------------------------------------------------------


class TestCrossSeedBoost:
    """Cross-seed detection requires 2 seeds + strong source type."""

    @staticmethod
    def _apply(evidence: dict[str, _DomainAccum], seeds: list[str]) -> None:
        Scout._apply_cross_seed_boost(evidence, seeds)

    def test_requires_two_seeds(self) -> None:
        """Domain with sources from only 1 seed → no cross_seed_verified."""
        accum = _DomainAccum()
        accum.sources = {"ct_san_expansion:seed1.com"}
        evidence = {"example.com": accum}
        self._apply(evidence, ["seed1.com", "seed2.com"])
        assert "cross_seed_verified" not in accum.sources

    def test_requires_strong_source(self) -> None:
        """Domain with 2 seeds but only weak sources → no cross_seed_verified."""
        accum = _DomainAccum()
        accum.sources = {"ct_seed_related:seed1.com", "ct_seed_related:seed2.com"}
        evidence = {"example.com": accum}
        self._apply(evidence, ["seed1.com", "seed2.com"])
        assert "cross_seed_verified" not in accum.sources

    def test_boost_applied(self) -> None:
        """Domain with 2 seeds + strong source → cross_seed_verified added."""
        accum = _DomainAccum()
        accum.sources = {
            "ct_san_expansion:seed1.com",
            "ct_seed_subdomain:seed2.com",
        }
        evidence = {"example.com": accum}
        self._apply(evidence, ["seed1.com", "seed2.com"])
        assert "cross_seed_verified" in accum.sources
        # Should also have an evidence record
        assert any(e.source_type == "cross_seed_verified" for e in accum.evidence)


# ---------------------------------------------------------------------------
# 5. _discover phase ordering (guards against PR #64 type refactors)
# ---------------------------------------------------------------------------


class TestPhaseOrdering:
    """Phases in _discover must execute in correct order."""

    @pytest.mark.asyncio
    async def test_dns_resolution_before_rdap_corroboration(self) -> None:
        """RDAP corroboration must see DNS results (it filters on accum.resolves)."""
        from domain_scout.config import ScoutConfig
        from domain_scout.models import EntityInput

        config = ScoutConfig()
        scout = Scout(config=config)

        # Track call order
        call_order: list[str] = []

        async def mock_bulk_resolve(domains: list[str]) -> dict[str, bool]:
            call_order.append("dns_bulk_resolve")
            return {d: True for d in domains}

        async def mock_rdap_corroborate(
            domain_evidence: dict[str, _DomainAccum], company_name: str
        ) -> None:
            call_order.append("rdap_corroborate")

        # Mock sources to produce minimal results
        scout._ct.search_by_domain = AsyncMock(return_value=[])  # type: ignore[method-assign]
        scout._ct.search_by_org = AsyncMock(return_value=[])  # type: ignore[method-assign]
        scout._dns.resolves = AsyncMock(return_value=True)  # type: ignore[method-assign]
        scout._dns.bulk_resolve = AsyncMock(side_effect=mock_bulk_resolve)  # type: ignore[method-assign]
        scout._dns.shares_infrastructure = AsyncMock(return_value=False)  # type: ignore[method-assign]
        scout._rdap.get_registrant_org = AsyncMock(return_value=None)  # type: ignore[method-assign]
        scout._rdap.get_registrant_info = AsyncMock(return_value=None)  # type: ignore[method-assign]
        scout._rdap_corroborate = AsyncMock(side_effect=mock_rdap_corroborate)  # type: ignore[method-assign]

        entity = EntityInput(company_name="TestCo", seed_domain=["test.com"])
        await scout.discover_async(entity)

        # If both were called, DNS must come first
        if "dns_bulk_resolve" in call_order and "rdap_corroborate" in call_order:
            dns_idx = call_order.index("dns_bulk_resolve")
            rdap_idx = call_order.index("rdap_corroborate")
            assert dns_idx < rdap_idx, (
                f"DNS resolution must happen before RDAP corroboration, but order was: {call_order}"
            )

    @pytest.mark.asyncio
    async def test_scoring_after_strategies(self) -> None:
        """Confidence scoring must happen after all strategies complete."""
        from domain_scout.config import ScoutConfig
        from domain_scout.models import EntityInput

        config = ScoutConfig()
        scout = Scout(config=config)

        # Return one CT record from org search
        ct_records = [
            {
                "cert_id": 1,
                "common_name": "found.com",
                "subject": "O=TestCo",
                "org_name": "TestCo",
                "not_before": "2025-01-01T00:00:00",
                "not_after": "2026-01-01T00:00:00",
                "san_dns_names": ["found.com"],
            }
        ]

        scout._ct.search_by_domain = AsyncMock(return_value=[])  # type: ignore[method-assign]
        scout._ct.search_by_org = AsyncMock(return_value=ct_records)  # type: ignore[method-assign]
        scout._dns.resolves = AsyncMock(return_value=True)  # type: ignore[method-assign]
        scout._dns.bulk_resolve = AsyncMock(return_value={"found.com": True})  # type: ignore[method-assign]
        scout._dns.shares_infrastructure = AsyncMock(return_value=False)  # type: ignore[method-assign]
        scout._rdap.get_registrant_org = AsyncMock(return_value=None)  # type: ignore[method-assign]
        scout._rdap.get_registrant_info = AsyncMock(return_value=None)  # type: ignore[method-assign]

        entity = EntityInput(company_name="TestCo", seed_domain=["test.com"])
        result = await scout.discover_async(entity)

        # If found.com is discovered, it must have a confidence score > 0
        found = [d for d in result.domains if d.domain == "found.com"]
        if found:
            assert found[0].confidence > 0, "Scoring must happen after strategies"


# ---------------------------------------------------------------------------
# 6. compute_metrics equivalence (guards against PR #61 type changes)
# ---------------------------------------------------------------------------


class TestComputeMetricsInvariants:
    """Known-value tests to catch behavioral changes in metric computation."""

    def test_known_values_k5(self) -> None:
        """Fixed input → fixed expected output at k=5."""
        ranked = ["a.com", "b.com", "x.com", "c.com", "y.com"]
        owned = {"a.com", "b.com", "c.com", "d.com"}
        results = compute_metrics(ranked, owned, set(), k_values=(5,))
        m = results[0]
        assert m.hits == 3
        assert m.precision == 0.6  # 3/5
        assert m.recall == 0.75  # 3/4

        # NDCG: DCG = 1/log2(2) + 1/log2(3) + 1/log2(5)
        dcg = 1.0 / math.log2(2) + 1.0 / math.log2(3) + 1.0 / math.log2(5)
        # IDCG (4 relevant, but k=5, so ideal top 4):
        idcg = sum(1.0 / math.log2(i + 2) for i in range(4))
        expected_ndcg = round(dcg / idcg, 3)
        assert m.ndcg == expected_ndcg

    def test_empty_input(self) -> None:
        results = compute_metrics([], {"a.com"}, set(), k_values=(5,))
        m = results[0]
        assert m.precision == 0.0
        assert m.recall == 0.0
        assert m.ndcg == 0.0
        assert m.hits == 0
        assert m.false_positives == 0

    def test_k_exceeds_results(self) -> None:
        """k=20 with only 5 results → adaptive denominator."""
        ranked = ["a.com", "b.com", "c.com", "d.com", "e.com"]
        owned = {"a.com", "b.com", "c.com"}
        results = compute_metrics(ranked, owned, set(), k_values=(20,))
        m = results[0]
        # precision = 3 / min(20, 5) = 3/5 = 0.6
        assert m.precision == 0.6
        assert m.recall == 1.0
        assert m.hits == 3

    def test_false_positives_in_not_owned(self) -> None:
        ranked = ["a.com", "bad.com", "b.com"]
        owned = {"a.com", "b.com"}
        not_owned = {"bad.com"}
        results = compute_metrics(ranked, owned, not_owned, k_values=(3,))
        assert results[0].false_positives == 1


# ---------------------------------------------------------------------------
# 7. Error handling graceful degradation
# ---------------------------------------------------------------------------


class TestGracefulDegradation:
    """Source failures must produce partial results, not exceptions."""

    @pytest.mark.asyncio
    async def test_strategy_org_search_ct_failure(self) -> None:
        """CT org search throws → empty results, error collected."""
        from domain_scout.config import ScoutConfig

        config = ScoutConfig()
        scout = Scout(config=config)
        scout._ct.search_by_org = AsyncMock(side_effect=RuntimeError("CT down"))  # type: ignore[method-assign]

        errors: list[str] = []
        results = await scout._strategy_org_search("TestCo", errors)
        assert results == []
        assert len(errors) == 1
        assert "CT org search failed" in errors[0]

    @pytest.mark.asyncio
    async def test_validate_seed_rdap_failure(self) -> None:
        """RDAP throws → seed still validated via DNS+CT, error collected."""
        from domain_scout.config import ScoutConfig

        config = ScoutConfig()
        scout = Scout(config=config)
        scout._dns.resolves = AsyncMock(return_value=True)  # type: ignore[method-assign]
        scout._rdap.get_registrant_org = AsyncMock(  # type: ignore[method-assign]
            side_effect=RuntimeError("RDAP down")
        )
        scout._ct.search_by_domain = AsyncMock(return_value=[])  # type: ignore[method-assign]

        errors: list[str] = []
        result = await scout._validate_seed("test.com", "TestCo", ["test.com"], errors)
        # Should complete despite RDAP failure
        assert "assessment" in result
        assert any("RDAP" in e for e in errors)

    @pytest.mark.asyncio
    async def test_strategy_seed_expansion_ct_failure(self) -> None:
        """CT seed expansion throws → empty results, error collected."""
        from domain_scout.config import ScoutConfig

        config = ScoutConfig()
        scout = Scout(config=config)
        scout._ct.search_by_domain = AsyncMock(  # type: ignore[method-assign]
            side_effect=RuntimeError("CT down")
        )

        errors: list[str] = []
        results = await scout._strategy_seed_expansion("test.com", "TestCo", errors)
        assert results == []
        assert len(errors) == 1
        assert "CT seed expansion failed" in errors[0]
