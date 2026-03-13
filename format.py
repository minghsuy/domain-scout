import re

with open("domain_scout/scout.py", "r") as f:
    text = f.read()

# I'm refactoring out some complexity.
start_idx = text.find("    async def _wait_for_seed_validation(")
end_idx = text.find("    def _start_dependent_strategies(")

new_methods = """    async def _wait_for_seed_validation(
        self, ctx: _DiscoveryContext, seed_tasks: dict[str, asyncio.Task[dict[str, Any]]]
    ) -> None:
        try:
            seed_results = await asyncio.wait_for(
                asyncio.gather(*seed_tasks.values(), return_exceptions=True),
                timeout=min(15.0, ctx.remaining_time()),
            )
            for sd, result in zip(seed_tasks.keys(), seed_results, strict=True):
                self._handle_seed_result(ctx, sd, result)
        except TimeoutError:
            ctx.errors.append("Seed validation timed out")
            for sd, stask in seed_tasks.items():
                self._handle_seed_timeout(ctx, sd, stask)

    def _handle_seed_result(self, ctx: _DiscoveryContext, sd: str, result: Any) -> None:
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

    def _handle_seed_timeout(self, ctx: _DiscoveryContext, sd: str, stask: asyncio.Task[dict[str, Any]]) -> None:
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

"""

final_text = text[:start_idx] + new_methods + text[end_idx:]

with open("domain_scout/scout.py", "w") as f:
    f.write(final_text)
