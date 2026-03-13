"""Fuzzy entity name matching for org-name comparison."""

from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz


def _strip_accents(text: str) -> str:
    """NFKD-normalize and strip combining marks (accents)."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


# DBA / subsidiary clauses to strip before suffix removal
_DBA_PATTERN = re.compile(
    r"(?i)(?:,?\s+d/?b/?a\s+|,?\s+doing\s+business\s+as\s+|,?\s+trading\s+as\s+|,?\s+t/a\s+)"
)
_SUBSIDIARY_PATTERN = re.compile(r"(?i),?\s+a\s+(?:subsidiary|division|unit|branch)\s+of\s+.*$")

# Common abbreviations to expand after normalization
_ABBREVIATIONS: dict[str, str] = {
    "intl": "international",
    "tech": "technology",
    "techs": "technologies",
    "svc": "service",
    "svcs": "services",
    "mgmt": "management",
    "natl": "national",
    "assoc": "associates",
    "mfg": "manufacturing",
    "eng": "engineering",
    "sys": "systems",
}

# Legal suffixes to strip before comparison (unambiguous — safe at any position)
_LEGAL_SUFFIXES = [
    r"\bInc\b\.?",
    r"\bIncorporated\b",
    r"\bCorp\.?\b",
    r"\bCorporation\b",
    r"\bLLC\b",
    r"\bL\.L\.C\.?\b",
    r"\bLtd\.?\b",
    r"\bLimited\b",
    r"\bLLP\b",
    r"\bL\.L\.P\.?\b",
    r"\bLP\b",
    r"\bL\.P\.?\b",
    r"\bPLC\b",
    r"\bP\.L\.C\.?\b",
    r"\bGmbH\b",
    r"\bS\.A\.?\b",  # requires dot — plain "SA" handled by trailing
    r"\bS\.?r\.?l\.?\b",
    r"\bS\.?p\.?A\.?\b",
    r"\bN\.V\.?\b",  # requires dot — plain "NV" handled by trailing
    r"\bOyj?\b",
    r"\bASA\b",
    r"\bK\.?K\.?\b",
    r"\bBerhad\b",
    r"\bBhd\.?\b",
    r"\bPJSC\b",
    r"\bCo\.",  # requires dot — dotless "Co" handled by trailing
    r"\bCompany\b",
    r"\band\b",
]

_SUFFIX_PATTERN = re.compile("|".join(_LEGAL_SUFFIXES), re.IGNORECASE)

# Ambiguous suffixes — only stripped at the END of a name.
# These tokens are meaningful words at the start/middle of company names
# (e.g., "Group Nine Media", "SA Power Networks", "AB InBev", "NV Energy")
# but are legal suffixes when trailing (e.g., "Siemens AG", "Volvo AB").
_TRAILING_SUFFIX_PATTERN = re.compile(r"\s+(?:group|holdings?|co|ag|sa|se|nv|ab)$")


def _extract_dba_name(name: str) -> str | None:
    """Extract the DBA (operating) name from a string like
    'ACME LLC DBA ACME CLOUD'. Returns None if no DBA clause found."""
    cleaned = _strip_accents(name)
    dba_match = _DBA_PATTERN.search(cleaned)
    if dba_match and dba_match.start() > 0:
        return cleaned[dba_match.end() :].strip() or None
    return None


def normalize_org_name(name: str) -> str:
    """Normalize a company/org name for comparison.

    - Normalizes unicode (strips accents)
    - Strips DBA/subsidiary clauses
    - Strips legal suffixes (Inc, LLC, Ltd, GmbH, S.p.A., etc.)
    - Lowercases and collapses whitespace
    - Removes leading "The"
    - Expands abbreviations (intl→international, tech→technology, etc.)
    """
    name = _strip_accents(name)
    # Strip DBA / subsidiary clauses before suffix removal
    # (so "Holdings" etc. don't break the regex)
    dba_match = _DBA_PATTERN.search(name)
    if dba_match and dba_match.start() > 0:
        name = name[: dba_match.start()]
    name = _SUBSIDIARY_PATTERN.sub("", name)
    # Strip legal suffixes
    name = _SUFFIX_PATTERN.sub(" ", name)
    # Remove punctuation except hyphens
    name = re.sub(r"[^\w\s\-]", " ", name)
    # Collapse whitespace
    name = re.sub(r"\s+", " ", name).strip().lower()
    # Strip trailing ambiguous suffixes (only meaningful at end of name)
    while True:
        stripped = _TRAILING_SUFFIX_PATTERN.sub("", name)
        if stripped == name:
            break
        name = stripped
    # Strip leading "the"
    name = re.sub(r"^the\s+", "", name)
    # Expand common abbreviations
    words = name.split()
    name = " ".join(_ABBREVIATIONS.get(w, w) for w in words)
    return name


# Hard legal suffixes — stripped in one acronym-detection pass so that
# acronyms like UHG (UnitedHealth Group) match after removing "Incorporated".
# Kept in sync with _LEGAL_SUFFIXES: ambiguous tokens (AG, SE, AB) excluded,
# SA/NV require dot.  Co\.? kept as-is (\b after literal dot fails in regex).
_HARD_LEGAL_SUFFIXES = re.compile(
    r"(?i)\b(?:Inc\.?|Incorporated|Corp\.?|Corporation|LLC|L\.L\.C\.?"
    r"|Ltd\.?|Limited|LLP|L\.L\.P\.?|LP|L\.P\.?|PLC|P\.L\.C\.?"
    r"|GmbH|S\.A\.?|S\.?r\.?l\.?|S\.?p\.?A\.?"
    r"|N\.V\.?|Oyj?|ASA|K\.?K\.?|Berhad|Bhd\.?|PJSC"
    r"|Co\.?|Company)\b"
)

# Acronym match score — deliberately below the confidence boost threshold
# (>0.9 in scout.py _score_confidence) so acronym-only matches require
# additional validation (DNS, RDAP) to reach high confidence.
_ACRONYM_MATCH_SCORE = 0.85

# Stop words skipped when computing acronym initials
_ACRONYM_STOP_WORDS = frozenset({"the", "of", "and", "de", "for", "a", "an"})

# CamelCase boundary patterns
_CAMEL_BOUNDARY_1 = re.compile(r"([a-z])([A-Z])")  # aB → a B
_CAMEL_BOUNDARY_2 = re.compile(r"([A-Z]+)([A-Z][a-z])")  # ABc → A Bc

# Brand aliases: pairs of (brand_name, legal_name) where string matching
# is hopeless without a lookup.  Both sides must be in normalized form.
_BRAND_ALIAS_PAIRS: list[tuple[str, str]] = [
    ("petrobras", "petroleo brasileiro"),
    ("foxconn", "hon hai precision industry"),
    ("etisalat", "emirates telecommunications"),
    ("singtel", "singapore telecommunications"),
]


def _build_brand_aliases() -> dict[str, list[str]]:
    aliases: dict[str, list[str]] = {}
    for a, b in _BRAND_ALIAS_PAIRS:
        aliases.setdefault(a, []).append(b)
        aliases.setdefault(b, []).append(a)
    return aliases


_BRAND_ALIASES = _build_brand_aliases()


def _extract_initials_from_word(word: str) -> str:
    """Extract initials from a single word for acronym detection.

    CamelCase words contribute multiple initials:
      JPMorgan → jpm,  UnitedHealth → uh,  GlaxoSmithKline → gsk
    All-uppercase segments from CamelCase splits contribute each letter:
      JP (from JPMorgan) → j, p
    Standalone words contribute one initial:
      International → i,  UFJ → u,  Bank → b
    """
    if not word:
        return ""
    expanded = _CAMEL_BOUNDARY_1.sub(r"\1 \2", word)
    expanded = _CAMEL_BOUNDARY_2.sub(r"\1 \2", expanded)
    parts = expanded.split()
    if len(parts) == 1:
        # Not CamelCase — single initial
        return word[0].lower()
    # CamelCase — each part contributes
    initials: list[str] = []
    for p in parts:
        if p.isupper() and len(p) > 1:
            # All-uppercase segment (e.g. "JP" from "JPMorgan")
            initials.extend(c.lower() for c in p)
        else:
            initials.append(p[0].lower())
    return "".join(initials)


def _get_initials(name: str) -> str:
    """Compute acronym initials from a name.

    Strips punctuation and stop words, splits CamelCase, then takes
    the first letter(s) of each remaining token.
    """
    name = re.sub(r"[^\w\s]", " ", name)
    words = [w for w in name.split() if w.lower() not in _ACRONYM_STOP_WORDS]
    if not words:
        return ""
    initials = "".join(_extract_initials_from_word(w) for w in words)
    # Need at least 2 initials for a meaningful acronym
    return initials if len(initials) >= 2 else ""


def _fuzzy_best(a: str, b: str) -> float:
    """Best weighted fuzzy score between two normalized strings."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ratio = fuzz.ratio(a, b) / 100.0
    token_sort = fuzz.token_sort_ratio(a, b) / 100.0
    token_set = fuzz.token_set_ratio(a, b) / 100.0
    partial = fuzz.partial_ratio(a, b) / 100.0
    # Penalize partial/token-set more when strings differ greatly in length.
    # Short substrings ("bank") matching inside long names ("deutsche bank")
    # get inflated scores without this guard.
    length_ratio = min(len(a), len(b)) / max(len(a), len(b))
    if length_ratio < 0.4:
        token_set *= 0.70
        partial *= 0.70
    score = max(
        ratio,
        token_sort * 0.95,
        token_set * 0.90,
        partial * 0.85,
    )
    # Conglomerate guard: when both names share tokens but ALSO have
    # dissimilar unique tokens, the shared prefix is a brand family, not
    # the same legal entity (e.g., "Samsung Electronics" ≠ "Samsung SDI").
    # Penalize the final score so these fall below org_match_threshold.
    tokens_a = set(a.split())
    tokens_b = set(b.split())
    shared = tokens_a & tokens_b
    unique_a = tokens_a - tokens_b
    unique_b = tokens_b - tokens_a
    if shared and unique_a and unique_b:
        best_unique_sim = max(fuzz.ratio(ua, ub) for ua in unique_a for ub in unique_b) / 100.0
        if best_unique_sim < 0.6:
            score *= 0.70
    return score


def _acronym_match(norm_a: str, norm_b: str, name_a: str, name_b: str) -> float:
    """Acronym detection with CamelCase splitting and stop-word removal.

    Try twice: once with all words kept (catches TSMC, SMBC where the
    legal suffix is part of the acronym) and once with hard legal
    suffixes stripped (catches UHG, JPMC, GSK where it isn't).
    """
    clean_a = norm_a.replace(" ", "")
    clean_b = norm_b.replace(" ", "")
    if len(clean_a) == len(clean_b):
        return 0.0

    if len(clean_a) < len(clean_b):
        short, long_name = clean_a, name_b
    else:
        short, long_name = clean_b, name_a

    if len(short) < 2:
        return 0.0

    initials_full = _get_initials(long_name)
    initials_stripped = _get_initials(_HARD_LEGAL_SUFFIXES.sub(" ", long_name))

    # Prefix match: "gs" matches "gsgi" (Goldman Sachs Group Inc)
    # where trailing suffixes add unwanted initials.
    if initials_full.startswith(short) or initials_stripped.startswith(short):
        # Score below cross-seed verification (0.90) and confidence
        # boost threshold (>0.9 in scout.py _score_confidence) so
        # acronym matches alone don't inflate confidence.
        return _ACRONYM_MATCH_SCORE

    return 0.0


def _alias_match(norm_a: str, norm_b: str) -> float:
    """Brand-alias lookup for names that differ completely."""
    alias_score = 0.0
    for alias in _BRAND_ALIASES.get(norm_a, []):
        alias_score = max(alias_score, _fuzzy_best(alias, norm_b))
    for alias in _BRAND_ALIASES.get(norm_b, []):
        alias_score = max(alias_score, _fuzzy_best(norm_a, alias))
    return alias_score


def _dba_match(name_a: str, norm_a: str, name_b: str, norm_b: str) -> float:
    """DBA dual-match: compare against the operating (DBA) name.

    If either input contains a DBA clause, also compare against the
    operating (DBA) name. "ACME LLC DBA ACME CLOUD" should match both
    "Acme" (legal name) and "Acme Cloud" (operating name).
    """
    best_score = 0.0
    for raw, other_norm in ((name_a, norm_b), (name_b, norm_a)):
        dba_name = _extract_dba_name(raw)
        if dba_name:
            norm_dba = normalize_org_name(dba_name)
            if norm_dba:
                best_score = max(best_score, _fuzzy_best(norm_dba, other_norm))
    return best_score


def org_name_similarity(name_a: str, name_b: str) -> float:
    """Score how similar two org names are (0.0–1.0).

    Uses multiple strategies and takes the best score:
    - Weighted rapidfuzz (ratio / token-sort / token-set / partial)
    - Acronym detection with CamelCase splitting and stop-word removal
    - Brand-alias lookup for names that differ completely
    - DBA dual-match (compares both legal and operating names)
    """
    # Guard against pathologically long inputs (e.g., adversarial cert org
    # fields). rapidfuzz has O(n*m) complexity; cap at 500 chars.
    name_a = name_a[:500]
    name_b = name_b[:500]
    norm_a = normalize_org_name(name_a)
    norm_b = normalize_org_name(name_b)

    if not norm_a or not norm_b:
        return 0.0

    # Exact match after normalization
    if norm_a == norm_b:
        return 1.0

    return max(
        _fuzzy_best(norm_a, norm_b),
        _acronym_match(norm_a, norm_b, name_a, name_b),
        _alias_match(norm_a, norm_b),
        _dba_match(name_a, norm_a, name_b, norm_b),
    )


def domain_from_company_name(company_name: str) -> list[str]:
    """Generate plausible domain slugs from a company name.

    Returns slugs WITHOUT TLD — the caller appends TLDs.
    e.g., "Acme Solutions, Inc." → ["acmesolutions", "acme-solutions", "acme"]
    """
    norm = normalize_org_name(company_name)
    words = norm.split()
    if not words:
        return []

    slugs: list[str] = ["".join(words)]
    if len(words) > 1:
        slugs.append("-".join(words))
        slugs.append(words[0])
    if len(words) >= 3:
        slugs.append(words[0] + words[-1])

    return list(dict.fromkeys(slugs))
