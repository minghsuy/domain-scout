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
    s = Scout(config=config)

    try:
        result = s.discover(
            company_name=name,
            location=location,
            seed_domain=seeds,
            industry=industry,
        )
    except KeyboardInterrupt:
        typer.echo("\nAborted.", err=True)
        raise typer.Exit(1) from None

    if output == "json":
        typer.echo(result.model_dump_json(indent=2))
    else:
        _print_table(result)


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
        seen_pairs: set[tuple[str, str]] = set()
        for s, others in result.seed_cross_verification.items():
            for o in others:
                pair = tuple(sorted([s, o]))
                if pair not in seen_pairs:
                    seen_pairs.add(pair)  # type: ignore[arg-type]
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
