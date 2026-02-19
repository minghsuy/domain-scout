"""Unit tests for ScoutConfig configuration management."""

from __future__ import annotations

import pytest

from domain_scout.config import ScoutConfig


class TestScoutConfig:
    def test_scout_config_defaults(self) -> None:
        """Verify that ScoutConfig() has expected default values."""
        config = ScoutConfig()
        assert config.postgres_timeout == 15
        assert config.http_timeout == 15
        assert config.dns_timeout == 5.0
        assert config.total_timeout == 90
        assert config.dns_nameservers == ["8.8.8.8", "1.1.1.1"]
        assert config.org_match_threshold == 0.65
        assert config.inclusion_threshold == 0.6
        assert config.include_non_resolving is False

    def test_scout_config_to_dict(self) -> None:
        """Verify that to_dict() returns a dictionary representation."""
        config = ScoutConfig()
        data = config.to_dict()
        assert isinstance(data, dict)
        assert data["postgres_timeout"] == 15
        assert data["dns_nameservers"] == ["8.8.8.8", "1.1.1.1"]
        assert data["org_match_threshold"] == 0.65

    def test_scout_config_from_profile_balanced(self) -> None:
        """Verify that 'balanced' profile uses default values."""
        config = ScoutConfig.from_profile("balanced")
        assert config.org_match_threshold == 0.65
        assert config.inclusion_threshold == 0.6
        assert config.include_non_resolving is False

    def test_scout_config_from_profile_broad(self) -> None:
        """Verify that 'broad' profile overrides specific fields."""
        config = ScoutConfig.from_profile("broad")
        assert config.org_match_threshold == 0.50
        assert config.inclusion_threshold == 0.40
        assert config.include_non_resolving is True
        assert config.rdap_corroborate_max == 15

    def test_scout_config_from_profile_strict(self) -> None:
        """Verify that 'strict' profile overrides specific fields."""
        config = ScoutConfig.from_profile("strict")
        assert config.org_match_threshold == 0.80
        assert config.inclusion_threshold == 0.75
        assert config.rdap_corroborate_max == 20

    def test_scout_config_from_profile_overrides(self) -> None:
        """Verify that overrides passed to from_profile take precedence."""
        config = ScoutConfig.from_profile("balanced", postgres_timeout=99, org_match_threshold=0.99)
        assert config.postgres_timeout == 99
        assert config.org_match_threshold == 0.99
        # Other values should still be from profile/default
        assert config.http_timeout == 15

    def test_scout_config_from_profile_invalid(self) -> None:
        """Verify that unknown profile raises ValueError."""
        with pytest.raises(ValueError, match="Unknown profile: 'invalid'"):
            ScoutConfig.from_profile("invalid")  # type: ignore[arg-type]
