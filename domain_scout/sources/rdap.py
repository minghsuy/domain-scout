"""RDAP (Registration Data Access Protocol) lookups for domain registrant info."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Literal

import httpx
import structlog

from domain_scout._metrics import SOURCE_ERRORS_TOTAL, inc

if TYPE_CHECKING:
    from domain_scout.config import ScoutConfig

log = structlog.get_logger()

_RDAP_BOOTSTRAP = "https://rdap.org/domain/"

# TLDs not in the IANA RDAP bootstrap registry (https://data.iana.org/rdap/dns.json).
# Requests to rdap.org for these TLDs return 404.  Skip them to avoid
# unnecessary HTTP round-trips and noisy warning logs.
RDAP_SKIP_TLDS: frozenset[str] = frozenset(
    {
        "ae",
        "at",
        "be",
        "bg",
        "ch",
        "cl",
        "cn",
        "co",
        "de",
        "dk",
        "edu",
        "ee",
        "es",
        "hk",
        "hr",
        "hu",
        "ie",
        "il",
        "io",
        "it",
        "jp",
        "kr",
        "lt",
        "lu",
        "lv",
        "mx",
        "my",
        "nz",
        "pe",
        "ro",
        "ru",
        "se",
        "sk",
        "tr",
        "us",
        "za",
    }
)


def _safe_list(value: object) -> list[object]:
    """Return value if it is a list, otherwise an empty list."""
    return value if isinstance(value, list) else []


class _RDAPCircuitBreaker:
    """Circuit breaker for rdap.org requests.

    States: closed (normal) -> open (skip queries) -> half_open (probe).
    """

    _BreakerState = Literal["closed", "open", "half_open"]

    def __init__(self, failure_threshold: int, recovery_timeout: float) -> None:
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._state: _RDAPCircuitBreaker._BreakerState = "closed"
        self._failure_count: int = 0
        self._opened_at: float = 0.0

    @property
    def state(self) -> str:
        return self._state

    def should_allow(self) -> bool:
        """Return True if a query should be attempted."""
        if self._state == "closed":
            return True
        if self._state == "open":
            if time.monotonic() - self._opened_at >= self._recovery_timeout:
                self._state = "half_open"
                log.info("rdap.circuit_half_open")
                return True
            return False
        # half_open: allow one probe
        return True

    def record_success(self) -> None:
        if self._state == "half_open":
            log.info("rdap.circuit_closed")
        self._state = "closed"
        self._failure_count = 0

    def record_failure(self) -> None:
        self._failure_count += 1
        if self._state == "half_open":
            log.warning("rdap.circuit_open", reason="half_open probe failed")
        elif self._failure_count >= self._failure_threshold:
            log.warning(
                "rdap.circuit_open",
                failure_count=self._failure_count,
                threshold=self._failure_threshold,
            )
        else:
            return
        self._state = "open"
        self._opened_at = time.monotonic()

    def reset(self) -> None:
        """Reset to initial closed state (for testing)."""
        self._state = "closed"
        self._failure_count = 0


class RDAPLookup:
    """Look up domain registration data via RDAP.

    The circuit breaker and semaphore are class-level singletons, initialized
    from the first instance's config. Subsequent instances reuse the same state.
    """

    # Shared across all instances to protect rdap.org as a single resource.
    # Initialized once from the first instance's config; subsequent instances
    # log a warning if their config differs.
    _breaker: _RDAPCircuitBreaker | None = None
    _semaphore: asyncio.Semaphore | None = None
    _init_concurrency: int | None = None

    def __init__(self, config: ScoutConfig) -> None:
        self._cfg = config
        if RDAPLookup._semaphore is None:
            RDAPLookup._semaphore = asyncio.Semaphore(config.max_rdap_concurrent)
            RDAPLookup._init_concurrency = config.max_rdap_concurrent
        elif RDAPLookup._init_concurrency != config.max_rdap_concurrent:
            log.warning(
                "rdap.config_mismatch_ignored",
                existing=RDAPLookup._init_concurrency,
                requested=config.max_rdap_concurrent,
            )
        if RDAPLookup._breaker is None:
            RDAPLookup._breaker = _RDAPCircuitBreaker(
                failure_threshold=config.rdap_cb_failure_threshold,
                recovery_timeout=config.rdap_cb_recovery_timeout,
            )

    async def get_registrant_org(self, domain: str) -> str | None:
        """Return the registrant organization name for a domain, or None."""
        try:
            data = await self._query(domain)
            return self._extract_org(data)
        except Exception as exc:
            inc(SOURCE_ERRORS_TOTAL, source="rdap")
            log.warning("rdap.lookup_failed", domain=domain, error=str(exc))
            return None

    async def get_registrant_info(self, domain: str) -> dict[str, str | None]:
        """Return a dict with org, name, country from RDAP."""
        try:
            data = await self._query(domain)
        except Exception as exc:
            inc(SOURCE_ERRORS_TOTAL, source="rdap")
            log.warning("rdap.lookup_failed", domain=domain, error=str(exc))
            return {"org": None, "name": None, "country": None}

        return {
            "org": self._extract_org(data),
            "name": self._extract_name(data),
            "country": self._extract_country(data),
        }

    async def _query(self, domain: str) -> dict[str, object]:
        tld = domain.rsplit(".", 1)[-1].lower()
        if tld in RDAP_SKIP_TLDS:
            log.debug("rdap.skip_unsupported_tld", domain=domain, tld=tld)
            return {}

        breaker = RDAPLookup._breaker
        semaphore = RDAPLookup._semaphore
        assert breaker is not None and semaphore is not None  # set in __init__

        if not breaker.should_allow():
            log.warning("rdap.circuit_open_skip", domain=domain)
            return {}

        async with semaphore:
            try:
                url = f"{_RDAP_BOOTSTRAP}{domain}"
                async with httpx.AsyncClient(
                    timeout=self._cfg.http_timeout,
                    follow_redirects=True,
                ) as client:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    data: dict[str, object] = resp.json()
                log.debug("rdap.query_ok", domain=domain)
                breaker.record_success()
                return data
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code >= 500:
                    breaker.record_failure()
                # 4xx (404 = domain not in RDAP) is normal, don't trip breaker
                raise
            except Exception:
                breaker.record_failure()
                raise

    @staticmethod
    def _find_entity(data: dict[str, object], role: str) -> dict[str, object] | None:
        """Walk the entities tree to find one with the given role."""
        for entity in _safe_list(data.get("entities", [])):
            if not isinstance(entity, dict):
                continue
            if role in _safe_list(entity.get("roles", [])):
                return entity
            for child in _safe_list(entity.get("entities", [])):
                if not isinstance(child, dict):
                    continue
                if role in _safe_list(child.get("roles", [])):
                    return child
        return None

    @classmethod
    def _extract_from_vcard(cls, data: dict[str, object], field: str) -> object | None:
        """Extract a field from jCard (vcardArray) in an RDAP entity."""
        raw_vcard = data.get("vcardArray")
        if not isinstance(raw_vcard, list) or len(raw_vcard) < 2:
            return None
        for entry in _safe_list(raw_vcard[1]):
            if not isinstance(entry, list) or len(entry) < 4:
                continue
            if entry[0] == field:
                val: object = entry[3]
                if isinstance(val, str):
                    s = val.strip()
                    return s if s else None
                return val
        return None

    @classmethod
    def _extract_org(cls, data: dict[str, object]) -> str | None:
        registrant = cls._find_entity(data, "registrant")
        if registrant:
            org = cls._extract_from_vcard(registrant, "org")
            if isinstance(org, str):
                return org
            fn = cls._extract_from_vcard(registrant, "fn")
            if isinstance(fn, str):
                return fn
        # Fallback: check top-level entities for org
        for entity in _safe_list(data.get("entities", [])):
            if not isinstance(entity, dict):
                continue
            org = cls._extract_from_vcard(entity, "org")
            if isinstance(org, str):
                return org
        return None

    @classmethod
    def _extract_name(cls, data: dict[str, object]) -> str | None:
        registrant = cls._find_entity(data, "registrant")
        if registrant:
            fn = cls._extract_from_vcard(registrant, "fn")
            if isinstance(fn, str):
                return fn
        return None

    @classmethod
    def _extract_country(cls, data: dict[str, object]) -> str | None:
        registrant = cls._find_entity(data, "registrant")
        if registrant:
            adr = cls._extract_from_vcard(registrant, "adr")
            if isinstance(adr, list) and len(adr) >= 7:
                country = adr[6]
                if isinstance(country, str):
                    return country
        return None
