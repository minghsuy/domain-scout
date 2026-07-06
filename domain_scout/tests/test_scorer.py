"""Unit tests for the scorer module."""

from __future__ import annotations

from typing import Any

import pytest
from structlog.testing import capture_logs

from domain_scout import scorer
from domain_scout.scorer import _clean_name

# A degenerate calibration curve that maps every probability to exactly 0.5:
# if a score comes back 0.5 the calibration layer ran, otherwise it was skipped.
_FLAT_CALIBRATION = {"x": [0.0, 1.0], "y": [0.5, 0.5]}


def _score_once() -> float:
    """Score one fixed, evidence-rich domain through the loaded model."""
    return scorer.score_confidence(
        domain="acme.com",
        company_name="Acme Corp",
        best_similarity=0.9,
        sources={"ct_org_match"},
        cert_org_names={"Acme Corp"},
        resolves=True,
        evidence_count=3,
        unique_cert_count=2,
    )


def _artifact(**overrides: Any) -> dict[str, Any]:
    """Fresh copy of the shipped artifact with optional top-level overrides."""
    model = scorer._load_model()
    model.update(overrides)
    return model


@pytest.mark.parametrize(
    ("input_name", "expected"),
    [
        ("Apple Inc", "apple"),
        ("Microsoft Corp.", "microsoft"),
        ("Stripe, Inc.", "stripe"),
        ("A B C Data", "data"),
        ("TeStinG LLC", "testing"),
        ("   ", ""),
        ("Data    Systems", "data systems"),
        ("The Company Inc", ""),
        ("Foo Ltd, Bar LLC", "foo bar"),
        ("a", ""),
        ("ab", ""),
    ],
)
def test_clean_name(input_name: str, expected: str) -> None:
    assert _clean_name(input_name) == expected


def test_scorer_identity_constants() -> None:
    """The learned scorer exposes a stable id and an artifact-derived version."""
    from domain_scout.scorer import SCORER_ID, scorer_version

    assert SCORER_ID == "learned_lr"
    # Pin the shipped artifact so a silent retrain can't reuse an old identity.
    # The +uncal suffix records that the acceptance gate disabled this
    # artifact's calibration layer (lr_calibrated_ece 0.2182 > lr_ece 0.0072),
    # so raw-LR probabilities never share an identity with calibrated ones.
    assert scorer_version() == "v1@2026-03-01+uncal"


# ---------------------------------------------------------------------------
# Calibration acceptance gate (issue #183)
# ---------------------------------------------------------------------------


class TestCalibrationGate:
    def test_shipped_artifact_gates_calibration_off(self) -> None:
        """The shipped artifact's own metrics reject its calibration layer."""
        model = scorer._validate_artifact(scorer._load_model())
        assert model["metrics"]["lr_calibrated_ece"] > model["metrics"]["lr_ece"]
        assert model["_calibration_active"] is False

    def test_bad_calibration_skipped_at_scoring(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With calibrated ECE worse than raw, the flat curve must NOT run."""
        model = _artifact(calibration=_FLAT_CALIBRATION)
        # Shipped metrics already say calibration is worse (0.2182 > 0.0072).
        monkeypatch.setattr(scorer, "_MODEL", scorer._validate_artifact(model))

        prob = _score_once()
        assert prob != 0.5  # flat curve would have mapped everything to 0.5
        assert 0.0 < prob < 1.0

    def test_good_calibration_applied(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A future artifact whose metrics show good calibration gets it back."""
        model = _artifact(calibration=_FLAT_CALIBRATION)
        model["metrics"]["lr_calibrated_ece"] = 0.001  # better than lr_ece 0.0072
        monkeypatch.setattr(scorer, "_MODEL", scorer._validate_artifact(model))

        assert _score_once() == 0.5

    def test_missing_ece_metrics_keep_calibration(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No ECE evidence -> no gate: pre-#183 behavior (calibration applied)."""
        model = _artifact(calibration=_FLAT_CALIBRATION, metrics={})
        monkeypatch.setattr(scorer, "_MODEL", scorer._validate_artifact(model))

        assert scorer._MODEL is not None
        assert scorer._MODEL["_calibration_active"] is True
        assert _score_once() == 0.5

    def test_partial_ece_metrics_keep_calibration(self) -> None:
        """Only one of the two ECE metrics present -> no gate (no comparison)."""
        model = _artifact(calibration=_FLAT_CALIBRATION)
        del model["metrics"]["lr_calibrated_ece"]
        assert scorer._validate_artifact(model)["_calibration_active"] is True

        model = _artifact(calibration=_FLAT_CALIBRATION)
        del model["metrics"]["lr_ece"]
        assert scorer._validate_artifact(model)["_calibration_active"] is True

    def test_gate_logs_once_at_load_not_per_domain(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(scorer, "_MODEL", None)
        with capture_logs() as logs:
            scorer._get_model()
            events = [entry["event"] for entry in logs]
            assert events.count("scorer_calibration_gated_off") == 1

            load_time_count = len(logs)
            _score_once()
            _score_once()
            assert len(logs) == load_time_count  # scoring adds no log entries

    def test_gate_warning_carries_artifact_metrics(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(scorer, "_MODEL", None)
        with capture_logs() as logs:
            scorer._get_model()
        (entry,) = [e for e in logs if e["event"] == "scorer_calibration_gated_off"]
        assert entry["artifact"] == "v1@2026-03-01"
        assert entry["lr_ece"] == 0.0072
        assert entry["lr_calibrated_ece"] == 0.2182


# ---------------------------------------------------------------------------
# Feature-availability contract (issue #183)
# ---------------------------------------------------------------------------


class TestFeatureAvailabilityContract:
    def test_declared_zero_filled_feature_warned_with_bias(self) -> None:
        """org_matches_different_entity is declared inference-unavailable."""
        with capture_logs() as logs:
            scorer._validate_artifact(scorer._load_model())

        (entry,) = [e for e in logs if e["event"] == "scorer_feature_zero_filled"]
        assert entry["feature"] == "org_matches_different_entity"
        assert entry["coefficient"] == pytest.approx(0.2044, abs=1e-4)
        # Positive coefficient + zero-fill -> scores biased low where the
        # signal would have been 1.
        assert "biased low" in entry["bias"]
        assert entry["zero_fill_z_offset"] == pytest.approx(-0.0201, abs=1e-4)

    def test_undeclared_zero_filled_feature_warns_as_undeclared(self) -> None:
        model = scorer._load_model()
        del model["inference_unavailable_features"]
        with capture_logs() as logs:
            scorer._validate_artifact(model)

        events = [e["event"] for e in logs]
        assert "scorer_feature_availability_undeclared" in events
        assert "scorer_feature_zero_filled" not in events

    def test_unknown_feature_rejected_at_load(self) -> None:
        """A feature the scorer can't even zero-fill fails loud at load."""
        model = scorer._load_model()
        model["features"] = [*model["features"], "bogus_feature"]
        with pytest.raises(ValueError, match="bogus_feature"):
            scorer._validate_artifact(model)


# ---------------------------------------------------------------------------
# Version stamp composition (issue #183 x #185)
# ---------------------------------------------------------------------------


class TestScorerVersionStamp:
    def test_gated_off_calibration_suffixes_version(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(scorer, "_MODEL", None)  # force reload of shipped artifact
        assert scorer.scorer_version() == "v1@2026-03-01+uncal"

    def test_accepted_calibration_keeps_plain_version(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        model = _artifact(calibration=_FLAT_CALIBRATION)
        model["metrics"]["lr_calibrated_ece"] = 0.001
        monkeypatch.setattr(scorer, "_MODEL", scorer._validate_artifact(model))
        assert scorer.scorer_version() == "v1@2026-03-01"

    def test_scout_stamps_gated_identity(self) -> None:
        """The #185 stamp on scored domains reflects the path that actually ran:
        raw-LR (gated) probabilities are stamped +uncal, composing with #183."""
        from domain_scout.config import ScoutConfig
        from domain_scout.scout import Scout, _DomainAccum

        accum = _DomainAccum()
        accum.sources = {"ct_org_match"}
        accum.cert_org_names = {"TestCo"}
        accum.resolves = True
        scout = Scout(config=ScoutConfig(use_learned_scorer=True))
        scout._score_confidence(accum, "TestCo", ["test.com"], domain="example.com")
        assert accum.scorer_id == "learned_lr"
        assert accum.scorer_version == "v1@2026-03-01+uncal"
