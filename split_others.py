import re

with open("domain_scout/scout.py", "r") as f:
    text = f.read()

# Replace _resolve_dns_and_rdap
start_idx = text.find("    async def _resolve_dns_and_rdap(self, ctx: _DiscoveryContext) -> None:")
end_idx = text.find("    async def _score_and_boost(self, ctx: _DiscoveryContext) -> list[str]:")

new_methods = """    async def _resolve_dns_and_rdap(self, ctx: _DiscoveryContext) -> None:
        await self._resolve_dns_bulk(ctx)
        await self._resolve_geodns_rescue(ctx)
        await self._resolve_rdap_corroboration(ctx)

    async def _resolve_dns_bulk(self, ctx: _DiscoveryContext) -> None:
        all_domains = list(ctx.domain_evidence.keys())
        if not all_domains or ctx.remaining_time() <= 2.0:
            return
        try:
            resolve_map = await asyncio.wait_for(
                self._dns.bulk_resolve(all_domains),
                timeout=ctx.remaining_time() - 2.0,
            )
            for domain, resolves in resolve_map.items():
                ctx.domain_evidence[domain].resolves = resolves
        except TimeoutError:
            ctx.timed_out = True
            ctx.errors.append("DNS resolution timed out")

    async def _resolve_geodns_rescue(self, ctx: _DiscoveryContext) -> None:
        if not self.config.deep_mode or ctx.remaining_time() <= 3.0:
            return
        failed_domains = [d for d, acc in ctx.domain_evidence.items() if not acc.resolves]
        if not failed_domains:
            return
        log.info("scout.geodns_rescue", count=len(failed_domains))
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                geodns_map = await asyncio.wait_for(
                    self._dns.bulk_geodns_resolve(failed_domains, client),
                    timeout=max(1.0, ctx.remaining_time() - 3.0),
                )
            for domain, did_resolve in geodns_map.items():
                if not did_resolve:
                    continue
                accum = ctx.domain_evidence[domain]
                accum.resolves = True
                accum.sources.add("geodns")
                accum.evidence.append(
                    EvidenceRecord(
                        source_type="geodns",
                        description="Resolved via Shodan GeoDNS (global)",
                    )
                )
                log.info("scout.geodns_rescued", domain=domain)
        except TimeoutError:
            ctx.timed_out = True
            ctx.errors.append("GeoDNS resolution timed out")

    async def _resolve_rdap_corroboration(self, ctx: _DiscoveryContext) -> None:
        if ctx.remaining_time() <= 5.0:
            return
        try:
            await asyncio.wait_for(
                self._rdap_corroborate(ctx.domain_evidence, ctx.entity.company_name),
                timeout=min(15.0, ctx.remaining_time() - 3.0),
            )
        except TimeoutError:
            ctx.errors.append("RDAP corroboration timed out")

"""

final_text = text[:start_idx] + new_methods + text[end_idx:]

with open("domain_scout/scout.py", "w") as f:
    f.write(final_text)
