"""Certificate Transparency log queries via crt.sh Postgres (primary) and JSON API (fallback)."""

from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime
from typing import TYPE_CHECKING

import httpx
import psycopg2
import psycopg2.extras
import structlog

from domain_scout._metrics import (
    CT_FALLBACKS_TOTAL,
    CT_QUERIES_TOTAL,
    inc,
    set_cb_state,
)

if TYPE_CHECKING:
    from domain_scout.config import ScoutConfig

log = structlog.get_logger()

# OID for X.509 organizationName
_ORG_OID = "2.5.4.10"

# --- Postgres queries ---

_QUERY_DOMAIN_CERTS = """
    SELECT c.id,
           x509_commonName(c.certificate),
           x509_subjectName(c.certificate),
           x509_notBefore(c.certificate),
           x509_notAfter(c.certificate),
           x509_altNames(c.certificate, 2)
    FROM certificate c
    WHERE plainto_tsquery('certwatch', %(query)s) @@ identities(c.certificate)
      AND x509_notAfter(c.certificate) > NOW() - make_interval(years => %(years)s)
    LIMIT %(limit)s
"""

_QUERY_ORG_FROM_CERT = """
    SELECT x509_nameattributes(c.certificate, '2.5.4.10', true)
    FROM certificate c
    WHERE c.id = %(cert_id)s
"""


def _parse_dt(value: object) -> datetime | None:
    """Parse a datetime string from the crt.sh JSON API."""
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _extract_org_from_subject(subject: str) -> str | None:
    """Parse O=... from an X.509 subject string.

    Handles quoted strings with commas and escaped characters.
    """
    parts: list[str] = []
    current: list[str] = []
    in_quote = False
    escape = False

    # Split by comma, respecting quotes
    for char in subject:
        if escape:
            current.append(char)
            escape = False
        elif char == "\\":
            current.append(char)
            escape = True
        elif char == '"':
            in_quote = not in_quote
            current.append(char)
        elif char == "," and not in_quote:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    parts.append("".join(current).strip())

    for part in parts:
        # Split on first = (key=value)
        idx = part.find("=")
        if idx == -1:
            continue

        key = part[:idx].strip().upper()
        val = part[idx + 1 :].strip()

        if key == "O":
            # If quoted
            if val.startswith('"') and val.endswith('"'):
                inner = val[1:-1]
                # In quoted string, \" is literal ", \\ is literal \
                return inner.replace(r'\"', '"').replace(r"\\", "\\")

            # Unquoted: unescape \, and \\
            return val.replace(r"\,", ",").replace(r"\\", "\\")

    return None


def _extract_base_domain(name: str) -> str | None:
    """Extract the registrable base domain from a DNS name.

    Handles wildcards and subdomains by keeping the last two labels
    (or three for two-letter second-level like .co.uk).
    """
    name = name.lower().strip().rstrip(".")
    if name.startswith("*."):
        name = name[2:]
    if re.match(r"^\d+\.\d+\.\d+\.\d+$", name):
        return None
    parts = name.split(".")
    if len(parts) < 2:
        return None
    # Simple heuristic for ccTLD+SLD (co.uk, com.au, etc.)
    if len(parts) >= 3 and len(parts[-2]) <= 3 and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _is_valid_domain(name: str) -> bool:
    """Reject obviously invalid entries."""
    name = name.strip().lower()
    if not name or name == "*":
        return False
    if name in ("localhost", "localhost.localdomain"):
        return False
    # IP-only SANs
    if re.match(r"^\d+\.\d+\.\d+\.\d+$", name):
        return False
    # Must have at least one dot
    clean = name.lstrip("*.")
    return "." in clean


class _CircuitBreaker:
    """Simple circuit breaker for crt.sh Postgres connections.

    States: closed (normal) → open (skip Postgres) → half_open (probe).
    Shared across CTLogSource instances via class variable.  The breaker
    is initialized once from the first CTLogSource instance's config;
    subsequent instances reuse the same breaker (and its thresholds).
    """

    def __init__(self, failure_threshold: int, recovery_timeout: float) -> None:
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._state: str = "closed"
        self._failure_count: int = 0
        self._opened_at: float = 0.0

    @property
    def state(self) -> str:
        return self._state

    def should_allow(self) -> bool:
        """Return True if a Postgres query should be attempted."""
        if self._state == "closed":
            return True
        if self._state == "open":
            if time.monotonic() - self._opened_at >= self._recovery_timeout:
                self._state = "half_open"
                set_cb_state(self._state)
                log.info("ct.circuit_half_open")
                return True
            return False
        # half_open: allow one probe
        return True

    def record_success(self) -> None:
        if self._state == "half_open":
            log.info("ct.circuit_closed")
        self._state = "closed"
        self._failure_count = 0
        set_cb_state(self._state)

    def record_failure(self) -> None:
        self._failure_count += 1
        if self._state == "half_open":
            log.warning("ct.circuit_open", reason="half_open probe failed")
        elif self._failure_count >= self._failure_threshold:
            log.warning(
                "ct.circuit_open",
                failure_count=self._failure_count,
                threshold=self._failure_threshold,
            )
        else:
            return
        self._state = "open"
        self._opened_at = time.monotonic()
        set_cb_state(self._state)

    def reset(self) -> None:
        """Reset to initial closed state (for testing)."""
        self._state = "closed"
        self._failure_count = 0
        self._opened_at = 0.0


class CTLogSource:
    """Query crt.sh for certificate transparency data."""

    _breaker: _CircuitBreaker | None = None

    def __init__(self, config: ScoutConfig) -> None:
        self._cfg = config
        self._semaphore = asyncio.Semaphore(config.max_concurrent_queries)
        # Lazily init shared breaker with first instance's config
        if CTLogSource._breaker is None:
            CTLogSource._breaker = _CircuitBreaker(
                config.cb_failure_threshold,
                config.cb_recovery_timeout,
            )

    @property
    def _active_breaker(self) -> _CircuitBreaker:
        if self._breaker is None:
            raise RuntimeError("CTLogSource used before breaker initialization")
        return self._breaker

    # --- Public API ---

    async def search_by_domain(self, domain: str) -> list[dict[str, object]]:
        """Search CT logs for certs matching a domain (FTS). Returns raw cert records."""
        return await self._pg_query_with_fallback(domain)

    async def search_by_org(
        self, org_name: str, *, verify_org: bool = True
    ) -> list[dict[str, object]]:
        """Search CT logs for certs where the subject Organization matches org_name.

        Uses FTS for initial candidate search, then filters by O= in subject.
        """
        records = await self._pg_query_with_fallback(org_name)
        if not verify_org:
            return records
        # Filter to only certs whose subject O= field roughly matches
        return [r for r in records if r.get("org_name")]

    async def get_cert_org(self, cert_id: int) -> str | None:
        """Fetch the Organization name from a specific certificate."""
        try:
            return await self._pg_get_org(cert_id)
        except Exception:
            log.warning("ct.get_cert_org_failed", cert_id=cert_id)
            return None

    # --- Postgres backend ---

    def _connect_pg(self) -> psycopg2.extensions.connection:
        conn = psycopg2.connect(
            host=self._cfg.crtsh_postgres_host,
            port=self._cfg.crtsh_postgres_port,
            dbname=self._cfg.crtsh_postgres_db,
            user=self._cfg.crtsh_postgres_user,
        )
        conn.set_session(autocommit=True)
        return conn

    async def _pg_query(self, search_term: str) -> list[dict[str, object]]:
        """Run a Postgres FTS query against crt.sh in a thread."""
        async with self._semaphore:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, self._pg_query_sync, search_term)

    def _pg_query_sync(self, search_term: str) -> list[dict[str, object]]:
        conn = self._connect_pg()
        try:
            cur = conn.cursor()
            cur.execute("SET statement_timeout = %s", (self._cfg.postgres_timeout * 1000,))
            cur.execute(
                _QUERY_DOMAIN_CERTS,
                {
                    "query": search_term,
                    "years": self._cfg.ct_recent_years,
                    "limit": self._cfg.ct_max_results,
                },
            )
            rows = cur.fetchall()
        finally:
            conn.close()

        # Aggregate: rows are (id, cn, subject, not_before, not_after, single_san)
        # Multiple rows per cert (one per SAN). Group by cert id.
        certs: dict[int, dict[str, object]] = {}
        san_sets: dict[int, set[str]] = {}
        for cert_id, cn, subject, nb, na, san in rows:
            if cert_id not in certs:
                certs[cert_id] = {
                    "cert_id": cert_id,
                    "common_name": cn or "",
                    "subject": subject or "",
                    "org_name": _extract_org_from_subject(subject or ""),
                    "not_before": nb,
                    "not_after": na,
                    "san_dns_names": [],
                }
                san_sets[cert_id] = set()
            if san and _is_valid_domain(san):
                san_sets[cert_id].add(san)

        for cid, cert in certs.items():
            cert["san_dns_names"] = sorted(san_sets[cid])

        log.info("ct.pg_query", term=search_term, certs_found=len(certs))
        inc(CT_QUERIES_TOTAL, backend="postgres", status="ok")
        return list(certs.values())

    async def _pg_get_org(self, cert_id: int) -> str | None:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._pg_get_org_sync, cert_id)

    def _pg_get_org_sync(self, cert_id: int) -> str | None:
        conn = self._connect_pg()
        try:
            cur = conn.cursor()
            cur.execute(_QUERY_ORG_FROM_CERT, {"cert_id": cert_id})
            row = cur.fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    # --- JSON API fallback ---

    async def _json_query(self, search_term: str) -> list[dict[str, object]]:
        """Fall back to the crt.sh JSON API."""
        async with self._semaphore:
            # Try domain search
            url = f"{self._cfg.crtsh_json_base_url}/"
            params: dict[str, str] = {"q": search_term, "output": "json"}
            log.info("ct.json_query", url=url, params=params)
            async with httpx.AsyncClient(timeout=self._cfg.http_timeout) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()

        certs: dict[int, dict[str, object]] = {}
        san_sets: dict[int, set[str]] = {}
        for entry in data:
            cert_id = entry.get("id", 0)
            if cert_id not in certs:
                certs[cert_id] = {
                    "cert_id": cert_id,
                    "common_name": entry.get("common_name", ""),
                    "subject": "",  # JSON API doesn't provide X.509 subject DN
                    "org_name": None,  # JSON API doesn't provide subject organization
                    "not_before": _parse_dt(entry.get("not_before")),
                    "not_after": _parse_dt(entry.get("not_after")),
                    "san_dns_names": [],
                }
                san_sets[cert_id] = set()
            name_value = entry.get("name_value", "")
            for name in name_value.split("\n"):
                name = name.strip()
                if name and _is_valid_domain(name):
                    san_sets[cert_id].add(name)

        for cid, cert in certs.items():
            cert["san_dns_names"] = sorted(san_sets[cid])

        log.info("ct.json_query", term=search_term, certs_found=len(certs))
        inc(CT_QUERIES_TOTAL, backend="json", status="ok")
        return list(certs.values())

    # --- Combined with retry/fallback ---

    async def _pg_query_with_fallback(self, search_term: str) -> list[dict[str, object]]:
        """Try Postgres with retries, fall back to JSON API."""
        breaker = self._active_breaker

        if not breaker.should_allow():
            log.warning("ct.circuit_breaker_skip_pg", state=breaker.state)
            return await self._json_fallback(search_term, last_pg_error=None)

        last_err: Exception | None = None
        for attempt in range(1, self._cfg.postgres_max_retries + 1):
            try:
                result = await self._pg_query(search_term)
                breaker.record_success()
                return result
            except Exception as exc:
                last_err = exc
                inc(CT_QUERIES_TOTAL, backend="postgres", status="error")
                log.warning(
                    "ct.pg_retry",
                    attempt=attempt,
                    max=self._cfg.postgres_max_retries,
                    error=str(exc),
                )
                if attempt < self._cfg.postgres_max_retries:
                    await asyncio.sleep(self._cfg.burst_delay * attempt)

        breaker.record_failure()
        return await self._json_fallback(search_term, last_pg_error=last_err)

    async def _json_fallback(
        self,
        search_term: str,
        *,
        last_pg_error: Exception | None,
    ) -> list[dict[str, object]]:
        """Fall back to JSON API after Postgres failure or circuit breaker skip."""
        inc(CT_FALLBACKS_TOTAL)
        if last_pg_error is not None:
            log.warning("ct.pg_failed_falling_back_to_json", error=str(last_pg_error))
        try:
            return await self._json_query(search_term)
        except Exception as exc:
            inc(CT_QUERIES_TOTAL, backend="json", status="error")
            log.error(
                "ct.all_sources_failed",
                pg_error=str(last_pg_error) if last_pg_error else "circuit_breaker_skip",
                json_error=str(exc),
            )
            return []


def extract_base_domain(name: str) -> str | None:
    """Public wrapper for base domain extraction."""
    return _extract_base_domain(name)


def is_valid_domain(name: str) -> bool:
    """Public wrapper for domain validation."""
    return _is_valid_domain(name)
