"""Learned confidence scorer for domain-entity attribution.

Loads a logistic regression model from a JSON artifact and produces
probabilities from domain evidence features. The artifact's own persisted
metrics are consumed at load time (issue #183): the isotonic calibration
layer is only applied when the artifact's metadata says it improves ECE,
and features the artifact uses but inference cannot supply are surfaced
with their bias direction.
"""

from __future__ import annotations

import json
import math
from importlib import resources
from typing import Any

import structlog

log = structlog.get_logger()

SCORER_ID = "learned_lr"

# Features score_confidence derives from real inference-time evidence.
_SUPPLIED_FEATURES = frozenset(
    {
        "best_similarity",
        "source_count",
        "domain_has_company_token",
        "has_shared_infra",
        "has_dns_guess",
        "tld_is_country",
        "entity_name_in_org",
        "evidence_density",
        "resolves",
        "domain_length",
        "rdap_similarity",
    }
)

# Train-time-only features: score_confidence zero-fills these because the
# underlying data is not available at inference (see the raw dict below).
_ZERO_FILLED_FEATURES = frozenset(
    {
        "org_matches_different_entity",  # requires S&P 500 entity data
        "same_asn_as_anchor",  # requires DNS+ASN lookup
        "asn_is_cdn",  # requires DNS+ASN lookup
        "shares_nameserver",  # requires NS lookup
    }
)


def _load_model() -> dict[str, Any]:
    """Load the default model artifact shipped with the package."""
    ref = resources.files(__package__).joinpath("logistic_v1.json")
    result: dict[str, Any] = json.loads(ref.read_text(encoding="utf-8"))
    return result


def _validate_artifact(model: dict[str, Any]) -> dict[str, Any]:
    """Inference-time acceptance gate: consume the artifact's own metadata.

    Called once at load (the result is cached in ``_MODEL``), so every
    warning here fires once per process, not per scored domain.

    Two contracts are enforced:

    1. Feature availability — every feature the model uses must either be
       genuinely computable at inference (``_SUPPLIED_FEATURES``) or a known
       zero-filled placeholder (``_ZERO_FILLED_FEATURES``). Zero-filled
       features should be declared by the artifact in
       ``inference_unavailable_features``; declared or not, the constant
       train/serve bias is logged with its direction.

    2. Calibration acceptance — the calibration layer is applied only when
       the artifact's own metrics say it helps (``lr_calibrated_ece <=
       lr_ece``). A future artifact with good calibration automatically gets
       the layer back; nothing is hardcoded to this artifact.
    """
    features: list[str] = model["features"]

    # -- Feature-availability contract --------------------------------------
    declared_unavailable = set(model.get("inference_unavailable_features", []))
    for i, fname in enumerate(features):
        if fname in _SUPPLIED_FEATURES:
            continue
        if fname not in _ZERO_FILLED_FEATURES:
            raise ValueError(
                f"model artifact uses feature {fname!r} which inference cannot"
                " compute; add it to score_confidence or retrain without it"
            )
        coef = float(model["coefficients"][fname])
        mean = float(model["scaler"]["mean"][i])
        scale = float(model["scaler"]["scale"][i])
        bias_direction = "low" if coef > 0 else "high"
        event = (
            "scorer_feature_zero_filled"
            if fname in declared_unavailable
            else "scorer_feature_availability_undeclared"
        )
        log.warning(
            event,
            feature=fname,
            coefficient=round(coef, 4),
            # Constant z-space offset every domain gets vs. the training mean.
            zero_fill_z_offset=round(coef * (0.0 - mean) / scale, 4),
            bias=f"confidence biased {bias_direction} for domains where {fname}=1",
        )

    # -- Calibration acceptance gate -----------------------------------------
    metrics = model.get("metrics", {})
    ece = metrics.get("lr_ece")
    calibrated_ece = metrics.get("lr_calibrated_ece")
    calibration_active = bool(model.get("calibration"))
    if (
        calibration_active
        and ece is not None
        and calibrated_ece is not None
        and calibrated_ece > ece
    ):
        calibration_active = False
        log.warning(
            "scorer_calibration_gated_off",
            artifact=f"{model['version']}@{model['training_date']}",
            lr_ece=ece,
            lr_calibrated_ece=calibrated_ece,
            reason=("artifact metrics say calibration worsens ECE; using raw LR probabilities"),
        )
    model["_calibration_active"] = calibration_active
    return model


_MODEL: dict[str, Any] | None = None


def _get_model() -> dict[str, Any]:
    global _MODEL  # noqa: PLW0603
    if _MODEL is None:
        _MODEL = _validate_artifact(_load_model())
    return _MODEL


# ---------------------------------------------------------------------------
# Feature helpers (mirrored from training pipeline build_training_data.py)
# ---------------------------------------------------------------------------

_COMPANY_SUFFIX_SKIP = frozenset(
    {
        "inc",
        "corp",
        "ltd",
        "llc",
        "plc",
        "company",
        "group",
        "holdings",
        "the",
        "and",
        "co",
        "sa",
        "se",
        "nv",
        "ag",
        "ab",
    }
)

# ISO 3166-1 alpha-2 country codes for TLD check
_CC_CODES = (
    "ac ad ae af ag ai al am ao aq ar as at au aw ax az "
    "ba bb bd be bf bg bh bi bj bl bm bn bo bq br bs bt bv bw by bz "
    "ca cc cd cf cg ch ci ck cl cm cn co cr cu cv cw cx cy cz "
    "de dj dk dm do dz "
    "ec ee eg eh er es et eu "
    "fi fj fk fm fo fr "
    "ga gb gd ge gf gg gh gi gl gm gn gp gq gr gs gt gu gw gy "
    "hk hm hn hr ht hu "
    "id ie il im in io iq ir is it "
    "je jm jo jp "
    "ke kg kh ki km kn kp kr kw ky kz "
    "la lb lc li lk lr ls lt lu lv ly "
    "ma mc md me mf mg mh mk ml mm mn mo mp mq mr ms mt mu mv mw mx my mz "
    "na nc ne nf ng ni nl no np nr nu nz "
    "om "
    "pa pe pf pg ph pk pl pm pn pr ps pt pw py "
    "qa "
    "re ro rs ru rw "
    "sa sb sc sd se sg sh si sj sk sl sm sn so sr ss st sv sx sy sz "
    "tc td tf tg th tj tk tl tm tn to tr tt tv tw tz "
    "ua ug uk us uy uz "
    "va vc ve vg vi vn vu "
    "wf ws "
    "ye yt "
    "za zm zw"
)
_COUNTRY_TLDS = frozenset("." + cc for cc in _CC_CODES.split())


def _tld_is_country(domain: str) -> int:
    tld = "." + domain.rsplit(".", maxsplit=1)[-1]
    return 1 if tld in _COUNTRY_TLDS else 0


def _domain_has_company_token(domain: str, company_name: str) -> int:
    base = domain.split(".", maxsplit=1)[0].lower()
    tokens = [t.lower().rstrip(".,") for t in company_name.split() if len(t) >= 3]
    tokens = [t for t in tokens if t not in _COMPANY_SUFFIX_SKIP]
    return 1 if any(t in base for t in tokens) else 0


def _clean_name(name: str) -> str:
    tokens = [t.lower().rstrip(".,") for t in name.split()]
    tokens = [t for t in tokens if len(t) >= 2 and t not in _COMPANY_SUFFIX_SKIP]
    return " ".join(tokens)


def _entity_name_in_org(company_name: str, cert_org_names: set[str]) -> int:
    entity_clean = _clean_name(company_name)
    if len(entity_clean) < 4:
        return 0
    for org in cert_org_names:
        org_clean = _clean_name(org)
        if org_clean and entity_clean in org_clean and len(entity_clean) < len(org_clean):
            return 1
    return 0


def _isotonic_interpolate(x: float, x_vals: list[float], y_vals: list[float]) -> float:
    if x <= x_vals[0]:
        return y_vals[0]
    if x >= x_vals[-1]:
        return y_vals[-1]
    for i in range(len(x_vals) - 1):
        if x_vals[i] <= x <= x_vals[i + 1]:
            dx = x_vals[i + 1] - x_vals[i]
            t = (x - x_vals[i]) / dx if dx else 0.0
            return y_vals[i] + t * (y_vals[i + 1] - y_vals[i])
    return y_vals[-1]


# ---------------------------------------------------------------------------
# Public scoring API
# ---------------------------------------------------------------------------


def scorer_version() -> str:
    """Identity of the loaded model artifact: "<version>@<training_date>".

    The artifact version alone ("v1") is not enough — a retrain keeps the version
    but shifts every probability, so the training date is the discriminator.

    When the artifact carries a calibration layer that the acceptance gate
    disabled (see ``_validate_artifact``), a ``+uncal`` suffix is appended:
    the same artifact produces different probabilities calibrated vs. raw, so
    the two must not share an identity (delta reports diff confidences only
    within one (scorer_id, scorer_version)).
    """
    model = _get_model()
    base = f"{model['version']}@{model['training_date']}"
    if model.get("calibration") and not model["_calibration_active"]:
        return f"{base}+uncal"
    return base


def score_confidence(
    domain: str,
    company_name: str,
    best_similarity: float,
    sources: set[str],
    cert_org_names: set[str],
    *,
    resolves: bool = False,
    evidence_count: int = 0,
    unique_cert_count: int = 0,
    rdap_similarity: float = 0.0,
) -> float:
    """Score domain-entity attribution using the learned logistic model.

    Returns a probability in [0, 1] — calibrated only if the artifact's own
    metrics show the calibration layer improves ECE, raw LR otherwise.
    """
    model = _get_model()
    features = model["features"]
    scaler = model["scaler"]
    coefs = model["coefficients"]
    intercept = float(model["intercept"])
    means = scaler["mean"]
    scales = scaler["scale"]

    evidence_density = (
        float(evidence_count) / float(unique_cert_count) if unique_cert_count > 0 else 0.0
    )

    raw: dict[str, float] = {
        "best_similarity": best_similarity,
        "source_count": float(len(sources)),
        "domain_has_company_token": float(_domain_has_company_token(domain, company_name)),
        "has_shared_infra": float("shared_infra" in sources),
        "has_dns_guess": float("dns_guess" in sources),
        "tld_is_country": float(_tld_is_country(domain)),
        "entity_name_in_org": float(_entity_name_in_org(company_name, cert_org_names)),
        "org_matches_different_entity": 0.0,  # requires S&P 500 data, not available at inference
        "evidence_density": evidence_density,
        "resolves": float(resolves),
        "domain_length": float(len(domain.split(".", maxsplit=1)[0])),
        "rdap_similarity": rdap_similarity,
        # ASN/NS features: not available at inference (would need separate DNS+ASN lookup)
        "same_asn_as_anchor": 0.0,
        "asn_is_cdn": 0.0,
        "shares_nameserver": 0.0,
    }

    z = intercept
    for i, fname in enumerate(features):
        scaled = (raw[fname] - means[i]) / scales[i]
        z += coefs[fname] * scaled

    prob = 1.0 / (1.0 + math.exp(-z))

    # Apply isotonic calibration only when the load-time acceptance gate
    # accepted it (artifact's own metrics say it improves ECE).
    cal = model.get("calibration")
    if cal and model["_calibration_active"]:
        prob = _isotonic_interpolate(prob, cal["x"], cal["y"])

    return round(prob, 4)
