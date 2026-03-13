import re

with open("domain_scout/scout.py", "r") as f:
    text = f.read()

# We need to replace the body of _run_strategies
start_idx = text.find("    async def _run_strategies(self, ctx: _DiscoveryContext) -> None:")
end_idx = text.find("    async def _resolve_dns_and_rdap(self, ctx: _DiscoveryContext) -> None:")

new_methods = """    async def _run_strategies(self, ctx: _DiscoveryContext) -> None:
        independent_tasks = self._start_independent_strategies(ctx)
        seed_tasks = self._start_seed_validation(ctx)

        if seed_tasks:
            await self._wait_for_seed_validation(ctx, seed_tasks)

        dependent_tasks = self._start_dependent_strategies(ctx)

        all_strategy_tasks = independent_tasks + dependent_tasks
        await self._wait_for_strategies(ctx, all_strategy_tasks)

        if len(ctx.seeds) > 1:
            self._apply_cross_seed_boost(ctx.domain_evidence, ctx.seeds)

    def _start_independent_strategies(
        self, ctx: _DiscoveryContext
    ) -> list[asyncio.Task[list[tuple[str, "_DomainAccum"]]]]:
        tasks: list[asyncio.Task[list[tuple[str, "_DomainAccum"]]]] = []
        tasks.append(
            asyncio.create_task(
                self._strategy_org_search(ctx.entity.company_name, ctx.errors),
                name="org_search",
            )
        )
        tasks.append(
            asyncio.create_task(
                self._strategy_domain_guess(
                    ctx.entity.company_name, ctx.entity.location, ctx.errors
                ),
                name="domain_guess",
            )
        )

        if self._subsidiaries:
            sub_names = self._match_subsidiaries(ctx.entity.company_name)
            for sub_name in sub_names[: self.config.subsidiary_max_queries]:
                tasks.append(
                    asyncio.create_task(
                        self._strategy_org_search(
                            sub_name,
                            ctx.errors,
                            source_tag="ct_subsidiary_match",
                        ),
                        name=f"subsidiary:{sub_name[:30]}",
                    )
                )
        return tasks

    def _start_seed_validation(
        self, ctx: _DiscoveryContext
    ) -> dict[str, asyncio.Task[dict[str, Any]]]:
        seed_to_base = {s: extract_base_domain(s) for s in ctx.seeds}
        seed_tasks: dict[str, asyncio.Task[dict[str, Any]]] = {}
        for sd in ctx.seeds:
            seed_tasks[sd] = asyncio.create_task(
                self._validate_seed(sd, ctx.entity.company_name, ctx.seeds, ctx.errors, seed_to_base),
                name=f"seed_validation:{sd}",
            )
        return seed_tasks

    async def _wait_for_seed_validation(
        self, ctx: _DiscoveryContext, seed_tasks: dict[str, asyncio.Task[dict[str, Any]]]
    ) -> None:
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

    def _start_dependent_strategies(
        self, ctx: _DiscoveryContext
    ) -> list[asyncio.Task[list[tuple[str, "_DomainAccum"]]]]:
        dependent_tasks: list[asyncio.Task[list[tuple[str, "_DomainAccum"]]]] = []
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
        return dependent_tasks

    async def _wait_for_strategies(
        self, ctx: _DiscoveryContext, tasks: list[asyncio.Task[list[tuple[str, "_DomainAccum"]]]]
    ) -> None:
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

        strategy_budget = ctx.remaining_time() - 10.0
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=max(1.0, strategy_budget),
            )
            _collect(results)
        except TimeoutError:
            ctx.timed_out = True
            ctx.errors.append("Strategy phase timed out")
            for task in tasks:
                if task.done() and not task.cancelled():
                    exc = task.exception()
                    if exc:
                        ctx.errors.append(str(exc))
                    else:
                        _collect([task.result()])
                elif not task.done():
                    task.cancel()

"""

final_text = text[:start_idx] + new_methods + text[end_idx:]

with open("domain_scout/scout.py", "w") as f:
    f.write(final_text)
