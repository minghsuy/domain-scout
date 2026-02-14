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
        entities: list[dict[str, object]] = data.get("entities", [])  # type: ignore[assignment]
        for entity in entities:
            roles: list[str] = entity.get("roles", [])  # type: ignore[assignment]
            if role in roles:
                return entity
            # Check nested entities
            nested: list[dict[str, object]] = entity.get("entities", [])  # type: ignore[assignment]
            for child in nested:
                child_roles: list[str] = child.get("roles", [])  # type: ignore[assignment]
                if role in child_roles:
                    return child
        return None

    @classmethod
    def _extract_from_vcard(cls, data: dict[str, object], field: str) -> str | None:
        """Extract a field from jCard (vcardArray) in an RDAP entity."""
        vcard_array: list[object] | None = data.get("vcardArray")  # type: ignore[assignment]
        if not vcard_array or len(vcard_array) < 2:  # type: ignore[arg-type]
            return None
        entries: list[list[object]] = vcard_array[1]  # type: ignore[index,assignment]
        for entry in entries:
            if len(entry) >= 4 and entry[0] == field:  # type: ignore[index]
                val = entry[3]  # type: ignore[index]
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
        for entity in data.get("entities", []):  # type: ignore[union-attr]
            org = cls._extract_from_vcard(entity, "org")  # type: ignore[arg-type]
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
