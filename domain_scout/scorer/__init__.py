"""Learned confidence scorer for domain-entity attribution.

Loads a logistic regression model from a JSON artifact and produces
calibrated probabilities from domain evidence features.
"""

from __future__ import annotations

import json
import math
from importlib import resources
from typing import Any


def _load_model() -> dict[str, Any]:
    """Load the default model artifact shipped with the package."""
    ref = resources.files(__package__).joinpath("logistic_v1.json")
    result: dict[str, Any] = json.loads(ref.read_text(encoding="utf-8"))
    return result


_MODEL: dict[str, Any] | None = None


def _get_model() -> dict[str, Any]:
    global _MODEL  # noqa: PLW0603
    if _MODEL is None:
        _MODEL = _load_model()
    return _MODEL


# ---------------------------------------------------------------------------
# Feature helpers (mirrored from ct-entity-resolution build_training_data.py)
# ---------------------------------------------------------------------------

_COMPANY_SUFFIX_SKIP = frozenset({
    "inc", "corp", "ltd", "llc", "plc", "company", "group", "holdings",
    "the", "and", "co", "sa", "se", "nv", "ag", "ab",
})

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
    base = domain.split(".")[0].lower()
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

def score_confidence(
    domain: str,
    company_name: str,
    best_similarity: float,
    sources: set[str],
    cert_org_names: set[str],
) -> float:
    """Score domain-entity attribution using the learned logistic model.

    Returns a calibrated probability in [0, 1].
    """
    model = _get_model()
    features = model["features"]
    scaler = model["scaler"]
    coefs = model["coefficients"]
    intercept = float(model["intercept"])
    means = scaler["mean"]
    scales = scaler["scale"]

    raw: dict[str, float] = {
        "best_similarity": best_similarity,
        "source_count": float(len(sources)),
        "domain_has_company_token": float(_domain_has_company_token(domain, company_name)),
        "has_shared_infra": float("shared_infra" in sources),
        "has_dns_guess": float("dns_guess" in sources),
        "tld_is_country": float(_tld_is_country(domain)),
        "entity_name_in_org": float(_entity_name_in_org(company_name, cert_org_names)),
        "org_matches_different_entity": 0.0,  # requires S&P 500 data, not available at inference
    }

    z = intercept
    for i, fname in enumerate(features):
        scaled = (raw[fname] - means[i]) / scales[i]
        z += coefs[fname] * scaled

    prob = 1.0 / (1.0 + math.exp(-z))

    # Apply isotonic calibration if available
    cal = model.get("calibration")
    if cal:
        prob = _isotonic_interpolate(prob, cal["x"], cal["y"])

    return round(prob, 4)
