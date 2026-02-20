"""Tests for ScoutConfig and profile management."""

from __future__ import annotations

import pytest

from domain_scout.config import ScoutConfig


class TestScoutConfig:
    def test_defaults(self) -> None:
        """Default config matches 'balanced' profile defaults."""
        config = ScoutConfig()
        # Check a few defaults
        assert config.org_match_threshold == 0.65
        assert config.inclusion_threshold == 0.6
        assert config.seed_confirm_threshold == 0.6
        assert config.include_non_resolving is False
        assert config.rdap_corroborate_max == 10

    def test_from_profile_broad(self) -> None:
        """'broad' profile lowers thresholds and enables non-resolving."""
        config = ScoutConfig.from_profile("broad")
        assert config.org_match_threshold == 0.50
        assert config.inclusion_threshold == 0.40
        assert config.seed_confirm_threshold == 0.45
        assert config.include_non_resolving is True
        assert config.rdap_corroborate_max == 15

    def test_from_profile_balanced(self) -> None:
        """'balanced' profile is identical to defaults."""
        config = ScoutConfig.from_profile("balanced")
        assert config == ScoutConfig()

    def test_from_profile_strict(self) -> None:
        """'strict' profile raises thresholds."""
        config = ScoutConfig.from_profile("strict")
        assert config.org_match_threshold == 0.80
        assert config.inclusion_threshold == 0.75
        assert config.seed_confirm_threshold == 0.75
        assert config.rdap_corroborate_max == 20
        # Should still default to False for non-resolving
        assert config.include_non_resolving is False

    def test_from_profile_unknown(self) -> None:
        """Unknown profile name raises ValueError."""
        with pytest.raises(ValueError, match="Unknown profile: 'mega-strict'"):
            ScoutConfig.from_profile("mega-strict")  # type: ignore[arg-type]

    def test_from_profile_overrides(self) -> None:
        """Overrides take precedence over profile defaults."""
        config = ScoutConfig.from_profile(
            "broad",
            org_match_threshold=0.99,
            include_non_resolving=False,
        )
        # Overridden
        assert config.org_match_threshold == 0.99
        assert config.include_non_resolving is False
        # Inherited from 'broad'
        assert config.inclusion_threshold == 0.40

    def test_from_profile_overrides_unknown_arg(self) -> None:
        """Passing unknown arguments to overrides raises TypeError."""
        with pytest.raises(TypeError):
            ScoutConfig.from_profile("balanced", non_existent_param=123)

    def test_to_dict(self) -> None:
        """to_dict returns a dictionary with all fields."""
        config = ScoutConfig(org_match_threshold=0.123)
        data = config.to_dict()
        assert isinstance(data, dict)
        assert data["org_match_threshold"] == 0.123
        assert "dns_nameservers" in data
        assert "guess_tlds" in data
