"""DuckDB-based query cache for CT and RDAP results."""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import structlog

if TYPE_CHECKING:
    from domain_scout.sources.ct_logs import CTLogSource
    from domain_scout.sources.rdap import RDAPLookup

try:
    import duckdb
except ImportError:
    duckdb = None  # type: ignore[assignment]

log = structlog.get_logger()

_DEFAULT_CACHE_DIR = Path.home() / ".cache" / "domain-scout"
_CT_TTL_SECONDS = 4 * 3600  # 4 hours
_RDAP_TTL_SECONDS = 24 * 3600  # 24 hours
_VALID_TABLES = frozenset({"ct_cache", "rdap_cache"})
_VACUUM_INTERVAL = 100


def _cache_key(prefix: str, query: str) -> str:
    """SHA-256 hash of prefix:query for use as cache key."""
    raw = f"{prefix}:{query}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _serialize(data: object) -> str:
    """JSON-serialize data, converting non-serializable types to strings."""
    return json.dumps(data, default=str)


def _deserialize(raw: str) -> object:
    """Deserialize JSON string back to Python objects."""
    return json.loads(raw)


class DuckDBCache:
    """Embedded DuckDB cache for CT and RDAP query results."""

    def __init__(self, cache_dir: str | Path | None = None) -> None:
        if duckdb is None:
            raise ImportError(
                "duckdb is required for caching. Install it with: "
                "pip install domain-scout-ct[cache]"
            )
        self._dir = Path(cache_dir) if cache_dir else _DEFAULT_CACHE_DIR
        self._dir.mkdir(parents=True, exist_ok=True)
        db_path = self._dir / "cache.db"
        self._lock = threading.Lock()
        self._conn: duckdb.DuckDBPyConnection | None = duckdb.connect(str(db_path))
        self._vacuum_counter = 0
        self._init_tables()
        log.debug("cache.opened", path=str(db_path))

    def __enter__(self) -> DuckDBCache:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _init_tables(self) -> None:
        assert self._conn is not None
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS ct_cache (
                cache_key VARCHAR PRIMARY KEY,
                result_json VARCHAR NOT NULL,
                created_at DOUBLE NOT NULL
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS rdap_cache (
                cache_key VARCHAR PRIMARY KEY,
                result_json VARCHAR NOT NULL,
                created_at DOUBLE NOT NULL
            )
        """)

    def get_ct(self, query: str) -> list[dict[str, object]] | None:
        """Get cached CT results, or None on miss/expired."""
        result = self._get("ct_cache", _cache_key("ct", query), _CT_TTL_SECONDS)
        return result if isinstance(result, list) else None

    def put_ct(self, query: str, results: list[dict[str, object]]) -> None:
        """Store CT results in cache."""
        self._put("ct_cache", _cache_key("ct", query), results)

    def get_rdap(self, query: str) -> dict[str, str | None] | None:
        """Get cached RDAP results, or None on miss/expired."""
        result = self._get("rdap_cache", _cache_key("rdap", query), _RDAP_TTL_SECONDS)
        return result if isinstance(result, dict) else None

    def put_rdap(self, query: str, result: dict[str, str | None]) -> None:
        """Store RDAP results in cache."""
        self._put("rdap_cache", _cache_key("rdap", query), result)

    def _get(self, table: str, key: str, ttl: float) -> object | None:
        if table not in _VALID_TABLES:
            raise ValueError(f"Invalid cache table: {table}")
        with self._lock:
            if self._conn is None:
                return None
            now = time.time()
            result = self._conn.execute(
                f"SELECT result_json, created_at FROM {table} WHERE cache_key = ?",
                [key],
            ).fetchone()
            if result is None:
                return None
            raw_json, created_at = result
            if now - created_at > ttl:
                self._conn.execute(f"DELETE FROM {table} WHERE cache_key = ?", [key])
                return None
            return _deserialize(raw_json)

    def _put(self, table: str, key: str, data: object) -> None:
        if table not in _VALID_TABLES:
            raise ValueError(f"Invalid cache table: {table}")
        with self._lock:
            if self._conn is None:
                return
            self._conn.execute(
                f"""INSERT OR REPLACE INTO {table} (cache_key, result_json, created_at)
                    VALUES (?, ?, ?)""",
                [key, _serialize(data), time.time()],
            )
            self._vacuum_counter += 1
            if self._vacuum_counter >= _VACUUM_INTERVAL:
                self._vacuum_expired()
                self._vacuum_counter = 0

    def _vacuum_expired(self) -> None:
        """Delete expired entries from both tables. Must be called with lock held."""
        now = time.time()
        if self._conn is None:
            return
        self._conn.execute(
            "DELETE FROM ct_cache WHERE ? - created_at > ?",
            [now, _CT_TTL_SECONDS],
        )
        self._conn.execute(
            "DELETE FROM rdap_cache WHERE ? - created_at > ?",
            [now, _RDAP_TTL_SECONDS],
        )
        log.debug("cache.vacuum_completed")

    def clear(self) -> None:
        """Drop all cached entries."""
        with self._lock:
            if self._conn is None:
                return
            self._conn.execute("DELETE FROM ct_cache")
            self._conn.execute("DELETE FROM rdap_cache")
        log.info("cache.cleared")

    def stats(self) -> dict[str, object]:
        """Return cache statistics."""
        with self._lock:
            if self._conn is None:
                return {
                    "cache_dir": str(self._dir),
                    "ct_entries": 0,
                    "rdap_entries": 0,
                    "ct_oldest_age_seconds": None,
                    "rdap_oldest_age_seconds": None,
                }
            now = time.time()
            ct_row = self._conn.execute("SELECT COUNT(*) FROM ct_cache").fetchone()
            rdap_row = self._conn.execute("SELECT COUNT(*) FROM rdap_cache").fetchone()
            ct_oldest = self._conn.execute("SELECT MIN(created_at) FROM ct_cache").fetchone()
            rdap_oldest = self._conn.execute("SELECT MIN(created_at) FROM rdap_cache").fetchone()

        ct_n = ct_row[0] if ct_row else 0
        rdap_n = rdap_row[0] if rdap_row else 0
        ct_ts = ct_oldest[0] if ct_oldest else None
        rdap_ts = rdap_oldest[0] if rdap_oldest else None

        return {
            "cache_dir": str(self._dir),
            "ct_entries": ct_n,
            "rdap_entries": rdap_n,
            "ct_oldest_age_seconds": round(now - ct_ts, 1) if ct_ts else None,
            "rdap_oldest_age_seconds": round(now - rdap_ts, 1) if rdap_ts else None,
        }

    def close(self) -> None:
        """Close the DuckDB connection. Safe to call multiple times."""
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
                log.debug("cache.closed")


@runtime_checkable
class CTSource(Protocol):
    """Protocol for CT log source (real or cached)."""

    async def search_by_domain(self, domain: str) -> list[dict[str, object]]: ...
    async def search_by_org(
        self, org_name: str, *, verify_org: bool = True
    ) -> list[dict[str, object]]: ...
    async def get_cert_org(self, cert_id: int) -> str | None: ...


@runtime_checkable
class RDAPSource(Protocol):
    """Protocol for RDAP lookup (real or cached)."""

    async def get_registrant_org(self, domain: str) -> str | None: ...
    async def get_registrant_info(self, domain: str) -> dict[str, str | None]: ...


class CachedCTLogSource:
    """Transparent caching wrapper around CTLogSource."""

    def __init__(self, inner: CTLogSource, cache: DuckDBCache) -> None:
        self._inner = inner
        self._cache = cache

    async def search_by_domain(self, domain: str) -> list[dict[str, object]]:
        loop = asyncio.get_running_loop()
        cached = await loop.run_in_executor(None, self._cache.get_ct, f"domain:{domain}")
        if cached is not None:
            log.debug("cache.ct_hit", query=f"domain:{domain}")
            return cached
        result = await self._inner.search_by_domain(domain)
        try:
            await loop.run_in_executor(None, self._cache.put_ct, f"domain:{domain}", result)
        except Exception:
            log.warning("cache.write_failed", query=f"domain:{domain}")
        return result

    async def search_by_org(
        self, org_name: str, *, verify_org: bool = True
    ) -> list[dict[str, object]]:
        key = f"org:{org_name}:verify={verify_org}"
        loop = asyncio.get_running_loop()
        cached = await loop.run_in_executor(None, self._cache.get_ct, key)
        if cached is not None:
            log.debug("cache.ct_hit", query=key)
            return cached
        result = await self._inner.search_by_org(org_name, verify_org=verify_org)
        try:
            await loop.run_in_executor(None, self._cache.put_ct, key, result)
        except Exception:
            log.warning("cache.write_failed", query=key)
        return result

    async def get_cert_org(self, cert_id: int) -> str | None:
        return await self._inner.get_cert_org(cert_id)


class CachedRDAPLookup:
    """Transparent caching wrapper around RDAPLookup."""

    def __init__(self, inner: RDAPLookup, cache: DuckDBCache) -> None:
        self._inner = inner
        self._cache = cache

    async def get_registrant_org(self, domain: str) -> str | None:
        loop = asyncio.get_running_loop()
        cached = await loop.run_in_executor(None, self._cache.get_rdap, f"org:{domain}")
        if cached is not None:
            log.debug("cache.rdap_hit", query=f"org:{domain}")
            return cached.get("org")
        result = await self._inner.get_registrant_org(domain)
        try:
            await loop.run_in_executor(None, self._cache.put_rdap, f"org:{domain}", {"org": result})
        except Exception:
            log.warning("cache.write_failed", query=f"org:{domain}")
        return result

    async def get_registrant_info(self, domain: str) -> dict[str, str | None]:
        loop = asyncio.get_running_loop()
        cached = await loop.run_in_executor(None, self._cache.get_rdap, f"info:{domain}")
        if cached is not None:
            log.debug("cache.rdap_hit", query=f"info:{domain}")
            return cached
        result = await self._inner.get_registrant_info(domain)
        try:
            await loop.run_in_executor(None, self._cache.put_rdap, f"info:{domain}", result)
        except Exception:
            log.warning("cache.write_failed", query=f"info:{domain}")
        return result
