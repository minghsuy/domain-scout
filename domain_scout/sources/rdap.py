"""RDAP (Registration Data Access Protocol) lookups for domain registrant info."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import structlog

if TYPE_CHECKING:
    from domain_scout.config import ScoutConfig

log = structlog.get_logger()

_RDAP_BOOTSTRAP = "https://rdap.org/domain/"


def _safe_list(value: object) -> list[object]:
    """Return value if it is a list, otherwise an empty list."""
    return value if isinstance(value, list) else []


class RDAPLookup:
    """Look up domain registration data via RDAP."""

    def __init__(self, config: ScoutConfig) -> None:
        self._cfg = config

    async def get_registrant_org(self, domain: str) -> str | None:
        """Return the registrant organization name for a domain, or None."""
        try:
            data = await self._query(domain)
            return self._extract_org(data)
        except Exception as exc:
            log.warning("rdap.lookup_failed", domain=domain, error=str(exc))
            return None

    async def get_registrant_info(self, domain: str) -> dict[str, str | None]:
        """Return a dict with org, name, country from RDAP."""
        try:
            data = await self._query(domain)
        except Exception as exc:
            log.warning("rdap.lookup_failed", domain=domain, error=str(exc))
            return {"org": None, "name": None, "country": None}

        return {
            "org": self._extract_org(data),
            "name": self._extract_name(data),
            "country": self._extract_country(data),
        }

    async def _query(self, domain: str) -> dict[str, object]:
        url = f"{_RDAP_BOOTSTRAP}{domain}"
        async with httpx.AsyncClient(
            timeout=self._cfg.http_timeout,
            follow_redirects=True,
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data: dict[str, object] = resp.json()
            log.debug("rdap.query_ok", domain=domain)
            return data

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
    def _extract_from_vcard(cls, data: dict[str, object], field: str) -> str | None:
        """Extract a field from jCard (vcardArray) in an RDAP entity."""
        raw_vcard = data.get("vcardArray")
        if not isinstance(raw_vcard, list) or len(raw_vcard) < 2:
            return None
        for entry in _safe_list(raw_vcard[1]):
            if not isinstance(entry, list) or len(entry) < 4:
                continue
            if entry[0] == field:
                val = entry[3]
                if isinstance(val, str) and val.strip():
                    return val.strip()
        return None

    @classmethod
    def _extract_org(cls, data: dict[str, object]) -> str | None:
        registrant = cls._find_entity(data, "registrant")
        if registrant:
            org = cls._extract_from_vcard(registrant, "org")
            if org:
                return org
            fn = cls._extract_from_vcard(registrant, "fn")
            if fn:
                return fn
        # Fallback: check top-level entities for org
        for entity in _safe_list(data.get("entities", [])):
            if not isinstance(entity, dict):
                continue
            org = cls._extract_from_vcard(entity, "org")
            if org:
                return org
        return None

    @classmethod
    def _extract_name(cls, data: dict[str, object]) -> str | None:
        registrant = cls._find_entity(data, "registrant")
        if registrant:
            return cls._extract_from_vcard(registrant, "fn")
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
