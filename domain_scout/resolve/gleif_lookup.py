"""GLEIF corporate tree lookup for entity resolution.

Given a company name, finds matching GLEIF entities and expands the
corporate tree (parent, direct subsidiaries, ultimate subsidiaries).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    import duckdb

log = structlog.get_logger()


@dataclass
class GleifEntity:
    """A GLEIF entity record."""

    lei: str
    legal_name: str
    other_names: list[str] = field(default_factory=list)
    country: str | None = None


@dataclass
class CorporateTree:
    """Resolved corporate tree for a queried entity."""

    query_entity: GleifEntity
    parent: GleifEntity | None = None
    subsidiaries: list[GleifEntity] = field(default_factory=list)
    siblings: list[GleifEntity] = field(default_factory=list)

    @property
    def all_names(self) -> list[str]:
        """All entity names in the corporate family (for warehouse search)."""
        names = [self.query_entity.legal_name, *self.query_entity.other_names]
        if self.parent:
            names.extend([self.parent.legal_name, *self.parent.other_names])
        for sub in self.subsidiaries:
            names.extend([sub.legal_name, *sub.other_names])
        # Deduplicate preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for n in names:
            nl = n.lower()
            if nl not in seen:
                seen.add(nl)
                unique.append(n)
        return unique


def find_entity(
    name: str,
    con: duckdb.DuckDBPyConnection,
    *,
    fuzzy_threshold: float = 0.80,
    short_name_threshold: float = 0.90,
) -> GleifEntity | None:
    """Find best GLEIF entity match for a company name.

    Tries exact match first, then case-insensitive, then normalized
    prefix, then fuzzy via DuckDB's jaro_winkler_similarity.

    NOTE: Only searches ``legal_name``, not ``other_names``. Trade names
    and short aliases stored in ``other_names`` (e.g. "AFS") will fall
    through to the fuzzy path, which may return a wrong match for short
    strings. After a match, ``other_names`` are used for warehouse
    expansion via ``CorporateTree.all_names``.
    """
    # Exact match — prefer entities with subsidiaries (e.g. "ALLIANZ" with 0 subs
    # vs "Allianz SE" with 682 subs both match "Allianz", but only SE is useful).
    sub_count_join = """
        LEFT JOIN (
            SELECT parent_lei, COUNT(*) AS sub_count
            FROM gleif_relationship
            GROUP BY parent_lei
        ) r ON r.parent_lei = g.lei
    """
    row = con.execute(
        f"SELECT g.lei, g.legal_name, g.other_names, g.country "
        f"FROM gleif_entity g {sub_count_join} "
        f"WHERE g.legal_name = ? ORDER BY COALESCE(r.sub_count, 0) DESC LIMIT 1",
        [name],
    ).fetchone()
    if row:
        log.debug("gleif.exact_match", name=name, lei=row[0])
        return _row_to_entity(row)

    # Case-insensitive — if match has no subsidiaries, fall through to prefix
    # match which may find a parent entity (e.g. "Allianz" → "ALLIANZ" has 0
    # subs, but prefix match finds "Allianz SE" with 682 subs).
    row = con.execute(
        f"SELECT g.lei, g.legal_name, g.other_names, g.country, "
        f"COALESCE(r.sub_count, 0) AS subs "
        f"FROM gleif_entity g {sub_count_join} "
        f"WHERE LOWER(g.legal_name) = LOWER(?) "
        f"ORDER BY subs DESC LIMIT 1",
        [name],
    ).fetchone()
    icase_fallback: GleifEntity | None = None
    if row:
        if row[4] > 0:
            log.debug("gleif.icase_match", name=name, lei=row[0])
            return _row_to_entity(row)
        # Has 0 subsidiaries — keep as fallback, try prefix match first
        icase_fallback = _row_to_entity(row)

    # Normalized prefix match: strip legal suffixes, prefer parent entities.
    normalized = _normalize_for_gleif(name)
    row = con.execute(
        f"""
        SELECT g.lei, g.legal_name, g.other_names, g.country,
               COALESCE(sub_count, 0) AS subs
        FROM gleif_entity g
        {sub_count_join}
        WHERE LOWER(g.legal_name) LIKE LOWER(?)
           OR LOWER(g.legal_name) LIKE LOWER(?)
           OR LOWER(g.legal_name) LIKE LOWER(?)
        ORDER BY subs DESC, LENGTH(g.legal_name)
        LIMIT 1
        """,
        [
            f"The {normalized}%",
            f"{normalized}%",
            f"% {normalized}%",
        ],
    ).fetchone()
    if row:
        log.debug("gleif.prefix_match", name=name, lei=row[0])
        return _row_to_entity(row)

    # Return case-insensitive fallback if prefix match also failed
    if icase_fallback:
        log.debug("gleif.icase_fallback", name=name, lei=icase_fallback.lei)
        return icase_fallback

    # Fuzzy match — jaro_winkler with high threshold
    threshold = short_name_threshold if len(name) <= 15 else fuzzy_threshold
    row = con.execute(
        """
        SELECT lei, legal_name, other_names, country,
               jaro_winkler_similarity(LOWER(legal_name), LOWER(?)) AS sim
        FROM gleif_entity
        WHERE jaro_winkler_similarity(LOWER(legal_name), LOWER(?)) >= ?
        ORDER BY sim DESC
        LIMIT 1
        """,
        [name, name, threshold],
    ).fetchone()
    if row:
        log.debug("gleif.fuzzy_match", name=name, lei=row[0], sim=round(row[4], 3))
        return _row_to_entity(row)

    log.debug("gleif.no_match", name=name)
    return None


def expand_corporate_tree(
    entity: GleifEntity,
    con: duckdb.DuckDBPyConnection,
) -> CorporateTree:
    """Expand a GLEIF entity into its full corporate tree."""
    tree = CorporateTree(query_entity=entity)

    # Find parent (direct consolidation)
    parent_row = con.execute(
        """
        SELECT g.lei, g.legal_name, g.other_names, g.country
        FROM gleif_relationship r
        JOIN gleif_entity g ON g.lei = r.parent_lei
        WHERE r.child_lei = ?
          AND r.relationship_type = 'IS_DIRECTLY_CONSOLIDATED_BY'
        LIMIT 1
        """,
        [entity.lei],
    ).fetchone()
    if parent_row:
        tree.parent = _row_to_entity(parent_row)

    # Find subsidiaries — include both direct and ultimate consolidation
    # so multi-hop trees work (e.g. BRK → Gen Re Corp → Gen Re AG).
    sub_rows = con.execute(
        """
        SELECT DISTINCT g.lei, g.legal_name, g.other_names, g.country
        FROM gleif_relationship r
        JOIN gleif_entity g ON g.lei = r.child_lei
        WHERE r.parent_lei = ?
          AND r.relationship_type IN (
              'IS_DIRECTLY_CONSOLIDATED_BY',
              'IS_ULTIMATELY_CONSOLIDATED_BY'
          )
        ORDER BY g.legal_name
        """,
        [entity.lei],
    ).fetchall()
    tree.subsidiaries = [_row_to_entity(r) for r in sub_rows]

    # Find siblings (other children of our parent)
    if tree.parent:
        sibling_rows = con.execute(
            """
            SELECT DISTINCT g.lei, g.legal_name, g.other_names, g.country
            FROM gleif_relationship r
            JOIN gleif_entity g ON g.lei = r.child_lei
            WHERE r.parent_lei = ?
              AND r.child_lei != ?
            ORDER BY g.legal_name
            """,
            [tree.parent.lei, entity.lei],
        ).fetchall()
        tree.siblings = [_row_to_entity(r) for r in sibling_rows]

    log.debug(
        "gleif.tree_expanded",
        entity=entity.legal_name,
        parent=tree.parent.legal_name if tree.parent else None,
        subsidiaries=len(tree.subsidiaries),
        siblings=len(tree.siblings),
    )
    return tree


_LEGAL_SUFFIXES = re.compile(
    r"[,.]?\s*\b(inc\.?|incorporated|corp\.?|corporation|co\.?|company|"
    r"ltd\.?|limited|llc|l\.l\.c\.?|plc|group|"
    r"s\.?a\.?|ag|gmbh|n\.?v\.?)\b\.?\s*$",
    re.IGNORECASE,
)


def _normalize_for_gleif(name: str) -> str:
    """Strip common prefixes/suffixes for GLEIF prefix matching."""
    # Remove "The" prefix
    n = re.sub(r"^the\s+", "", name, flags=re.IGNORECASE)
    # Repeatedly strip trailing legal suffixes (e.g. "Foo Corp. Ltd.")
    while True:
        stripped = _LEGAL_SUFFIXES.sub("", n)
        if stripped == n:
            break
        n = stripped
    # Collapse whitespace, strip punctuation edges
    n = re.sub(r"[,.]", "", n)
    n = " ".join(n.split()).strip()
    return n


def _row_to_entity(row: tuple[Any, ...]) -> GleifEntity:
    """Convert a DuckDB row tuple to GleifEntity."""
    return GleifEntity(
        lei=row[0],
        legal_name=row[1],
        other_names=row[2] or [],
        country=row[3],
    )
