"""Tests for discovery profiles, RunMetadata, and EvidenceRecord."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from domain_scout.config import ScoutConfig
from domain_scout.models import EvidenceRecord, RunMetadata

# --- Discovery Profiles ---


class TestDiscoveryProfiles:
    def test_balanced_is_default(self) -> None:
        cfg = ScoutConfig.from_profile("balanced")
        default = ScoutConfig()
        assert cfg.org_match_threshold == default.org_match_threshold
        assert cfg.inclusion_threshold == default.inclusion_threshold
        assert cfg.seed_confirm_threshold == default.seed_confirm_threshold
        assert cfg.include_non_resolving == default.include_non_resolving

    def test_broad_lower_thresholds(self) -> None:
        cfg = ScoutConfig.from_profile("broad")
        assert cfg.org_match_threshold == 0.50
        assert cfg.inclusion_threshold == 0.40
        assert cfg.seed_confirm_threshold == 0.45
        assert cfg.include_non_resolving is True

    def test_strict_higher_thresholds(self) -> None:
        cfg = ScoutConfig.from_profile("strict")
        assert cfg.org_match_threshold == 0.80
        assert cfg.inclusion_threshold == 0.75
        assert cfg.seed_confirm_threshold == 0.75

    def test_override_after_profile(self) -> None:
        cfg = ScoutConfig.from_profile("broad", total_timeout=200)
        assert cfg.total_timeout == 200
        # Profile values still applied
        assert cfg.org_match_threshold == 0.50

    def test_broad_includes_non_resolving(self) -> None:
        cfg = ScoutConfig.from_profile("broad")
        assert cfg.include_non_resolving is True
        # Default and strict do not
        assert ScoutConfig().include_non_resolving is False
        assert ScoutConfig.from_profile("strict").include_non_resolving is False

    def test_invalid_profile_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown profile"):
            ScoutConfig.from_profile("aggressive")  # type: ignore[arg-type]


# --- RunMetadata ---


class TestRunMetadata:
    def test_schema_version(self) -> None:
        meta = RunMetadata(
            tool_version="0.2.0",
            timestamp=datetime.now(UTC),
            elapsed_seconds=1.0,
            domains_found=5,
        )
        assert meta.schema_version == "1.0"

    def test_config_snapshot(self) -> None:
        cfg = ScoutConfig(total_timeout=120)
        meta = RunMetadata(
            tool_version="0.2.0",
            timestamp=datetime.now(UTC),
            elapsed_seconds=2.5,
            domains_found=10,
            config=cfg.to_dict(),
        )
        assert meta.config["total_timeout"] == 120
        assert "org_match_threshold" in meta.config

    def test_defaults(self) -> None:
        meta = RunMetadata(
            tool_version="0.2.0",
            timestamp=datetime.now(UTC),
            elapsed_seconds=0.0,
            domains_found=0,
        )
        assert meta.timed_out is False
        assert meta.seed_count == 0
        assert meta.errors == []
        assert meta.config == {}


# --- EvidenceRecord ---


class TestEvidenceRecord:
    def test_minimal_record(self) -> None:
        rec = EvidenceRecord(source_type="dns_guess", description="Guessed")
        assert rec.seed_domain is None
        assert rec.cert_id is None
        assert rec.cert_org is None
        assert rec.similarity_score is None

    def test_full_record(self) -> None:
        rec = EvidenceRecord(
            source_type="ct_org_match",
            description="Cert org 'Acme' matches target",
            cert_id=12345,
            cert_org="Acme",
            similarity_score=0.95,
            seed_domain="acme.com",
        )
        assert rec.cert_id == 12345
        assert rec.similarity_score == 0.95

    def test_serialization_roundtrip(self) -> None:
        rec = EvidenceRecord(
            source_type="ct_san_expansion",
            description="Found on same cert",
            seed_domain="example.com",
        )
        data = rec.model_dump()
        restored = EvidenceRecord.model_validate(data)
        assert restored == rec


# --- Config.to_dict ---


class TestConfigToDict:
    def test_to_dict_has_all_fields(self) -> None:
        cfg = ScoutConfig()
        d = cfg.to_dict()
        assert "total_timeout" in d
        assert "org_match_threshold" in d
        assert "include_non_resolving" in d
        assert d["include_non_resolving"] is False

    def test_to_dict_reflects_profile(self) -> None:
        cfg = ScoutConfig.from_profile("broad")
        d = cfg.to_dict()
        assert d["org_match_threshold"] == 0.50
        assert d["include_non_resolving"] is True
