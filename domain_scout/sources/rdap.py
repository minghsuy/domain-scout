"""RDAP (Registration Data Access Protocol) lookups for domain registrant info."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import structlog

if TYPE_CHECKING:
    from domain_scout.config import ScoutConfig

log = structlog.get_logger()

_RDAP_BOOTSTRAP = "https://rdap.org/domain/"


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
        raw_entities = data.get("entities", [])
        entities = raw_entities if isinstance(raw_entities, list) else []
        for entity in entities:
            if not isinstance(entity, dict):
                continue
            raw_roles = entity.get("roles", [])
            roles = raw_roles if isinstance(raw_roles, list) else []
            if role in roles:
                return entity
            # Check nested entities
            raw_nested = entity.get("entities", [])
            nested = raw_nested if isinstance(raw_nested, list) else []
            for child in nested:
                if not isinstance(child, dict):
                    continue
                raw_child_roles = child.get("roles", [])
                child_roles = raw_child_roles if isinstance(raw_child_roles, list) else []
                if role in child_roles:
                    return child
        return None

    @classmethod
    def _extract_from_vcard(cls, data: dict[str, object], field: str) -> str | None:
        """Extract a field from jCard (vcardArray) in an RDAP entity."""
        raw_vcard = data.get("vcardArray")
        if not isinstance(raw_vcard, list) or len(raw_vcard) < 2:
            return None
        raw_entries = raw_vcard[1]
        if not isinstance(raw_entries, list):
            return None
        for entry in raw_entries:
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
        raw_entities = data.get("entities", [])
        for entity in raw_entities if isinstance(raw_entities, list) else []:
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
