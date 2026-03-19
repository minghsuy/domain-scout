"""CTScout remote API source — queries ctscout.dev warehouse."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import structlog

if TYPE_CHECKING:
    from domain_scout.config import ScoutConfig

log = structlog.get_logger()


class CTScoutRemoteSource:
    """Query the CTScout API (ctscout.dev) for org-domain mappings.

    Returns warehouse results as CT-compatible records so they integrate
    into the existing scoring pipeline. Each warehouse row becomes a
    synthetic CT record with source_type "ctscout_warehouse".

    Note: ``verify_org`` is accepted for CTSource protocol compatibility
    but has no effect — the API always filters by the query terms.
    """

    def __init__(self, config: ScoutConfig) -> None:
        self._api_url = config.ctscout_api_url.rstrip("/")
        self._api_key = config.ctscout_api_key or ""
        self._timeout = config.http_timeout

    async def search_by_org(
        self, org_name: str, *, verify_org: bool = True
    ) -> list[dict[str, object]]:
        """Search CTScout warehouse by organization name."""
        return await self._query(company_name=org_name)

    async def search_by_domain(self, domain: str) -> list[dict[str, object]]:
        """Search CTScout warehouse by apex domain."""
        return await self._query(seed_domain=[domain])

    async def get_cert_org(self, cert_id: int) -> str | None:
        """Not supported by the warehouse API."""
        return None

    async def _query(
        self,
        company_name: str | None = None,
        seed_domain: list[str] | None = None,
    ) -> list[dict[str, object]]:
        body: dict[str, object] = {}
        if company_name:
            body["company_name"] = company_name
        if seed_domain:
            body["seed_domain"] = seed_domain
        if not body:
            return []

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._api_url}/scan",
                    json=body,
                    headers={"X-API-Key": self._api_key},
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            log.warning("ctscout_remote.query_failed", error=str(exc))
            raise

        # Convert warehouse rows to CT-compatible records
        records: list[dict[str, object]] = []
        for row in data.get("domains", []):
            apex = row.get("apex_domain")
            if not apex:
                continue
            records.append(
                {
                    "cert_id": None,
                    "org_name": row.get("org"),
                    "common_name": apex,
                    "san_dns_names": [apex],
                    "not_before": row.get("first_seen"),
                    "not_after": row.get("last_seen"),
                    "source_type": "ctscout_warehouse",
                    "cert_count": row.get("cert_count", 0),
                    "subdomain_count": row.get("subdomain_count", 0),
                }
            )

        log.debug(
            "ctscout_remote.query_ok",
            company_name=company_name,
            seed_domain=seed_domain,
            results=len(records),
        )
        return records
