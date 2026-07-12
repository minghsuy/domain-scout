"""Tests for #166 (one pooled httpx client per scan) and #167 (phase errors
are recorded, not silently swallowed).

#166: RDAP and CT-JSON fetchers reuse a caller-owned client when one is
injected, and fall back to a per-call client otherwise; a single scan
constructs exactly one client and closes it.

#167: the infra / rdap / fingerprint corroboration checks record swallowed
exceptions to the scan error channel (RunMetadata.errors) instead of dropping
them, and the scan still completes.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from domain_scout.config import ScoutConfig
from domain_scout.models import EntityInput
from domain_scout.scout import Scout, _DomainAccum
from domain_scout.sources.ct_logs import CTLogSource
from domain_scout.sources.rdap import RDAPLookup


def _response(json_payload: Any) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = json_payload
    return resp


def _injected_client(json_payload: Any) -> AsyncMock:
    """A caller-owned client (never used as a context manager in the hot path)."""
    client = AsyncMock()
    client.get.return_value = _response(json_payload)
    return client


def _ctx_client(json_payload: Any) -> AsyncMock:
    """A per-call client mock supporting `async with` (the fallback path)."""
    client = AsyncMock()
    client.get.return_value = _response(json_payload)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


# --------------------------------------------------------------------------- #
# #166 — source-level injected client reuse
# --------------------------------------------------------------------------- #

_RDAP_DATA = {
    "entities": [
        {
            "roles": ["registrant"],
            "vcardArray": [
                "vcard",
                [["version", {}, "text", "4.0"], ["org", {}, "text", "Example Corp"]],
            ],
        }
    ]
}


class TestRDAPInjectedClient:
    @pytest.mark.asyncio
    async def test_reuses_injected_client_without_constructing_one(self) -> None:
        RDAPLookup._breaker = None  # fresh, closed breaker
        rdap = RDAPLookup(ScoutConfig())
        injected = _injected_client(_RDAP_DATA)

        with patch("domain_scout.sources.rdap.httpx.AsyncClient") as ctor:
            org = await rdap.get_registrant_org("example.com", client=injected)

        assert org == "Example Corp"
        injected.get.assert_awaited_once()
        # rdap.org 302-redirects to the registry server: follow_redirects must
        # be applied per-request on the shared client (a MockTransport would
        # not redirect, so assert it explicitly).
        _, kwargs = injected.get.call_args
        assert kwargs.get("follow_redirects") is True
        # No per-call client constructed when one is injected.
        ctor.assert_not_called()

    @pytest.mark.asyncio
    async def test_falls_back_to_own_client_when_none_injected(self) -> None:
        RDAPLookup._breaker = None
        rdap = RDAPLookup(ScoutConfig())
        own = _ctx_client(_RDAP_DATA)

        with patch("domain_scout.sources.rdap.httpx.AsyncClient", return_value=own) as ctor:
            org = await rdap.get_registrant_org("example.com")

        assert org == "Example Corp"
        ctor.assert_called_once()  # standalone path still constructs + closes its own
        own.__aexit__.assert_awaited()


class TestCTJSONInjectedClient:
    _PAYLOAD = [
        {
            "id": 1,
            "common_name": "example.com",
            "name_value": "example.com\nwww.example.com",
            "not_before": "2020-01-01T00:00:00",
            "not_after": "2030-01-01T00:00:00",
        }
    ]

    @pytest.mark.asyncio
    async def test_json_query_reuses_injected_client(self) -> None:
        CTLogSource._breaker = None
        ct = CTLogSource(ScoutConfig())
        injected = _injected_client(self._PAYLOAD)

        with patch("domain_scout.sources.ct_logs.httpx.AsyncClient") as ctor:
            records = await ct._json_query("example.com", client=injected)

        assert [r["common_name"] for r in records] == ["example.com"]
        injected.get.assert_awaited_once()
        ctor.assert_not_called()

    @pytest.mark.asyncio
    async def test_json_query_falls_back_to_own_client(self) -> None:
        CTLogSource._breaker = None
        ct = CTLogSource(ScoutConfig())
        own = _ctx_client(self._PAYLOAD)

        with patch("domain_scout.sources.ct_logs.httpx.AsyncClient", return_value=own) as ctor:
            records = await ct._json_query("example.com")

        assert [r["common_name"] for r in records] == ["example.com"]
        ctor.assert_called_once()
        own.__aexit__.assert_awaited()


# --------------------------------------------------------------------------- #
# #166 — one client per scan, shared and closed
# --------------------------------------------------------------------------- #


class TestScanSharesOneClient:
    @pytest.mark.asyncio
    async def test_scan_constructs_one_client_shares_it_and_closes_it(self) -> None:
        scout = Scout(config=ScoutConfig())
        seen: list[tuple[str, object]] = []

        async def ct_by_domain(domain: str, client: object = None) -> list[dict[str, object]]:
            seen.append(("ct_domain", client))
            return []

        async def ct_by_org(
            org_name: str, *, verify_org: bool = True, client: object = None
        ) -> list[dict[str, object]]:
            seen.append(("ct_org", client))
            return []

        async def rdap_org(domain: str, client: object = None) -> str | None:
            seen.append(("rdap", client))
            return None

        scout._ct.search_by_domain = AsyncMock(side_effect=ct_by_domain)  # type: ignore[method-assign]
        scout._ct.search_by_org = AsyncMock(side_effect=ct_by_org)  # type: ignore[method-assign]
        scout._rdap.get_registrant_org = AsyncMock(side_effect=rdap_org)  # type: ignore[method-assign]
        scout._dns.resolves = AsyncMock(return_value=False)  # type: ignore[method-assign]
        scout._dns.bulk_resolve = AsyncMock(return_value={})  # type: ignore[method-assign]

        fake = AsyncMock()
        fake.__aenter__ = AsyncMock(return_value=fake)
        fake.__aexit__ = AsyncMock(return_value=False)
        ctor = MagicMock(return_value=fake)

        with patch("domain_scout.scout.httpx.AsyncClient", ctor):
            await scout.discover_async(EntityInput(company_name="TestCo", seed_domain=["test.com"]))

        # Exactly one client per scan.
        assert ctor.call_count == 1
        # Sources were invoked, all with the same shared client instance.
        assert seen
        assert all(client is fake for _, client in seen)
        # Client closed after the scan (async with exit).
        fake.__aexit__.assert_awaited()


# --------------------------------------------------------------------------- #
# #167 — corroboration checks record errors instead of swallowing them
# --------------------------------------------------------------------------- #


def _candidate(confidence: float = 0.5, *, resolves: bool = True) -> _DomainAccum:
    accum = _DomainAccum()
    accum.confidence = confidence
    accum.resolves = resolves
    accum.sources.add("ct_org_match")
    return accum


class TestPhaseCheckErrorsRecorded:
    @pytest.mark.asyncio
    async def test_infra_boost_records_check_error_and_continues(self) -> None:
        scout = Scout(config=ScoutConfig())
        scout._dns.shares_infrastructure = AsyncMock(  # type: ignore[method-assign]
            side_effect=RuntimeError("shares boom")
        )
        evidence = {"candidate.com": _candidate()}
        errors: list[str] = []

        # Must not raise — a boost-check fault is best-effort.
        await scout._infra_boost("reference.com", evidence, errors)

        assert any("candidate.com" in e for e in errors)
        assert "shared_infra" not in evidence["candidate.com"].sources

    @pytest.mark.asyncio
    async def test_rdap_corroborate_records_check_error(self) -> None:
        # In production get_registrant_org swallows RDAP faults internally and
        # returns None, so the _check except fires only on a genuine post-lookup
        # fault (parser/cache bug). This mocks that by making the call raise.
        scout = Scout(config=ScoutConfig())
        scout._rdap.get_registrant_org = AsyncMock(  # type: ignore[method-assign]
            side_effect=RuntimeError("rdap boom")
        )
        evidence = {"x.com": _candidate()}
        errors: list[str] = []

        await scout._rdap_corroborate(evidence, "X Corp", errors)

        assert any("x.com" in e for e in errors)
        assert "rdap_registrant_match" not in evidence["x.com"].sources

    @pytest.mark.asyncio
    async def test_fingerprint_corroborate_records_check_error(self) -> None:
        scout = Scout(config=ScoutConfig(discovery_mode="fingerprint"))

        seed_fp = MagicMock()
        seed_fp.has_signals = True
        seed_fp.mx_tenants = []
        seed_fp.ns_zones = []
        seed_fp.ip_prefixes = []
        seed_fp.spf_includes = []

        async def fake_extract(domain: str, _dns: object) -> object:
            if domain == "seed.com":
                return seed_fp
            raise RuntimeError("fingerprint boom")

        evidence = {"candidate.com": _candidate()}
        errors: list[str] = []

        with patch("domain_scout.scout.extract_fingerprint", side_effect=fake_extract):
            await scout._fingerprint_corroborate(evidence, ["seed.com"], errors)

        assert any("candidate.com" in e for e in errors)

    @pytest.mark.asyncio
    async def test_fingerprint_seed_extraction_error_recorded(self) -> None:
        # A seed-extraction fault must surface on the error channel rather than
        # being masked as "no seed signals" (#167 seed-side symmetry).
        scout = Scout(config=ScoutConfig(discovery_mode="fingerprint"))

        async def fake_extract(_domain: str, _dns: object) -> object:
            raise RuntimeError("seed fingerprint boom")

        evidence = {"candidate.com": _candidate()}
        errors: list[str] = []

        with patch("domain_scout.scout.extract_fingerprint", side_effect=fake_extract):
            await scout._fingerprint_corroborate(evidence, ["seed.com"], errors)

        assert any("seed.com" in e for e in errors)
