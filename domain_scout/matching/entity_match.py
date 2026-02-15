"""Fuzzy entity name matching for org-name comparison."""

from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz

# Legal suffixes to strip before comparison
_LEGAL_SUFFIXES = [
    r"\bInc\.?",
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
    r"\bAG\b",
    r"\bS\.?A\.?\b",
    r"\bS\.?r\.?l\.?\b",
    r"\bCo\.?\b",
    r"\bCompany\b",
    r"\bGroup\b",
    r"\bHoldings?\b",
    r"\bInternational\b",
    r"\bIntl\.?\b",
    r"\b&\b",
    r"\band\b",
]

_SUFFIX_PATTERN = re.compile("|".join(_LEGAL_SUFFIXES), re.IGNORECASE)


def normalize_org_name(name: str) -> str:
    """Normalize a company/org name for comparison.

    - Strips legal suffixes (Inc, LLC, Ltd, etc.)
    - Lowercases
    - Normalizes unicode
    - Collapses whitespace
    - Removes leading "The"
    """
    # Unicode normalize: decompose then strip combining marks (accents)
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    # Strip legal suffixes
    name = _SUFFIX_PATTERN.sub(" ", name)
    # Remove punctuation except hyphens
    name = re.sub(r"[^\w\s\-]", " ", name)
    # Collapse whitespace
    name = re.sub(r"\s+", " ", name).strip().lower()
    # Strip leading "the"
    name = re.sub(r"^the\s+", "", name)
    return name


def org_name_similarity(name_a: str, name_b: str) -> float:
    """Score how similar two org names are (0.0–1.0).

    Uses multiple rapidfuzz strategies and takes the best score.
    """
    norm_a = normalize_org_name(name_a)
    norm_b = normalize_org_name(name_b)

    if not norm_a or not norm_b:
        return 0.0

    # Exact match after normalization
    if norm_a == norm_b:
        return 1.0

    # Token-sort handles word reordering: "Palo Alto Networks" vs "Networks, Palo Alto"
    token_sort = fuzz.token_sort_ratio(norm_a, norm_b) / 100.0
    # Token-set handles subset matches: "Alphabet" vs "Alphabet Inc Holdings"
    token_set = fuzz.token_set_ratio(norm_a, norm_b) / 100.0
    # Partial ratio handles substring: "Palo Alto Networks" vs "Palo Alto Networks, Inc."
    partial = fuzz.partial_ratio(norm_a, norm_b) / 100.0
    # Standard ratio
    ratio = fuzz.ratio(norm_a, norm_b) / 100.0

    # Weighted best — token_set is most forgiving, ratio is strictest
    return max(
        ratio,
        token_sort * 0.95,
        token_set * 0.90,
        partial * 0.85,
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

    slugs: list[str] = []
    # All words concatenated: "acmesolutions"
    joined = "".join(words)
    slugs.append(joined)
    # Hyphenated: "acme-solutions"
    if len(words) > 1:
        slugs.append("-".join(words))
    # First word only (if multi-word): "acme"
    if len(words) > 1:
        slugs.append(words[0])
    # First + last word (if 3+ words): useful for "United Parcel Service" → "unitedservice"
    if len(words) >= 3:
        slugs.append(words[0] + words[-1])

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for s in slugs:
        if s not in seen:
            seen.add(s)
            unique.append(s)
    return unique
