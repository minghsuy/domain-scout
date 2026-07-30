"""CTScout remote API source — queries ctscout.dev warehouse."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import httpx
import structlog

if TYPE_CHECKING:
    from domain_scout.config import ScoutConfig

log = structlog.get_logger()

CTSCOUT_API_VERSION = "2026-07-11"

_RESPONSE_FIELDS = frozenset(
    {
        "domains",
        "total",
        "truncated",
        "upgrade_hint",
        "source",
        "candidates",
        "match_type",
        "org_match_strategy",
        "empty_reason",
    }
)
_DOMAIN_FIELDS = frozenset(
    {
        "org",
        "apex_domain",
        "cert_count",
        "subdomain_count",
        "first_seen",
        "last_seen",
        "is_top_org_for_apex",
        "apex_contested",
        "apex_bulk_infra",
        "dns_verified",
    }
)
_MATCH_TYPES = frozenset({"exact", "semantic", "none"})
_ORG_MATCH_STRATEGIES = frozenset(
    {"substring", "word", "normalized", "semantic", "none", "not_applicable"}
)


class CTScoutSchemaError(RuntimeError):
    """The remote response does not match the adapter's reviewed contract."""


class CTScoutSemanticFallbackUnsupportedError(RuntimeError):
    """The remote returned weak semantic suggestions that this source cannot promote."""


def _require_bool(row: Mapping[str, object], field: str) -> bool:
    value = row.get(field)
    if not isinstance(value, bool):
        raise CTScoutSchemaError(f"CTScout domain field {field!r} must be a boolean")
    return value


def _require_count(row: Mapping[str, object], field: str) -> int:
    value = row.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise CTScoutSchemaError(f"CTScout domain field {field!r} must be a non-negative integer")
    return value


def _validate_response(
    api_version: str | None,
    data: Any,
    *,
    organization_query: bool,
) -> tuple[list[Mapping[str, object]], str, str]:
    if api_version != CTSCOUT_API_VERSION:
        shown = api_version if api_version is not None else "missing"
        raise CTScoutSchemaError(
            "Unsupported CTScout X-API-Version "
            f"{shown!r}; this adapter requires {CTSCOUT_API_VERSION!r}"
        )
    if not isinstance(data, Mapping):
        raise CTScoutSchemaError("CTScout response body must be a JSON object")

    unknown_response_fields = set(data) - _RESPONSE_FIELDS
    if unknown_response_fields:
        raise CTScoutSchemaError(
            "CTScout response contains unreviewed fields: "
            + ", ".join(sorted(str(field) for field in unknown_response_fields))
        )

    match_type = data.get("match_type")
    if not isinstance(match_type, str) or match_type not in _MATCH_TYPES:
        raise CTScoutSchemaError("CTScout response has an invalid or missing match_type")
    org_match_strategy = data.get("org_match_strategy")
    if not isinstance(org_match_strategy, str) or org_match_strategy not in _ORG_MATCH_STRATEGIES:
        raise CTScoutSchemaError("CTScout response has an invalid or missing org_match_strategy")

    domains = data.get("domains")
    if not isinstance(domains, list):
        raise CTScoutSchemaError("CTScout response field 'domains' must be a list")
    if match_type == "semantic" or (not domains and data.get("candidates")):
        raise CTScoutSemanticFallbackUnsupportedError(
            "CTScout returned semantic candidates, but the Domain Scout CT source only "
            "accepts authoritative warehouse rows; corroborate the candidates separately"
        )
    if domains:
        if match_type != "exact":
            raise CTScoutSchemaError(
                "CTScout returned domains outside the authoritative exact-match path"
            )
        authoritative_strategies = (
            {"substring", "word", "normalized"} if organization_query else {"not_applicable"}
        )
        if org_match_strategy not in authoritative_strategies:
            query_kind = "organization" if organization_query else "seed-domain"
            raise CTScoutSchemaError(
                f"CTScout returned a nonmatching strategy for a {query_kind} query"
            )

    validated_domains: list[Mapping[str, object]] = []
    for index, row in enumerate(domains):
        if not isinstance(row, Mapping):
            raise CTScoutSchemaError(f"CTScout domains[{index}] must be a JSON object")
        unknown_domain_fields = set(row) - _DOMAIN_FIELDS
        if unknown_domain_fields:
            raise CTScoutSchemaError(
                f"CTScout domains[{index}] contains unreviewed fields: "
                + ", ".join(sorted(str(field) for field in unknown_domain_fields))
            )
        validated_domains.append(row)

    return validated_domains, match_type, org_match_strategy


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
        self, org_name: str, *, verify_org: bool = True, client: httpx.AsyncClient | None = None
    ) -> list[dict[str, object]]:
        """Search CTScout warehouse by organization name.

        ``client`` is accepted for CTSource protocol compatibility (#166) but
        unused — this source POSTs to a different host (ctscout.dev) with its
        own per-call client and API-key header.
        """
        return await self._query(company_name=org_name)

    async def search_by_domain(
        self, domain: str, client: httpx.AsyncClient | None = None
    ) -> list[dict[str, object]]:
        """Search CTScout warehouse by apex domain.

        ``client`` is accepted for CTSource protocol compatibility (#166) but
        unused — see ``search_by_org``.
        """
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
                api_version = resp.headers.get("X-API-Version")
        except Exception as exc:
            log.warning("ctscout_remote.query_failed", error=str(exc))
            raise

        rows, match_type, org_match_strategy = _validate_response(
            api_version,
            data,
            organization_query=company_name is not None,
        )

        # Convert warehouse rows to CT-compatible records
        records: list[dict[str, object]] = []
        for row in rows:
            apex = row.get("apex_domain")
            if not isinstance(apex, str) or not apex:
                continue
            org = row.get("org")
            if not isinstance(org, str) or not org:
                raise CTScoutSchemaError("CTScout domain field 'org' must be a non-empty string")
            cert_count = _require_count(row, "cert_count")
            subdomain_count = _require_count(row, "subdomain_count")
            is_top_org_for_apex = _require_bool(row, "is_top_org_for_apex")
            apex_contested = _require_bool(row, "apex_contested")
            apex_bulk_infra = _require_bool(row, "apex_bulk_infra")
            dns_verified = _require_bool(row, "dns_verified")
            records.append(
                {
                    "cert_id": None,
                    "org_name": org,
                    "common_name": apex,
                    "san_dns_names": [apex],
                    "not_before": row.get("first_seen"),
                    "not_after": row.get("last_seen"),
                    "source_type": "ctscout_warehouse",
                    "cert_count": cert_count,
                    "subdomain_count": subdomain_count,
                    "ctscout_attribution": {
                        "api_version": api_version,
                        "org": org,
                        "apex_domain": apex,
                        "match_type": match_type,
                        "org_match_strategy": org_match_strategy,
                        "cert_count": cert_count,
                        "subdomain_count": subdomain_count,
                        "is_top_org_for_apex": is_top_org_for_apex,
                        "apex_contested": apex_contested,
                        "apex_bulk_infra": apex_bulk_infra,
                        "dns_verified": dns_verified,
                        "cert_volume_attribution_safe": (
                            is_top_org_for_apex
                            and not apex_contested
                            and not apex_bulk_infra
                            and dns_verified
                        ),
                    },
                }
            )

        log.debug(
            "ctscout_remote.query_ok",
            company_name=company_name,
            seed_domain=seed_domain,
            results=len(records),
        )
        return records
