"""Tests for multi-seed domain support and cross-verification."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from domain_scout.config import ScoutConfig
from domain_scout.models import (
    DiscoveredDomain,
    DomainAccumulator,
    EntityInput,
    EvidenceRecord,
    RunMetadata,
    ScoutResult,
)
from domain_scout.scout import Scout, _extract_contributing_seeds

# Shared stub result for backward compat tests
_STUB_META = RunMetadata(
    tool_version="0.0.0-test",
    timestamp=datetime.now(UTC),
    elapsed_seconds=0.0,
    domains_found=0,
)
_STUB_RESULT = ScoutResult(entity=EntityInput(company_name="Test"), run_metadata=_STUB_META)

# --- Unit tests for cross-seed detection ---


class TestApplyCrossSeedBoost:
    """Test the static _apply_cross_seed_boost method."""

    def test_no_boost_single_seed(self) -> None:
        evidence: dict[str, DomainAccumulator] = {}
        a = DomainAccumulator()
        a.sources.add("ct_san_expansion:walmart.com")
        evidence["samsclub.com"] = a

        Scout._apply_cross_seed_boost(evidence, ["walmart.com"])
        assert "cross_seed_verified" not in a.sources

    def test_boost_two_seeds(self) -> None:
        a = DomainAccumulator()
        a.sources.add("ct_san_expansion:walmart.com")
        a.sources.add("ct_san_expansion:samsclub.com")
        evidence = {"walmartlabs.com": a}

        Scout._apply_cross_seed_boost(evidence, ["walmart.com", "samsclub.com"])
        assert "cross_seed_verified" in a.sources
        assert any(e.source_type == "cross_seed_verified" for e in a.evidence)

    def test_boost_mixed_source_types(self) -> None:
        a = DomainAccumulator()
        a.sources.add("ct_san_expansion:walmart.com")
        a.sources.add("ct_seed_related:samsclub.com")
        evidence = {"example.com": a}

        Scout._apply_cross_seed_boost(evidence, ["walmart.com", "samsclub.com"])
        assert "cross_seed_verified" in a.sources

    def test_no_boost_same_seed_different_types(self) -> None:
        """Two source types from the same seed should NOT trigger cross-verification."""
        a = DomainAccumulator()
        a.sources.add("ct_san_expansion:walmart.com")
        a.sources.add("ct_seed_subdomain:walmart.com")
        evidence = {"example.com": a}

        Scout._apply_cross_seed_boost(evidence, ["walmart.com", "samsclub.com"])
        assert "cross_seed_verified" not in a.sources

    def test_boost_three_seeds(self) -> None:
        a = DomainAccumulator()
        a.sources.add("ct_san_expansion:a.com")
        a.sources.add("ct_san_expansion:b.com")
        a.sources.add("ct_seed_related:c.com")
        evidence = {"shared.com": a}

        Scout._apply_cross_seed_boost(evidence, ["a.com", "b.com", "c.com"])
        assert "cross_seed_verified" in a.sources

    def test_domains_without_seed_sources_unaffected(self) -> None:
        a = DomainAccumulator()
        a.sources.add("ct_org_match")
        a.sources.add("dns_guess")
        evidence = {"example.com": a}

        Scout._apply_cross_seed_boost(evidence, ["walmart.com", "samsclub.com"])
        assert "cross_seed_verified" not in a.sources


# --- Unit tests for seed-tagged source scoring ---


class TestScoreConfidenceMultiSeed:
    def setup_method(self) -> None:
        self.scout = Scout(config=ScoutConfig())

    def test_cross_seed_verified_score(self) -> None:
        a = DomainAccumulator()
        a.sources.add("cross_seed_verified")
        a.sources.add("ct_san_expansion:walmart.com")
        a.sources.add("ct_san_expansion:samsclub.com")
        a.resolves = True
        score = self.scout._score_confidence(a, "Walmart", ["walmart.com", "samsclub.com"])
        # 0.90 base + 0.05 (Level 2: resolves + multi_source) = 0.95
        assert score == 0.95

    def test_tagged_san_expansion_no_resolves(self) -> None:
        """ct_san_expansion:seed without resolution gets -0.05 penalty."""
        a = DomainAccumulator()
        a.sources.add("ct_san_expansion:walmart.com")
        score = self.scout._score_confidence(a, "Walmart", ["walmart.com"])
        assert score == 0.75

    def test_tagged_seed_subdomain_no_resolves(self) -> None:
        a = DomainAccumulator()
        a.sources.add("ct_seed_subdomain:walmart.com")
        score = self.scout._score_confidence(a, "Walmart", ["walmart.com"])
        assert score == 0.70

    def test_tagged_seed_related_no_resolves(self) -> None:
        a = DomainAccumulator()
        a.sources.add("ct_seed_related:walmart.com")
        score = self.scout._score_confidence(a, "Walmart", ["walmart.com"])
        assert score == 0.35

    def test_no_seeds_no_resolves(self) -> None:
        a = DomainAccumulator()
        a.sources.add("ct_org_match")
        score = self.scout._score_confidence(a, "Walmart", [])
        assert score == 0.80

    def test_cross_seed_with_org_match_takes_highest(self) -> None:
        a = DomainAccumulator()
        a.sources.add("cross_seed_verified")
        a.sources.add("ct_org_match")
        a.sources.add("ct_san_expansion:walmart.com")
        a.sources.add("ct_san_expansion:samsclub.com")
        a.cert_org_names.add("Walmart Inc.")
        a.resolves = True
        score = self.scout._score_confidence(a, "Walmart", ["walmart.com", "samsclub.com"])
        # 0.90 base + 0.10 (Level 3: resolves + high_sim + multi_source) = 1.00
        assert score == 1.0


# --- Unit tests for build_output seed_sources ---


class TestBuildOutputSeedSources:
    def setup_method(self) -> None:
        self.scout = Scout(config=ScoutConfig())

    def test_seed_sources_populated(self) -> None:
        a = DomainAccumulator()
        a.sources.add("ct_san_expansion:walmart.com")
        a.sources.add("ct_san_expansion:samsclub.com")
        a.sources.add("cross_seed_verified")
        a.confidence = 0.95
        a.resolves = True
        evidence = {"walmartlabs.com": a}

        domains = self.scout._build_output(evidence, ["walmart.com", "samsclub.com"])
        assert len(domains) == 1
        assert sorted(domains[0].seed_sources) == ["samsclub.com", "walmart.com"]

    def test_is_seed_multi(self) -> None:
        a = DomainAccumulator()
        a.sources.add("ct_seed_subdomain:walmart.com")
        a.confidence = 0.90
        a.resolves = True
        evidence = {"walmart.com": a}

        domains = self.scout._build_output(evidence, ["walmart.com", "samsclub.com"])
        assert len(domains) == 1
        assert domains[0].is_seed is True

    def test_empty_seed_sources_when_no_seeds(self) -> None:
        a = DomainAccumulator()
        a.sources.add("ct_org_match")
        a.confidence = 0.90
        a.resolves = True
        evidence = {"example.com": a}

        domains = self.scout._build_output(evidence, [])
        assert len(domains) == 1
        assert domains[0].seed_sources == []
        assert domains[0].is_seed is False


# --- Unit tests for backward compatibility ---


class TestBackwardCompat:
    def test_discover_single_string_seed(self) -> None:
        """discover() with a single string seed should still work."""
        scout = Scout(config=ScoutConfig())
        with patch.object(scout, "_discover", new_callable=AsyncMock) as mock:
            mock.return_value = _STUB_RESULT
            scout.discover(company_name="Test", seed_domain="example.com")
            entity = mock.call_args[0][0]
            assert entity.seed_domain == ["example.com"]

    def test_discover_none_seed(self) -> None:
        scout = Scout(config=ScoutConfig())
        with patch.object(scout, "_discover", new_callable=AsyncMock) as mock:
            mock.return_value = _STUB_RESULT
            scout.discover(company_name="Test", seed_domain=None)
            entity = mock.call_args[0][0]
            assert entity.seed_domain == []

    def test_discover_list_seed(self) -> None:
        scout = Scout(config=ScoutConfig())
        with patch.object(scout, "_discover", new_callable=AsyncMock) as mock:
            mock.return_value = _STUB_RESULT
            scout.discover(company_name="Test", seed_domain=["a.com", "b.com"])
            entity = mock.call_args[0][0]
            assert entity.seed_domain == ["a.com", "b.com"]


# --- Model tests ---


class TestModelChanges:
    def test_entity_input_default_seeds(self) -> None:
        e = EntityInput(company_name="Test")
        assert e.seed_domain == []

    def test_entity_input_with_seeds(self) -> None:
        e = EntityInput(company_name="Test", seed_domain=["a.com", "b.com"])
        assert e.seed_domain == ["a.com", "b.com"]

    def test_scout_result_assessment_dict(self) -> None:
        r = ScoutResult(
            entity=EntityInput(company_name="Test", seed_domain=["a.com"]),
            seed_domain_assessment={"a.com": "confirmed"},
            run_metadata=_STUB_META,
        )
        assert r.seed_domain_assessment["a.com"] == "confirmed"

    def test_scout_result_cross_verification(self) -> None:
        r = ScoutResult(
            entity=EntityInput(company_name="Test", seed_domain=["a.com", "b.com"]),
            seed_cross_verification={"a.com": ["b.com"]},
            run_metadata=_STUB_META,
        )
        assert r.seed_cross_verification["a.com"] == ["b.com"]

    def test_discovered_domain_seed_sources(self) -> None:
        d = DiscoveredDomain(
            domain="example.com",
            confidence=0.90,
            seed_sources=["a.com", "b.com"],
        )
        assert d.seed_sources == ["a.com", "b.com"]


# --- Simulated scenario tests ---


class TestSimulatedScenarios:
    """Simulate real-world multi-seed scenarios using mocked CT data."""

    def setup_method(self) -> None:
        self.scout = Scout(config=ScoutConfig())

    def test_walmart_samsclub_cross_verification(self) -> None:
        """Simulate: walmart.com and samsclub.com both find walmartlabs.com."""
        evidence: dict[str, DomainAccumulator] = {}

        # walmartlabs.com found via walmart.com seed expansion
        a1 = DomainAccumulator()
        a1.sources.add("ct_san_expansion:walmart.com")
        a1.evidence.append(
            EvidenceRecord(
                source_type="ct_san_expansion",
                description="Found on same cert as seed domain walmart.com",
                seed_domain="walmart.com",
            )
        )
        a1.resolves = True
        evidence["walmartlabs.com"] = a1

        # walmartlabs.com also found via samsclub.com seed expansion
        a2 = DomainAccumulator()
        a2.sources.add("ct_san_expansion:samsclub.com")
        a2.evidence.append(
            EvidenceRecord(
                source_type="ct_san_expansion",
                description="Found on same cert as seed domain samsclub.com",
                seed_domain="samsclub.com",
            )
        )
        evidence["walmartlabs.com"].merge(a2)

        # Apply cross-seed boost
        Scout._apply_cross_seed_boost(evidence, ["walmart.com", "samsclub.com"])

        assert "cross_seed_verified" in evidence["walmartlabs.com"].sources
        score = self.scout._score_confidence(
            evidence["walmartlabs.com"], "Walmart", ["walmart.com", "samsclub.com"]
        )
        # 0.90 (cross_seed) + 0.05 (Level 2: resolves + multi_source) = 0.95
        assert score == 0.95

    def test_generali_overlap(self) -> None:
        """Simulate: generali.it and generali.com both find generali.de."""
        evidence: dict[str, DomainAccumulator] = {}

        a = DomainAccumulator()
        a.sources.add("ct_san_expansion:generali.it")
        a.sources.add("ct_san_expansion:generali.com")
        a.sources.add("ct_org_match")
        a.cert_org_names.add("Generali")
        a.resolves = True
        evidence["generali.de"] = a

        Scout._apply_cross_seed_boost(evidence, ["generali.it", "generali.com"])

        assert "cross_seed_verified" in evidence["generali.de"].sources
        score = self.scout._score_confidence(
            evidence["generali.de"], "Generali", ["generali.it", "generali.com"]
        )
        # cross_seed 0.90 base + 0.10 (Level 3: resolves + high_sim + multi_source) = 1.0
        assert score == 1.0

    def test_ma_sold_subsidiary_no_false_cross(self) -> None:
        """Simulate: After M&A, subsidiary domain only found from one seed.

        If Walmart sells ASDA, asda.com might only appear from walmart.com's
        historical certs, not from samsclub.com. No cross-verification boost.
        """
        evidence: dict[str, DomainAccumulator] = {}

        a = DomainAccumulator()
        a.sources.add("ct_san_expansion:walmart.com")
        a.resolves = True
        evidence["asda.com"] = a

        Scout._apply_cross_seed_boost(evidence, ["walmart.com", "samsclub.com"])

        # Only found from one seed, no cross-verification
        assert "cross_seed_verified" not in evidence["asda.com"].sources
        score = self.scout._score_confidence(
            evidence["asda.com"], "Walmart", ["walmart.com", "samsclub.com"]
        )
        assert score == 0.80  # 0.80 + 0.00 (resolves alone is neutral)

    def test_cdn_false_positive_not_cross_verified(self) -> None:
        """CDN domain from ct_seed_related only (no strong sources) is not cross-verified."""
        evidence: dict[str, DomainAccumulator] = {}

        # cloudflare.com found as ct_seed_related from both seeds (just appeared
        # in search results, not actually on same cert)
        a = DomainAccumulator()
        a.sources.add("ct_seed_related:walmart.com")
        a.sources.add("ct_seed_related:samsclub.com")
        a.resolves = True
        evidence["cloudflare.com"] = a

        Scout._apply_cross_seed_boost(evidence, ["walmart.com", "samsclub.com"])

        # No strong source (ct_san_expansion/ct_seed_subdomain), so no cross-verify.
        # Score: 0.40 (ct_seed_related) + 0.00 (Level 1: resolves only) = 0.40,
        # below inclusion threshold.
        assert "cross_seed_verified" not in evidence["cloudflare.com"].sources

    def test_unrelated_domains_not_boosted(self) -> None:
        """Domains only found via org match (no seed tags) get no cross-seed boost."""
        evidence: dict[str, DomainAccumulator] = {}

        a = DomainAccumulator()
        a.sources.add("ct_org_match")
        a.sources.add("dns_guess")
        a.resolves = True
        evidence["walmart.co.uk"] = a

        Scout._apply_cross_seed_boost(evidence, ["walmart.com", "samsclub.com"])
        assert "cross_seed_verified" not in evidence["walmart.co.uk"].sources


# --- Post-Merger/Acquisition edge cases ---


class TestPostMergerAcquisition:
    """Test M&A scenarios: acquired brands, divestitures, pre/post integration."""

    def setup_method(self) -> None:
        self.scout = Scout(config=ScoutConfig())

    def test_acquired_brand_pre_integration_one_seed(self) -> None:
        """Domain only on one seed's certs (pre-integration) gets no cross-verify."""
        a = DomainAccumulator()
        a.sources.add("ct_san_expansion:walmart.com")
        a.resolves = True
        evidence = {"bonobos.com": a}

        Scout._apply_cross_seed_boost(evidence, ["walmart.com", "samsclub.com"])
        assert "cross_seed_verified" not in a.sources
        assert self.scout._score_confidence(a, "Walmart", ["walmart.com", "samsclub.com"]) == 0.80

    def test_divested_entity_historical_certs(self) -> None:
        """Divested subsidiary with mismatched org gets no cross-verify or org boost."""
        a = DomainAccumulator()
        a.sources.add("ct_san_expansion:walmart.com")
        a.cert_org_names.add("ASDA Group")
        a.resolves = True
        evidence = {"asda.com": a}

        Scout._apply_cross_seed_boost(evidence, ["walmart.com", "samsclub.com"])
        assert "cross_seed_verified" not in a.sources
        assert self.scout._score_confidence(a, "Walmart", ["walmart.com", "samsclub.com"]) == 0.80

    def test_acquired_brand_different_source_types(self) -> None:
        """Cross-verification fires across different source types (SAN + subdomain)."""
        a = DomainAccumulator()
        a.sources.add("ct_san_expansion:companya.com")
        a.sources.add("ct_seed_subdomain:companyb.com")
        a.resolves = True
        evidence = {"companyb.com": a}

        Scout._apply_cross_seed_boost(evidence, ["companya.com", "companyb.com"])
        assert "cross_seed_verified" in a.sources
        assert self.scout._score_confidence(a, "CompanyA", ["companya.com", "companyb.com"]) == 0.95


# --- Post-Spin-Off scenarios ---


class TestPostSpinOff:
    """Test corporate spin-off scenarios: shared legacy certs, transition-era domains."""

    def setup_method(self) -> None:
        self.scout = Scout(config=ScoutConfig())

    def test_spinoff_shared_legacy_domain(self) -> None:
        """Shared domain on certs from both post-split seeds gets cross-verified."""
        a = DomainAccumulator()
        a.sources.add("ct_san_expansion:hp.com")
        a.sources.add("ct_san_expansion:hpe.com")
        a.resolves = True
        evidence = {"hpcloud.com": a}

        Scout._apply_cross_seed_boost(evidence, ["hp.com", "hpe.com"])
        assert "cross_seed_verified" in a.sources
        assert self.scout._score_confidence(a, "HP Inc", ["hp.com", "hpe.com"]) == 0.95

    def test_spinoff_child_domain_not_on_parent_certs(self) -> None:
        """Domain only from one post-split seed gets no cross-verify."""
        a = DomainAccumulator()
        a.sources.add("ct_san_expansion:paypal.com")
        a.resolves = False
        evidence = {"paypal-engineering.com": a}

        Scout._apply_cross_seed_boost(evidence, ["ebay.com", "paypal.com"])
        assert "cross_seed_verified" not in a.sources
        assert self.scout._score_confidence(a, "eBay", ["ebay.com", "paypal.com"]) == 0.75

    def test_spinoff_transition_cert_non_resolving(self) -> None:
        """Cross-verified but non-resolving non-seed domain is excluded from output."""
        a = DomainAccumulator()
        a.sources.add("ct_san_expansion:hp.com")
        a.sources.add("ct_san_expansion:hpe.com")
        a.resolves = False
        evidence = {"hp-transition.com": a}

        Scout._apply_cross_seed_boost(evidence, ["hp.com", "hpe.com"])
        assert "cross_seed_verified" in a.sources

        a.confidence = self.scout._score_confidence(a, "HP Inc", ["hp.com", "hpe.com"])
        domains = self.scout._build_output(evidence, ["hp.com", "hpe.com"])
        assert len(domains) == 0

    def test_spinoff_single_seed_with_resolves(self) -> None:
        """Single-seed domain with resolves gets standard score, no cross-verify."""
        a = DomainAccumulator()
        a.sources.add("ct_san_expansion:hpe.com")
        a.resolves = True
        evidence = {"hpe-services.com": a}

        Scout._apply_cross_seed_boost(evidence, ["hp.com", "hpe.com"])
        assert "cross_seed_verified" not in a.sources
        assert self.scout._score_confidence(a, "HP Inc", ["hp.com", "hpe.com"]) == 0.80


# --- Look-alike but different entities ---


class TestLookAlikeDifferentEntities:
    """Test scenarios where similarly-named but unrelated entities share nothing."""

    def setup_method(self) -> None:
        self.scout = Scout(config=ScoutConfig())

    def test_independent_domains_no_cross(self) -> None:
        """Each seed finds only its own domains -- no cross-verification."""
        a1 = DomainAccumulator()
        a1.sources.add("ct_san_expansion:delta.com")
        a1.resolves = True

        a2 = DomainAccumulator()
        a2.sources.add("ct_san_expansion:deltafaucet.com")
        a2.resolves = True

        evidence = {"news.delta.com": a1, "shop.deltafaucet.com": a2}

        Scout._apply_cross_seed_boost(evidence, ["delta.com", "deltafaucet.com"])
        assert "cross_seed_verified" not in a1.sources
        assert "cross_seed_verified" not in a2.sources

    def test_shared_infra_weak_only_not_cross_verified(self) -> None:
        """Shared CDN/provider domain with only ct_seed_related is not cross-verified.

        Without strong sources, weak evidence from multiple seeds stays at low score.
        """
        a = DomainAccumulator()
        a.sources.add("ct_seed_related:delta.com")
        a.sources.add("ct_seed_related:deltafaucet.com")
        a.resolves = True
        evidence = {"cdn-provider.com": a}

        Scout._apply_cross_seed_boost(evidence, ["delta.com", "deltafaucet.com"])
        assert "cross_seed_verified" not in a.sources
        # 0.40 (ct_seed_related) + 0.00 (resolves only = Level 1) = 0.40
        assert (
            self.scout._score_confidence(a, "Delta Air Lines", ["delta.com", "deltafaucet.com"])
            == 0.40
        )

    def test_completely_isolated_seeds(self) -> None:
        """Zero cert overlap between unrelated seeds -- no cross-verification."""
        a1 = DomainAccumulator()
        a1.sources.add("ct_san_expansion:apple.com")
        a1.resolves = True

        a2 = DomainAccumulator()
        a2.sources.add("ct_san_expansion:applehospitality.com")
        a2.resolves = False

        evidence = {"icloud.com": a1, "applehospitality-reit.com": a2}
        seeds = ["apple.com", "applehospitality.com"]

        Scout._apply_cross_seed_boost(evidence, seeds)
        assert "cross_seed_verified" not in a1.sources
        assert "cross_seed_verified" not in a2.sources

        assert self.scout._score_confidence(a1, "Apple Inc", seeds) == 0.80
        assert self.scout._score_confidence(a2, "Apple Inc", seeds) == 0.75


# --- Cross-verification edge cases ---


class TestCrossVerificationEdgeCases:
    """Boundary conditions and mechanical edge cases in cross-seed logic."""

    def setup_method(self) -> None:
        self.scout = Scout(config=ScoutConfig())

    def test_empty_evidence_dict(self) -> None:
        """Empty evidence dict does not error."""
        evidence: dict[str, DomainAccumulator] = {}
        Scout._apply_cross_seed_boost(evidence, ["a.com", "b.com"])
        assert evidence == {}

    def test_single_domain_five_seeds(self) -> None:
        """Domain found by all 5 seeds gets cross-verified to 1.0."""
        a = DomainAccumulator()
        seeds = ["a.com", "b.com", "c.com", "d.com", "e.com"]
        for seed in seeds:
            a.sources.add(f"ct_san_expansion:{seed}")
        a.resolves = True
        evidence = {"shared.com": a}

        Scout._apply_cross_seed_boost(evidence, seeds)
        assert "cross_seed_verified" in a.sources
        assert self.scout._score_confidence(a, "TestCo", seeds) == 0.95

    def test_duplicate_seed_no_cross_verify(self) -> None:
        """Same seed listed twice contributes only 1 unique seed -- no cross-verify."""
        a = DomainAccumulator()
        a.sources.add("ct_san_expansion:walmart.com")
        a.sources.add("ct_seed_subdomain:walmart.com")
        evidence = {"example.com": a}

        Scout._apply_cross_seed_boost(evidence, ["walmart.com", "walmart.com"])
        assert "cross_seed_verified" not in a.sources

    def test_boost_idempotency(self) -> None:
        """Second boost call is idempotent for sources but appends to evidence list."""
        a = DomainAccumulator()
        a.sources.add("ct_san_expansion:a.com")
        a.sources.add("ct_san_expansion:b.com")
        evidence = {"shared.com": a}

        Scout._apply_cross_seed_boost(evidence, ["a.com", "b.com"])
        evidence_count_after_first = len(a.evidence)

        Scout._apply_cross_seed_boost(evidence, ["a.com", "b.com"])
        assert "cross_seed_verified" in a.sources
        assert len(a.evidence) == evidence_count_after_first + 1

    def test_all_boosts_cap_at_one(self) -> None:
        """Every possible boost stacked still caps at exactly 1.0."""
        a = DomainAccumulator()
        a.sources.update(
            {
                "cross_seed_verified",
                "ct_org_match",
                "ct_san_expansion:walmart.com",
                "ct_san_expansion:samsclub.com",
            }
        )
        a.cert_org_names.add("Walmart Inc.")
        a.resolves = True

        assert self.scout._score_confidence(a, "Walmart", ["walmart.com", "samsclub.com"]) == 1.0

    def test_seed_domain_own_tag_only_is_seed_not_cross_verified(self) -> None:
        """Seed domain with only its own tag: no cross-verify, but is_seed in output."""
        a = DomainAccumulator()
        a.sources.add("ct_seed_subdomain:walmart.com")
        a.resolves = True
        evidence = {"walmart.com": a}
        seeds = ["walmart.com", "samsclub.com"]

        Scout._apply_cross_seed_boost(evidence, seeds)
        assert "cross_seed_verified" not in a.sources

        a.confidence = self.scout._score_confidence(a, "Walmart", seeds)
        domains = self.scout._build_output(evidence, seeds)
        assert len(domains) == 1
        assert domains[0].is_seed is True

    def test_seed_domain_cross_verified_from_other_seed(self) -> None:
        """Seed domain with tags from both seeds gets cross-verified."""
        a = DomainAccumulator()
        a.sources.add("ct_seed_subdomain:walmart.com")
        a.sources.add("ct_san_expansion:samsclub.com")
        a.resolves = True
        evidence = {"walmart.com": a}

        Scout._apply_cross_seed_boost(evidence, ["walmart.com", "samsclub.com"])
        assert "cross_seed_verified" in a.sources
        assert self.scout._score_confidence(a, "Walmart", ["walmart.com", "samsclub.com"]) == 0.95

    def test_extract_contributing_seeds_filters_non_tagged(self) -> None:
        """_extract_contributing_seeds ignores non-seed-tagged sources."""
        sources = {
            "ct_san_expansion:walmart.com",
            "ct_seed_related:samsclub.com",
            "ct_org_match",
            "dns_guess",
            "cross_seed_verified",
            "shared_infra",
        }
        assert _extract_contributing_seeds(sources) == {"walmart.com", "samsclub.com"}


# --- Build output edge cases ---


class TestBuildOutputEdgeCases:
    """Edge cases in _build_output filtering and sorting."""

    def setup_method(self) -> None:
        self.scout = Scout(config=ScoutConfig())

    def test_non_resolving_cross_verified_excluded(self) -> None:
        """Non-resolving non-seed domain excluded even with high confidence."""
        a = DomainAccumulator()
        a.sources.update(
            {
                "ct_san_expansion:a.com",
                "ct_san_expansion:b.com",
                "cross_seed_verified",
            }
        )
        a.resolves = False
        a.confidence = 1.0
        evidence = {"dead-domain.com": a}

        domains = self.scout._build_output(evidence, ["a.com", "b.com"])
        assert len(domains) == 0

    def test_below_inclusion_threshold_excluded(self) -> None:
        """Domain below 0.60 inclusion threshold excluded even if it resolves."""
        a = DomainAccumulator()
        a.sources.add("dns_guess")
        a.resolves = True
        a.confidence = 0.35
        evidence = {"guessed.com": a}

        domains = self.scout._build_output(evidence, [])
        assert len(domains) == 0

    def test_output_sorts_descending_by_confidence(self) -> None:
        """Output is sorted high-to-low by confidence."""
        low = DomainAccumulator()
        low.sources.add("ct_org_match")
        low.resolves = True
        low.confidence = 0.70

        high = DomainAccumulator()
        high.sources.add("cross_seed_verified")
        high.resolves = True
        high.confidence = 1.0

        mid = DomainAccumulator()
        mid.sources.add("ct_san_expansion:a.com")
        mid.resolves = True
        mid.confidence = 0.85

        evidence = {"low.com": low, "high.com": high, "mid.com": mid}

        domains = self.scout._build_output(evidence, ["a.com", "b.com"])
        assert [d.domain for d in domains] == ["high.com", "mid.com", "low.com"]


# --- Corroboration level tests ---


class TestCorroborationLevels:
    """Test the corroboration level scoring model."""

    def setup_method(self) -> None:
        self.scout = Scout(config=ScoutConfig())

    def test_level3_resolves_rdap_high_sim(self) -> None:
        """Level 3: resolves + rdap_match + high_sim → +0.10."""
        a = DomainAccumulator()
        a.sources.update({"ct_org_match", "rdap_registrant_match", "ct_san_expansion:a.com"})
        a.cert_org_names.add("Walmart Inc.")
        a.resolves = True
        score = self.scout._score_confidence(a, "Walmart", ["a.com"])
        assert score == 0.95  # 0.85 + 0.10

    def test_level3_resolves_rdap_multi_source(self) -> None:
        """Level 3: resolves + rdap_match + multi_source → +0.10."""
        a = DomainAccumulator()
        a.sources.update({"ct_org_match", "rdap_registrant_match", "ct_san_expansion:a.com"})
        a.resolves = True
        score = self.scout._score_confidence(a, "Walmart", ["a.com"])
        assert score == 0.95  # 0.85 + 0.10

    def test_level2_resolves_rdap(self) -> None:
        """Level 2: resolves + rdap_match (no multi_source) → +0.05."""
        a = DomainAccumulator()
        a.sources.update({"ct_org_match", "rdap_registrant_match"})
        a.resolves = True
        score = self.scout._score_confidence(a, "Walmart", [])
        assert score == 0.90  # 0.85 + 0.05

    def test_level2_resolves_high_sim(self) -> None:
        """Level 2: resolves + high_sim (no rdap) → +0.05."""
        a = DomainAccumulator()
        a.sources.add("ct_org_match")
        a.cert_org_names.add("Walmart Inc.")
        a.resolves = True
        score = self.scout._score_confidence(a, "Walmart", [])
        assert score == 0.90  # 0.85 + 0.05

    def test_level2_resolves_multi_source(self) -> None:
        """Level 2: resolves + multi_source (no rdap, no high_sim) → +0.05."""
        a = DomainAccumulator()
        a.sources.update({"ct_org_match", "ct_san_expansion:a.com", "ct_seed_subdomain:a.com"})
        a.resolves = True
        score = self.scout._score_confidence(a, "Walmart", ["a.com"])
        assert score == 0.90  # 0.85 + 0.05

    def test_level1_resolves_only(self) -> None:
        """Level 1: resolves only → +0.00."""
        a = DomainAccumulator()
        a.sources.add("ct_org_match")
        a.resolves = True
        score = self.scout._score_confidence(a, "Walmart", [])
        assert score == 0.85  # 0.85 + 0.00

    def test_level0_no_resolves(self) -> None:
        """Level 0: no resolution → -0.05."""
        a = DomainAccumulator()
        a.sources.add("ct_org_match")
        a.resolves = False
        score = self.scout._score_confidence(a, "Walmart", [])
        assert score == 0.80  # 0.85 - 0.05

    def test_cross_seed_rdap_resolves_reaches_one(self) -> None:
        """Cross-seed + rdap + resolves → 1.0."""
        a = DomainAccumulator()
        a.sources.update(
            {
                "cross_seed_verified",
                "ct_san_expansion:a.com",
                "ct_san_expansion:b.com",
                "rdap_registrant_match",
            }
        )
        a.resolves = True
        score = self.scout._score_confidence(a, "TestCo", ["a.com", "b.com"])
        # 0.90 + 0.10 (Level 3: resolves + rdap + multi_source) = 1.0
        assert score == 1.0

    def test_dns_guess_stays_at_030(self) -> None:
        """dns_guess stays at 0.30 regardless of corroboration."""
        a = DomainAccumulator()
        a.sources.add("dns_guess")
        a.resolves = True
        score = self.scout._score_confidence(a, "Walmart", [])
        assert score == 0.30


# --- RDAP corroboration pipeline tests ---


class TestRDAPCorroboration:
    """Test the _rdap_corroborate pipeline step."""

    def setup_method(self) -> None:
        self.scout = Scout(config=ScoutConfig())
        self.scout._rdap.get_registrant_org = AsyncMock(return_value="Walmart Inc.")  # type: ignore[method-assign]

    @pytest.mark.asyncio
    async def test_adds_rdap_source_on_match(self) -> None:
        """RDAP corroboration adds rdap_registrant_match when org matches."""
        evidence: dict[str, DomainAccumulator] = {}
        a = DomainAccumulator()
        a.sources.add("ct_org_match")
        a.resolves = True
        evidence["walmart.com"] = a

        await self.scout._rdap_corroborate(evidence, "Walmart")
        assert "rdap_registrant_match" in a.sources
        assert a.rdap_org == "Walmart Inc."
        assert any(e.source_type == "rdap_registrant_match" for e in a.evidence)

    @pytest.mark.asyncio
    async def test_skips_non_resolving(self) -> None:
        """Non-resolving domains are skipped."""
        evidence: dict[str, DomainAccumulator] = {}
        a = DomainAccumulator()
        a.sources.add("ct_org_match")
        a.resolves = False
        evidence["dead.com"] = a

        await self.scout._rdap_corroborate(evidence, "Walmart")
        assert "rdap_registrant_match" not in a.sources

    @pytest.mark.asyncio
    async def test_below_threshold_no_source(self) -> None:
        """RDAP org below org_match_threshold doesn't add source."""
        self.scout._rdap.get_registrant_org = AsyncMock(return_value="Totally Different Corp")  # type: ignore[method-assign]
        evidence: dict[str, DomainAccumulator] = {}
        a = DomainAccumulator()
        a.sources.add("ct_org_match")
        a.resolves = True
        evidence["unrelated.com"] = a

        await self.scout._rdap_corroborate(evidence, "Walmart")
        assert "rdap_registrant_match" not in a.sources

    @pytest.mark.asyncio
    async def test_exception_handled_gracefully(self) -> None:
        """Exception during RDAP corroboration doesn't crash."""
        self.scout._rdap.get_registrant_org = AsyncMock(  # type: ignore[method-assign]
            side_effect=Exception("RDAP service down")
        )
        evidence: dict[str, DomainAccumulator] = {}
        a = DomainAccumulator()
        a.sources.add("ct_org_match")
        a.resolves = True
        evidence["walmart.com"] = a

        # Should not raise
        await self.scout._rdap_corroborate(evidence, "Walmart")
        # Source should NOT be added when RDAP fails
        assert "rdap_registrant_match" not in a.sources

    @pytest.mark.asyncio
    async def test_respects_max_limit(self) -> None:
        """Only checks up to rdap_corroborate_max domains."""
        config = ScoutConfig(rdap_corroborate_max=3)
        self.scout = Scout(config=config)
        self.scout._rdap.get_registrant_org = AsyncMock(return_value="Walmart Inc.")  # type: ignore[method-assign]

        evidence: dict[str, DomainAccumulator] = {}
        for i in range(10):
            a = DomainAccumulator()
            a.sources.add("ct_org_match")
            a.resolves = True
            evidence[f"domain{i}.com"] = a

        await self.scout._rdap_corroborate(evidence, "Walmart")
        assert self.scout._rdap.get_registrant_org.call_count == 3
