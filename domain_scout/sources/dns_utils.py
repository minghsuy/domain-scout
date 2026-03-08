"""DNS resolution and infrastructure comparison utilities."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import dns.asyncresolver
import dns.exception
import dns.name
import dns.rdatatype
import httpx
import structlog

if TYPE_CHECKING:
    from domain_scout.config import ScoutConfig

log = structlog.get_logger()


class DNSChecker:
    """Async DNS resolution and infrastructure checks."""

    def __init__(self, config: ScoutConfig) -> None:
        self._cfg = config
        self._resolver = dns.asyncresolver.Resolver()
        self._resolver.nameservers = config.dns_nameservers
        self._resolver.lifetime = config.dns_timeout
        self._ns_cache: dict[str, asyncio.Task[tuple[str, ...]]] = {}
        self._ips_cache: dict[str, asyncio.Task[tuple[str, ...]]] = {}

    async def resolves(self, domain: str) -> bool:
        """Check whether a domain resolves to any A or AAAA record."""
        for rdtype in (dns.rdatatype.A, dns.rdatatype.AAAA):
            try:
                await self._resolver.resolve(domain, rdtype)
                return True
            except (dns.exception.DNSException, ValueError):
                continue
        log.debug("dns.no_resolution", domain=domain)
        return False

    async def _get_ips_uncached(self, domain: str) -> tuple[str, ...]:
        ips: list[str] = []
        for rdtype in (dns.rdatatype.A, dns.rdatatype.AAAA):
            try:
                answer = await self._resolver.resolve(domain, rdtype)
                ips.extend(rr.to_text() for rr in answer)
            except (dns.exception.DNSException, ValueError):
                continue
        return tuple(ips)

    async def get_ips(self, domain: str) -> list[str]:
        """Return all A/AAAA addresses for a domain."""
        if domain not in self._ips_cache:
            self._ips_cache[domain] = asyncio.create_task(self._get_ips_uncached(domain))
        return list(await self._ips_cache[domain])

    async def _get_nameservers_uncached(self, domain: str) -> tuple[str, ...]:
        try:
            answer = await self._resolver.resolve(domain, dns.rdatatype.NS)
            return tuple(sorted(rr.to_text().rstrip(".").lower() for rr in answer))
        except (dns.exception.DNSException, ValueError):
            return ()

    async def get_nameservers(self, domain: str) -> list[str]:
        """Return NS records for a domain."""
        if domain not in self._ns_cache:
            self._ns_cache[domain] = asyncio.create_task(
                self._get_nameservers_uncached(domain)
            )
        return list(await self._ns_cache[domain])

    async def shares_infrastructure(self, domain_a: str, domain_b: str) -> bool:
        """Check if two domains share nameservers or IP ranges."""
        ns_a, ns_b = await asyncio.gather(
            self.get_nameservers(domain_a),
            self.get_nameservers(domain_b),
        )
        if ns_a and ns_b and set(ns_a) & set(ns_b):
            return True

        ips_a, ips_b = await asyncio.gather(
            self.get_ips(domain_a),
            self.get_ips(domain_b),
        )
        # Compare /24 prefixes for IPv4
        prefixes_a = {ip.rsplit(".", 1)[0] for ip in ips_a if "." in ip}
        prefixes_b = {ip.rsplit(".", 1)[0] for ip in ips_b if "." in ip}
        return bool(prefixes_a & prefixes_b)

    async def bulk_resolve(self, domains: list[str]) -> dict[str, bool]:
        """Resolve many domains concurrently. Returns {domain: resolves}."""
        sem = asyncio.Semaphore(self._cfg.max_concurrent_queries)

        async def _check(d: str) -> tuple[str, bool]:
            async with sem:
                return d, await self.resolves(d)

        results = await asyncio.gather(*[_check(d) for d in domains])
        return dict(results)

    async def geodns_resolve(self, domain: str, client: httpx.AsyncClient) -> bool:
        """Check if a domain resolves from any global location via Shodan GeoDNS."""
        url = f"{self._cfg.geodns_base_url}/{domain}"
        try:
            resp = await client.get(url)
            if resp.status_code == 500:
                # Shodan returns HTTP 500 for NXDOMAIN
                return False
            resp.raise_for_status()
            data = resp.json()
            return any(isinstance(entry, dict) and entry.get("answers") for entry in data)
        except (httpx.HTTPError, ValueError):
            log.debug("geodns.error", domain=domain)
        return False

    async def bulk_geodns_resolve(
        self, domains: list[str], client: httpx.AsyncClient
    ) -> dict[str, bool]:
        """Resolve many domains via GeoDNS with concurrency limits."""
        sem = asyncio.Semaphore(self._cfg.geodns_concurrency)

        async def _check(d: str) -> tuple[str, bool]:
            async with sem:
                result = await self.geodns_resolve(d, client)
                await asyncio.sleep(self._cfg.geodns_delay)
                return d, result

        results = await asyncio.gather(*[_check(d) for d in domains])
        return dict(results)
