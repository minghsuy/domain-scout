import re

with open("domain_scout/scout.py", "r") as f:
    text = f.read()

# We need to replace the body of _discover
discover_start = text.find("    async def _discover(self, entity: EntityInput) -> ScoutResult:")
validate_seed_start = text.find("    # --- Step 1: Seed validation ---")

new_discover = """    async def _discover(self, entity: EntityInput) -> ScoutResult:
        self._dns.reset()
        t0 = time.monotonic()

        ctx = _DiscoveryContext(
            entity=entity,
            start_time=t0,
            total_budget=self.config.total_timeout,
            seeds=entity.seed_domain,
        )

        await self._run_strategies(ctx)
        await self._resolve_dns_and_rdap(ctx)
        confirmed_domains = await self._score_and_boost(ctx)
        return self._finalize(ctx, confirmed_domains)

    async def _run_strategies(self, ctx: _DiscoveryContext) -> None:
        def _collect(results: list[Any]) -> None:
            for result in results:
                if isinstance(result, BaseException):
                    ctx.errors.append(str(result))
                    continue
                for domain, accum in result:
                    if domain in ctx.domain_evidence:
                        ctx.domain_evidence[domain].merge(accum)
                    else:
                        ctx.domain_evidence[domain] = accum

        # Phase 1: Seed validation + independent strategies run in parallel.
        independent_tasks: list[asyncio.Task[list[tuple[str, _DomainAccum]]]] = []

        independent_tasks.append(
            asyncio.create_task(
                self._strategy_org_search(ctx.entity.company_name, ctx.errors),
                name="org_search",
            )
        )

        independent_tasks.append(
            asyncio.create_task(
                self._strategy_domain_guess(ctx.entity.company_name, ctx.entity.location, ctx.errors),
                name="domain_guess",
            )
        )

        if self._subsidiaries:
            sub_names = self._match_subsidiaries(ctx.entity.company_name)
            for sub_name in sub_names[: self.config.subsidiary_max_queries]:
                independent_tasks.append(
                    asyncio.create_task(
                        self._strategy_org_search(
                            sub_name,
                            ctx.errors,
                            source_tag="ct_subsidiary_match",
                        ),
                        name=f"subsidiary:{sub_name[:30]}",
                    )
                )

        seed_to_base = {s: extract_base_domain(s) for s in ctx.seeds}

        seed_tasks: dict[str, asyncio.Task[dict[str, Any]]] = {}
        for sd in ctx.seeds:
            seed_tasks[sd] = asyncio.create_task(
                self._validate_seed(sd, ctx.entity.company_name, ctx.seeds, ctx.errors, seed_to_base),
                name=f"seed_validation:{sd}",
            )

        if seed_tasks:
            try:
                seed_results = await asyncio.wait_for(
                    asyncio.gather(*seed_tasks.values(), return_exceptions=True),
                    timeout=min(15.0, ctx.remaining_time()),
                )
                for sd, result in zip(seed_tasks.keys(), seed_results, strict=True):
                    if isinstance(result, BaseException):
                        ctx.errors.append(f"Seed validation failed for {sd}: {result}")
                        ctx.seed_assessments[sd] = "error"
                    else:
                        ctx.seed_assessments[sd] = result["assessment"]
                        ctx.seed_org_names[sd] = result["org_name"]
                        if result.get("co_hosted_seeds"):
                            ctx.seed_cross_verification[sd] = result["co_hosted_seeds"]
                        if result.get("ct_records"):
                            ctx.seed_ct_records[sd] = result["ct_records"]
                        log.info(
                            "scout.seed_validated",
                            seed=sd,
                            assessment=result["assessment"],
                            seed_org=result["org_name"],
                            co_hosted=result.get("co_hosted_seeds", []),
                        )
            except TimeoutError:
                ctx.errors.append("Seed validation timed out")
                for sd, stask in seed_tasks.items():
                    if not stask.done():
                        stask.cancel()
                        ctx.seed_assessments.setdefault(sd, "timeout")
                    elif stask.cancelled():
                        ctx.seed_assessments.setdefault(sd, "timeout")
                    else:
                        exc = stask.exception()
                        if exc:
                            ctx.seed_assessments.setdefault(sd, "error")
                        else:
                            r = stask.result()
                            ctx.seed_assessments.setdefault(sd, r["assessment"])
                            ctx.seed_org_names.setdefault(sd, r["org_name"])
                            if r.get("co_hosted_seeds"):
                                ctx.seed_cross_verification.setdefault(sd, r["co_hosted_seeds"])
                            if r.get("ct_records"):
                                ctx.seed_ct_records.setdefault(sd, r["ct_records"])

        dependent_tasks: list[asyncio.Task[list[tuple[str, _DomainAccum]]]] = []

        seen_org_searches: set[str] = set()
        for sd, org_name in ctx.seed_org_names.items():
            if (
                org_name
                and org_name not in seen_org_searches
                and org_name_similarity(org_name, ctx.entity.company_name) < 0.95
            ):
                seen_org_searches.add(org_name)
                dependent_tasks.append(
                    asyncio.create_task(
                        self._strategy_org_search(org_name, ctx.errors),
                        name=f"org_search_seed:{sd}",
                    )
                )

        for sd in ctx.seeds:
            dependent_tasks.append(
                asyncio.create_task(
                    self._strategy_seed_expansion(
                        sd,
                        ctx.entity.company_name,
                        ctx.errors,
                        ct_records=ctx.seed_ct_records.get(sd),
                    ),
                    name=f"seed_expansion:{sd}",
                )
            )

        all_strategy_tasks = independent_tasks + dependent_tasks
        strategy_budget = ctx.remaining_time() - 10.0
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*all_strategy_tasks, return_exceptions=True),
                timeout=max(1.0, strategy_budget),
            )
            _collect(results)
        except TimeoutError:
            ctx.timed_out = True
            ctx.errors.append("Strategy phase timed out")
            for task in all_strategy_tasks:
                if task.done() and not task.cancelled():
                    exc = task.exception()
                    if exc:
                        ctx.errors.append(str(exc))
                    else:
                        _collect([task.result()])
                elif not task.done():
                    task.cancel()

        if len(ctx.seeds) > 1:
            self._apply_cross_seed_boost(ctx.domain_evidence, ctx.seeds)

    async def _resolve_dns_and_rdap(self, ctx: _DiscoveryContext) -> None:
        all_domains = list(ctx.domain_evidence.keys())
        if all_domains and ctx.remaining_time() > 2.0:
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

        if self.config.deep_mode and ctx.remaining_time() > 3.0:
            failed_domains = [d for d, acc in ctx.domain_evidence.items() if not acc.resolves]
            if failed_domains:
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

        if ctx.remaining_time() > 5.0:
            try:
                await asyncio.wait_for(
                    self._rdap_corroborate(ctx.domain_evidence, ctx.entity.company_name),
                    timeout=min(15.0, ctx.remaining_time() - 3.0),
                )
            except TimeoutError:
                ctx.errors.append("RDAP corroboration timed out")

    async def _score_and_boost(self, ctx: _DiscoveryContext) -> list[str]:
        confirmed_domains: list[str] = []
        for domain, accum in ctx.domain_evidence.items():
            accum.confidence = self._score_confidence(
                accum,
                ctx.entity.company_name,
                ctx.seeds,
                domain=domain,
            )
            if accum.confidence >= self.config.seed_confirm_threshold:
                confirmed_domains.append(domain)

        reference: str | None = None
        if ctx.seeds:
            best_seed_conf = -1.0
            for sd in ctx.seeds:
                sd_base = extract_base_domain(sd)
                if sd_base and sd_base in ctx.domain_evidence:
                    conf = ctx.domain_evidence[sd_base].confidence
                    if conf > best_seed_conf:
                        best_seed_conf = conf
                        reference = sd_base
            if reference is None:
                reference = extract_base_domain(ctx.seeds[0])
        if not reference and confirmed_domains:
            reference = confirmed_domains[0]
        if reference and len(ctx.domain_evidence) <= 50 and ctx.remaining_time() > 1.0:
            await self._infra_boost(reference, ctx.domain_evidence)

        return confirmed_domains

    def _finalize(self, ctx: _DiscoveryContext, confirmed_domains: list[str]) -> ScoutResult:
        domains = self._build_output(ctx.domain_evidence, ctx.seeds)
        elapsed = time.monotonic() - ctx.start_time

        status = "timeout" if ctx.timed_out else ("error" if ctx.errors else "ok")
        inc(SCANS_TOTAL, status=status)
        observe(SCAN_DURATION_SECONDS, elapsed)
        observe(DOMAINS_FOUND, float(len(domains)))

        run_meta = RunMetadata(
            tool_version=_TOOL_VERSION,
            timestamp=datetime.now(UTC),
            elapsed_seconds=round(elapsed, 2),
            domains_found=len(domains),
            timed_out=ctx.timed_out,
            seed_count=len(ctx.seeds),
            errors=ctx.errors,
            config=self.config.to_dict(),
        )
        return ScoutResult(
            entity=ctx.entity,
            domains=domains,
            seed_domain_assessment=ctx.seed_assessments,
            seed_cross_verification=ctx.seed_cross_verification,
            run_metadata=run_meta,
        )
"""

final_text = text[:discover_start] + new_discover + "\n" + text[validate_seed_start:]

with open("domain_scout/scout.py", "w") as f:
    f.write(final_text)
