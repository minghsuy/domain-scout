"""CLI interface for domain-scout."""

from __future__ import annotations

import logging
from pathlib import Path  # noqa: TC003 — Typer needs runtime import
from typing import TYPE_CHECKING, Annotated

import typer

from domain_scout._logging import configure_logging
from domain_scout.config import ScoutConfig
from domain_scout.scout import Scout

if TYPE_CHECKING:
    from domain_scout.cache import DuckDBCache
    from domain_scout.models import DeltaReport, ScoutResult

app = typer.Typer(
    name="domain-scout",
    help="Discover internet domains associated with a business entity.",
    no_args_is_help=True,
)

cache_app = typer.Typer(help="Manage the query cache.")
app.add_typer(cache_app, name="cache")


@app.command()
def scout(  # noqa: PLR0913
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
    local: Annotated[
        bool, typer.Option("--local", help="Use local parquet warehouse only")
    ] = False,
    local_first: Annotated[
        bool, typer.Option("--local-first", help="Try local warehouse, fall back to crt.sh")
    ] = False,
    warehouse_path: Annotated[
        str | None, typer.Option("--warehouse-path", help="Path to parquet warehouse directory")
    ] = None,
    subsidiaries_path: Annotated[
        str | None,
        typer.Option("--subsidiaries-path", help="Path to EDGAR subsidiaries CSV"),
    ] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Verbose logging")] = False,
) -> None:
    """Discover domains associated with a company."""
    configure_logging(level=logging.DEBUG if verbose else logging.INFO)

    seeds = seed or []
    if deep:
        timeout = max(timeout, 180)
    if len(seeds) >= 3:
        timeout = max(timeout, 150)

    # Resolve local mode and warehouse path
    overrides: dict[str, object] = {"total_timeout": timeout, "deep_mode": deep}
    if local and local_first:
        typer.echo("Error: --local and --local-first are mutually exclusive.", err=True)
        raise typer.Exit(1)
    if local or local_first:
        import os

        overrides["local_mode"] = "local_only" if local else "local_first"
        overrides["warehouse_path"] = warehouse_path or os.environ.get(
            "DOMAIN_SCOUT_WAREHOUSE_PATH",
            str(Path.home() / ".local" / "share" / "ct-warehouse"),
        )
    if subsidiaries_path:
        overrides["subsidiaries_path"] = subsidiaries_path

    config = (
        ScoutConfig.from_profile(profile, **overrides)  # type: ignore[arg-type]
        if profile
        else ScoutConfig(**overrides)  # type: ignore[arg-type]
    )

    cache = None
    if use_cache:
        cache = _get_cache_or_exit(cache_dir)

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
def serve(  # noqa: PLR0913
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


@app.command()
def diff(
    baseline: Annotated[Path, typer.Argument(help="Baseline ScoutResult JSON file")],
    current: Annotated[Path, typer.Argument(help="Current ScoutResult JSON file")],
    output: Annotated[
        str, typer.Option("--output", "-o", help="Output format: table or json")
    ] = "table",
) -> None:
    """Show what changed between two scan results."""
    from domain_scout.delta import compute_delta

    baseline_result = _load_result(baseline, "Baseline")
    current_result = _load_result(current, "Current")
    report = compute_delta(baseline_result, current_result)

    if output == "json":
        typer.echo(report.model_dump_json(indent=2))
    else:
        _print_delta_table(report)


def _get_cache_or_exit(cache_dir: str | Path | None = None) -> DuckDBCache:
    """Import and instantiate DuckDBCache, or exit with a friendly error."""
    try:
        from domain_scout.cache import DuckDBCache

        return DuckDBCache(cache_dir=cache_dir)
    except ImportError:
        typer.echo(
            "Error: duckdb is not installed. Install with: pip install domain-scout-ct[cache]",
            err=True,
        )
        raise typer.Exit(1) from None


def _load_result(path: Path, label: str) -> ScoutResult:
    """Load and validate a ScoutResult JSON file, or exit with an error."""
    from domain_scout.models import ScoutResult

    if not path.is_file():
        typer.echo(f"Error: {label} file not found: {path}", err=True)
        raise typer.Exit(1)
    try:
        data = path.read_bytes()
    except OSError as exc:
        typer.echo(f"Error: could not read {label.lower()} file: {exc}", err=True)
        raise typer.Exit(1) from None
    try:
        return ScoutResult.model_validate_json(data)
    except Exception as exc:
        typer.echo(f"Error: invalid {label.lower()} JSON: {exc}", err=True)
        raise typer.Exit(1) from None


def _print_delta_table(report: DeltaReport) -> None:
    """Pretty-print a delta report as a table."""
    # Warnings to stderr
    for w in report.warnings:
        typer.echo(f"  Warning [{w.code}]: {w.message}", err=True)
    if report.warnings:
        typer.echo(err=True)

    # Summary
    s = report.summary
    typer.echo(
        f"  Summary: {s.added} added, {s.removed} removed, "
        f"{s.changed} changed, {s.unchanged} unchanged",
        err=True,
    )
    typer.echo(
        f"  Baseline: {s.baseline_total} domains | Current: {s.current_total} domains",
        err=True,
    )
    typer.echo(err=True)

    if report.added:
        typer.echo("  + Added:", err=True)
        for d in report.added:
            typer.echo(f"    + {d.domain:<40} {d.confidence:>5.2f}", err=True)
        typer.echo(err=True)

    if report.removed:
        typer.echo("  - Removed:", err=True)
        for d in report.removed:
            typer.echo(f"    - {d.domain:<40} {d.confidence:>5.2f}", err=True)
        typer.echo(err=True)

    if report.changed:
        typer.echo("  ~ Changed:", err=True)
        for c in report.changed:
            b, cur = c.baseline_confidence, c.current_confidence
            typer.echo(f"    ~ {c.domain:<40} {b:>5.2f} -> {cur:>5.2f}", err=True)
            for ch in c.changes:
                typer.echo(f"      {ch.field}: {ch.old} -> {ch.new}", err=True)
        typer.echo(err=True)

    if not report.added and not report.removed and not report.changed:
        typer.echo("  No changes detected.", err=True)


@cache_app.command("stats")
def cache_stats(
    cache_dir: Annotated[str | None, typer.Option("--cache-dir", help="Cache directory")] = None,
) -> None:
    """Show cache statistics."""
    try:
        with _get_cache_or_exit(cache_dir) as cache:
            stats = cache.stats()
    except Exception as exc:
        if "lock" in str(exc).lower():
            typer.echo(
                "Error: cache database is locked by another process (API server running?).\n"
                "Use the /cache/stats API endpoint instead, or stop the server first.",
                err=True,
            )
            raise typer.Exit(1) from None
        raise

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
        with _get_cache_or_exit(cache_dir) as cache:
            cache.clear()
    except Exception as exc:
        if "lock" in str(exc).lower():
            typer.echo(
                "Error: cache database is locked by another process (API server running?).\n"
                "Use the /cache/clear API endpoint instead, or stop the server first.",
                err=True,
            )
            raise typer.Exit(1) from None
        raise
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
