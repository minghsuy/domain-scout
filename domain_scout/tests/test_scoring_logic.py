"""Unit tests for _score_confidence in scout.py."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch
from domain_scout.config import ScoutConfig
from domain_scout.models import EvidenceRecord
from domain_scout.scout import Scout, _DomainAccum

class TestScoutScoreConfidence:
    def setup_method(self) -> None:
        self.scout = Scout(config=ScoutConfig())

    # --- Heuristic Base Scores ---

    def test_base_score_cross_seed_verified(self) -> None:
        accum = _DomainAccum()
        accum.sources.add("cross_seed_verified")
        # Base 0.90, No resolution -> Level 0 (-0.05) = 0.85
        assert self.scout._score_confidence(accum, "Test", []) == 0.85

    def test_base_score_ct_org_match(self) -> None:
        accum = _DomainAccum()
        accum.sources.add("ct_org_match")
        # Base 0.85, No resolution -> Level 0 (-0.05) = 0.80
        assert self.scout._score_confidence(accum, "Test", []) == 0.80

    def test_base_score_ct_subsidiary_match(self) -> None:
        accum = _DomainAccum()
        accum.sources.add("ct_subsidiary_match")
        # Base 0.80, No resolution -> Level 0 (-0.05) = 0.75
        assert self.scout._score_confidence(accum, "Test", []) == 0.75

    def test_base_score_ct_san_expansion(self) -> None:
        accum = _DomainAccum()
        accum.sources.add("ct_san_expansion:seed.com")
        # Base 0.80, No resolution -> Level 0 (-0.05) = 0.75
        assert self.scout._score_confidence(accum, "Test", []) == 0.75

    def test_base_score_ct_seed_subdomain(self) -> None:
        accum = _DomainAccum()
        accum.sources.add("ct_seed_subdomain:seed.com")
        # Base 0.75, No resolution -> Level 0 (-0.05) = 0.70
        assert self.scout._score_confidence(accum, "Test", []) == 0.70

    def test_base_score_ct_seed_related(self) -> None:
        accum = _DomainAccum()
        accum.sources.add("ct_seed_related:seed.com")
        # Base 0.40, No resolution -> Level 0 (-0.05) = 0.35
        assert self.scout._score_confidence(accum, "Test", []) == 0.35

    def test_base_score_dns_guess(self) -> None:
        accum = _DomainAccum()
        accum.sources.add("dns_guess")
        # Base 0.30, Bypasses Phase 2 adjustments
        assert self.scout._score_confidence(accum, "Test", []) == 0.30

    # --- Corroboration Level Adjustments ---

    def test_level3_strong_corroboration_rdap(self) -> None:
        """Level 3: resolves + rdap_match + 3+ sources (+0.10)."""
        accum = _DomainAccum()
        accum.sources.update({"ct_org_match", "rdap_registrant_match", "ct_san_expansion:s.com"})
        accum.resolves = True
        # Base 0.85 (ct_org_match) + 0.10 = 0.95
        assert self.scout._score_confidence(accum, "Test", []) == 0.95

    def test_level3_strong_corroboration_high_sim(self) -> None:
        """Level 3: resolves + high_sim + 3+ sources (+0.10)."""
        accum = _DomainAccum()
        accum.sources.update({"ct_org_match", "ct_san_expansion:s.com", "shared_infra"})
        accum.cert_org_names.add("Test Co Inc")
        accum.resolves = True
        # Base 0.85 (ct_org_match), org_name_similarity("Test Co Inc", "Test") > 0.9
        with patch("domain_scout.scout.org_name_similarity", return_value=0.95):
            # Base 0.85 + 0.10 = 0.95
            assert self.scout._score_confidence(accum, "Test", []) == 0.95

    def test_level2_moderate_corroboration_rdap(self) -> None:
        """Level 2: resolves + rdap_match (+0.05)."""
        accum = _DomainAccum()
        accum.sources.update({"ct_org_match", "rdap_registrant_match"})
        accum.resolves = True
        # Base 0.85 + 0.05 = 0.90
        assert self.scout._score_confidence(accum, "Test", []) == 0.90

    def test_level2_moderate_corroboration_multi_source(self) -> None:
        """Level 2: resolves + multi_source (+0.05)."""
        accum = _DomainAccum()
        accum.sources.update({"ct_org_match", "ct_san_expansion:s.com", "shared_infra"})
        accum.resolves = True
        # Base 0.85 + 0.05 = 0.90
        assert self.scout._score_confidence(accum, "Test", []) == 0.90

    def test_level1_resolves_only(self) -> None:
        """Level 1: resolves only (0.00)."""
        accum = _DomainAccum()
        accum.sources.add("ct_org_match")
        accum.resolves = True
        # Base 0.85 + 0.00 = 0.85
        assert self.scout._score_confidence(accum, "Test", []) == 0.85

    def test_level0_no_resolution(self) -> None:
        """Level 0: no resolution (-0.05)."""
        accum = _DomainAccum()
        accum.sources.add("ct_org_match")
        accum.resolves = False
        # Base 0.85 - 0.05 = 0.80
        assert self.scout._score_confidence(accum, "Test", []) == 0.80

    # --- Bypass Logic ---

    def test_adjustment_bypass_low_score(self) -> None:
        """Scores <= 0.30 (like dns_guess) skip Phase 2 adjustments."""
        accum = _DomainAccum()
        accum.sources.add("dns_guess")
        accum.resolves = False # Should get -0.05 if not bypassed
        assert self.scout._score_confidence(accum, "Test", []) == 0.30

    # --- Learned Scorer Bridge ---

    def test_learned_scorer_opt_in(self) -> None:
        config = ScoutConfig(use_learned_scorer=True)
        scout = Scout(config=config)
        accum = _DomainAccum()
        accum.cert_org_names.add("Test Co")
        accum.evidence.append(
            EvidenceRecord(
                source_type="rdap_registrant_match",
                similarity_score=0.98
            )
        )
        accum.evidence.append(EvidenceRecord(source_type="ct_org_match", cert_id=12345, description="test"))

        with patch("domain_scout.scorer.score_confidence") as mock_learned:
            mock_learned.return_value = 0.99
            score = scout._score_confidence(accum, "Test", [], domain="example.com")

            assert score == 0.99
            mock_learned.assert_called_once()
            args = mock_learned.call_args[1]
            assert args["unique_cert_count"] == 1
            assert args["rdap_similarity"] == 0.98
