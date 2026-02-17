"""CLI interface for domain-scout."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Annotated

import typer

from domain_scout._logging import configure_logging
from domain_scout.config import ScoutConfig
from domain_scout.scout import Scout

if TYPE_CHECKING:
    from domain_scout.models import ScoutResult

app = typer.Typer(
    name="domain-scout",
    help="Discover internet domains associated with a business entity.",
    no_args_is_help=True,
)

cache_app = typer.Typer(help="Manage the query cache.")
app.add_typer(cache_app, name="cache")


@app.command()
def scout(
    name: Annotated[str, typer.Option("--name", "-n", help="Company name to search for")],
    location: Annotated[
        str | None, typer.Option("--location", "-l", help="City, state, country")
    ] = None,
    seed: Annotated[
        list[str] | None, typer.Option("--seed", "-s", help="Seed domain(s), repeatable")
    ] = None,
    industry: Annotated[str | None, typer.Option("--industry", "-i", help="Industry hint")] = None,
    deep: Annotated[
        bool,
        typer.Option("--deep", "-d", help="GeoDNS global resolution"),
    ] = False,
    output: Annotated[
        str, typer.Option("--output", "-o", help="Output format: table or json")
    ] = "table",
    timeout: Annotated[int, typer.Option("--timeout", help="Total timeout in seconds")] = 120,
    profile: Annotated[
        str | None,
        typer.Option("--profile", "-p", help="Discovery profile: broad, balanced, strict"),
    ] = None,
    use_cache: Annotated[
        bool, typer.Option("--cache/--no-cache", help="Enable query cache")
    ] = False,
    cache_dir: Annotated[str | None, typer.Option("--cache-dir", help="Cache directory")] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Verbose logging")] = False,
) -> None:
    """Discover domains associated with a company."""
    configure_logging(level=logging.DEBUG if verbose else logging.INFO)

    seeds = seed or []
    if deep:
        timeout = max(timeout, 180)
    if len(seeds) >= 3:
        timeout = max(timeout, 150)
    if profile:
        config = ScoutConfig.from_profile(profile, total_timeout=timeout, deep_mode=deep)  # type: ignore[arg-type]
    else:
        config = ScoutConfig(total_timeout=timeout, deep_mode=deep)

    cache = None
    if use_cache:
        from domain_scout.cache import DuckDBCache

        cache = DuckDBCache(cache_dir=cache_dir)

    try:
        s = Scout(config=config, cache=cache)
        result = s.discover(
            company_name=name,
            location=location,
            seed_domain=seeds,
            industry=industry,
        )
    except KeyboardInterrupt:
        typer.echo("\nAborted.", err=True)
        raise typer.Exit(1) from None
    finally:
        if cache:
            cache.close()

    if output == "json":
        typer.echo(result.model_dump_json(indent=2))
    else:
        _print_table(result)


@app.command()
def serve(
    host: Annotated[str, typer.Option("--host", help="Bind address")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", help="Bind port")] = 8080,
    workers: Annotated[int, typer.Option("--workers", help="Uvicorn workers")] = 1,
    max_concurrent: Annotated[
        int, typer.Option("--max-concurrent", help="Max concurrent scans")
    ] = 3,
    no_cache: Annotated[bool, typer.Option("--no-cache", help="Disable query cache")] = False,
    cache_dir: Annotated[str | None, typer.Option("--cache-dir", help="Cache directory")] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Start the REST API server."""
    import os

    import uvicorn

    configure_logging(level=logging.DEBUG if verbose else logging.INFO)

    # DuckDB is embedded single-writer — force workers=1 when cache is enabled
    if workers > 1 and not no_cache:
        typer.echo(
            "  Warning: --workers > 1 requires --no-cache (DuckDB is single-writer). "
            "Disabling cache.",
            err=True,
        )
        no_cache = True

    # Pass config to app via env vars (picked up by get_app factory)
    os.environ["DOMAIN_SCOUT_MAX_CONCURRENT"] = str(max_concurrent)
    if no_cache:
        os.environ["DOMAIN_SCOUT_CACHE"] = "false"
    if cache_dir:
        os.environ["DOMAIN_SCOUT_CACHE_DIR"] = cache_dir

    uvicorn.run(
        "domain_scout.api:get_app",
        host=host,
        port=port,
        workers=workers,
        factory=True,
        log_level="debug" if verbose else "info",
    )


@cache_app.command("stats")
def cache_stats(
    cache_dir: Annotated[str | None, typer.Option("--cache-dir", help="Cache directory")] = None,
) -> None:
    """Show cache statistics."""
    try:
        from domain_scout.cache import DuckDBCache
    except ImportError:
        typer.echo(
            "Error: duckdb is not installed. Install with: pip install domain-scout-ct[cache]",
            err=True,
        )
        raise typer.Exit(1) from None

    with DuckDBCache(cache_dir=cache_dir) as cache:
        stats = cache.stats()

    typer.echo(f"  Cache directory: {stats['cache_dir']}")
    typer.echo(f"  CT entries:      {stats['ct_entries']}")
    typer.echo(f"  RDAP entries:    {stats['rdap_entries']}")
    if stats["ct_oldest_age_seconds"] is not None:
        typer.echo(f"  CT oldest:       {stats['ct_oldest_age_seconds']}s ago")
    if stats["rdap_oldest_age_seconds"] is not None:
        typer.echo(f"  RDAP oldest:     {stats['rdap_oldest_age_seconds']}s ago")


@cache_app.command("clear")
def cache_clear(
    cache_dir: Annotated[str | None, typer.Option("--cache-dir", help="Cache directory")] = None,
) -> None:
    """Clear all cached entries."""
    try:
        from domain_scout.cache import DuckDBCache
    except ImportError:
        typer.echo(
            "Error: duckdb is not installed. Install with: pip install domain-scout-ct[cache]",
            err=True,
        )
        raise typer.Exit(1) from None

    with DuckDBCache(cache_dir=cache_dir) as cache:
        cache.clear()
    typer.echo("  Cache cleared.")


def _print_table(result: ScoutResult) -> None:
    """Pretty-print results as a table to stderr/stdout."""
    typer.echo(f"\n  Entity: {result.entity.company_name}", err=True)
    if result.entity.location:
        typer.echo(f"  Location: {result.entity.location}", err=True)
    if result.entity.seed_domain:
        for sd in result.entity.seed_domain:
            assessment = result.seed_domain_assessment.get(sd, "unknown")
            typer.echo(f"  Seed domain: {sd} ({assessment})", err=True)
    if result.seed_cross_verification:
        seen_pairs: set[tuple[str, ...]] = set()
        for s, others in result.seed_cross_verification.items():
            for o in others:
                pair = tuple(sorted([s, o]))
                if pair not in seen_pairs:
                    seen_pairs.add(pair)
                    typer.echo(f"  Cross-verified: {pair[0]} <-> {pair[1]} (shared cert)", err=True)
    typer.echo(err=True)

    if not result.domains:
        typer.echo("  No domains found.", err=True)
        return

    # Header
    typer.echo(f"  {'Domain':<40} {'Conf':>5}  {'Resolves':>8}  Sources", err=True)
    typer.echo(f"  {'─' * 40} {'─' * 5}  {'─' * 8}  {'─' * 30}", err=True)

    for d in result.domains:
        flag = "seed " if d.is_seed else ""
        res = "yes" if d.resolves else "no"
        sources = ", ".join(d.sources)
        typer.echo(
            f"  {flag}{d.domain:<{40 - len(flag)}} {d.confidence:>5.2f}  {res:>8}  {sources}",
            err=True,
        )

    typer.echo(err=True)
    typer.echo(
        f"  Found {result.run_metadata.domains_found} domains"
        f" in {result.run_metadata.elapsed_seconds}s",
        err=True,
    )

    if result.run_metadata.errors:
        typer.echo(f"  Warnings: {len(result.run_metadata.errors)}", err=True)


if __name__ == "__main__":
    app()
