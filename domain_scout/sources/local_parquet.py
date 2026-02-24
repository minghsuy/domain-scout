"""Local parquet warehouse as a CT source."""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from domain_scout.config import ScoutConfig
    from domain_scout.sources.ct_logs import CTLogSource

log = structlog.get_logger()


def _fingerprint_to_cert_id(fp: str) -> int:
    """Deterministic cert_id from fingerprint hash."""
    return int(hashlib.md5(fp.encode()).hexdigest()[:8], 16)  # noqa: S324


def _row_to_record(row: tuple[object, ...], columns: list[str]) -> dict[str, object]:
    """Convert a DuckDB result row to a CTSource-compatible dict."""
    d = dict(zip(columns, row, strict=True))
    fp = str(d["fingerprint"])
    org = str(d["org_raw"]) if d["org_raw"] else None
    raw_sans = d.get("san_dns_names")
    sans: list[str] = list(raw_sans) if isinstance(raw_sans, list) else []
    not_before = d.get("not_before")
    not_after = d.get("not_after")

    # Synthesize common_name from first SAN
    cn = sans[0] if sans else ""

    return {
        "cert_id": _fingerprint_to_cert_id(fp),
        "common_name": cn,
        "subject": f"O={org}" if org else "",
        "org_name": org,
        "not_before": not_before if isinstance(not_before, datetime) else None,
        "not_after": not_after if isinstance(not_after, datetime) else None,
        "san_dns_names": sans,
    }


class LocalParquetSource:
    """CT source backed by local parquet warehouse files."""

    def __init__(self, config: ScoutConfig) -> None:
        import duckdb

        self._cfg = config
        wpath = Path(config.warehouse_path or "")
        if not wpath.is_dir():
            raise FileNotFoundError(f"Warehouse directory not found: {wpath}")

        files = list(wpath.glob("**/*.parquet"))
        if not files:
            raise FileNotFoundError(f"No parquet files in: {wpath}")

        self._parquet_glob = str(wpath / "**" / "*.parquet")
        self._conn = duckdb.connect()

        # Preload distinct org names for fuzzy matching
        result = self._conn.execute(
            "SELECT DISTINCT org_raw FROM read_parquet(?, union_by_name=true) "
            "WHERE org_raw IS NOT NULL",
            [self._parquet_glob],
        ).fetchall()
        self._org_index: list[str] = [r[0] for r in result if r[0]]
        log.info(
            "local_parquet.loaded",
            parquet_files=len(files),
            distinct_orgs=len(self._org_index),
        )

    async def search_by_org(
        self, org_name: str, *, verify_org: bool = True
    ) -> list[dict[str, object]]:
        """Search warehouse by org name using fuzzy matching."""
        from rapidfuzz import process as rfprocess

        matches = rfprocess.extract(
            org_name,
            self._org_index,
            limit=self._cfg.local_max_fuzzy_matches,
            score_cutoff=self._cfg.local_fuzzy_threshold,
        )
        if not matches:
            log.debug("local_parquet.no_org_match", query=org_name)
            return []

        matched_names = [m[0] for m in matches]
        log.debug(
            "local_parquet.org_matches",
            query=org_name,
            matches=[(m[0], round(m[1], 1)) for m in matches],
        )

        placeholders = ", ".join(["?"] * len(matched_names))
        sql = (
            "SELECT fingerprint, org_raw, "
            "MIN(not_before) AS not_before, MAX(not_after) AS not_after, "
            "LIST(DISTINCT domain ORDER BY domain) AS san_dns_names "
            "FROM read_parquet(?, union_by_name=true) "
            f"WHERE org_raw IN ({placeholders}) "
            "GROUP BY fingerprint, org_raw"
        )
        params = [self._parquet_glob, *matched_names]
        result = self._conn.execute(sql, params)
        columns = [desc[0] for desc in result.description]
        rows = result.fetchall()
        return [_row_to_record(r, columns) for r in rows]

    async def search_by_domain(self, domain: str) -> list[dict[str, object]]:
        """Search warehouse by exact domain or suffix match."""
        sql = (
            "SELECT fingerprint, org_raw, "
            "MIN(not_before) AS not_before, MAX(not_after) AS not_after, "
            "LIST(DISTINCT domain ORDER BY domain) AS san_dns_names "
            "FROM read_parquet(?, union_by_name=true) "
            "WHERE domain = ? OR domain LIKE ? "
            "GROUP BY fingerprint, org_raw"
        )
        suffix_pattern = f"%.{domain}"
        result = self._conn.execute(sql, [self._parquet_glob, domain, suffix_pattern])
        columns = [desc[0] for desc in result.description]
        rows = result.fetchall()
        return [_row_to_record(r, columns) for r in rows]

    async def get_cert_org(self, cert_id: int) -> str | None:
        """Not applicable for local parquet (no cert_id concept)."""
        return None

    def close(self) -> None:
        """Close the DuckDB connection."""
        if self._conn is not None:
            self._conn.close()
            log.debug("local_parquet.closed")


class HybridCTSource:
    """Tries local parquet first, falls back to remote CTLogSource."""

    def __init__(self, local: LocalParquetSource, remote: CTLogSource) -> None:
        self._local = local
        self._remote = remote

    async def search_by_org(
        self, org_name: str, *, verify_org: bool = True
    ) -> list[dict[str, object]]:
        results = await self._local.search_by_org(org_name, verify_org=verify_org)
        if results:
            return results
        log.debug("hybrid.fallback_to_remote", method="search_by_org", query=org_name)
        return await self._remote.search_by_org(org_name, verify_org=verify_org)

    async def search_by_domain(self, domain: str) -> list[dict[str, object]]:
        results = await self._local.search_by_domain(domain)
        if results:
            return results
        log.debug("hybrid.fallback_to_remote", method="search_by_domain", query=domain)
        return await self._remote.search_by_domain(domain)

    async def get_cert_org(self, cert_id: int) -> str | None:
        return await self._remote.get_cert_org(cert_id)
