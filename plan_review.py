print("""
If I must NOT refactor the Typer command arguments into objects (due to Typer's explicit param requirement and the memory), the ONLY reasonable interpretation of the prompt's issue ("Function 'scout' has too many arguments and is long", "CLI handlers should generally delegate to a service class. The arguments can be grouped into a single configuration object") is to do this:

1. Define a dataclass or Pydantic model for the CLI arguments, say `ScoutCliArgs` (or `ScoutRunnerConfig`, etc.).
2. In the CLI handler (`scout` function), instantiate this `ScoutCliArgs` with all the explicitly received CLI parameters.
3. Pass this `ScoutCliArgs` to a new service class or function (e.g. `CliRunner` or a helper `run_scout` method) that handles all the complex logic, so the `scout` function becomes very short (just taking args, creating the object, and delegating).
4. `# noqa: PLR0913` is kept on the `scout` function definition, as the memory directs.

Let's look at what `scout` does currently:
```python
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
        typer.Option("--subsidiaries-path", help="Path to subsidiaries CSV"),
    ] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Verbose logging")] = False,
) -> None:
    \"\"\"Discover domains associated with a company.\"\"\"
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
        typer.echo("\\nAborted.", err=True)
        raise typer.Exit(1) from None
    finally:
        if cache:
            cache.close()

    if output == "json":
        typer.echo(result.model_dump_json(indent=2))
    else:
        _print_table(result)
```

I can create a `dataclass` called `ScoutCliArgs` (or similar) with all these fields.
Then a separate class like `CliRunner` (or `ScoutRunner` maybe `ScoutRunnerService`) that takes `ScoutCliArgs`. Or even better, a class called `ScoutService` that has an `execute(args)` method.
Wait! Is there an existing service class? The prompt says "CLI handlers should generally delegate to a service class".

Let's do this:

```python
from dataclasses import dataclass

@dataclass
class ScoutCliArgs:
    name: str
    location: str | None
    seed: list[str] | None
    industry: str | None
    deep: bool
    output: str
    timeout: int
    profile: str | None
    use_cache: bool
    cache_dir: str | None
    local: bool
    local_first: bool
    warehouse_path: str | None
    subsidiaries_path: str | None
    verbose: bool

class ScoutRunner:
    \"\"\"Service class to execute scout operations.\"\"\"

    def __init__(self, args: ScoutCliArgs) -> None:
        self.args = args

    def run(self) -> None:
        configure_logging(level=logging.DEBUG if self.args.verbose else logging.INFO)

        seeds = self.args.seed or []
        timeout = self.args.timeout
        if self.args.deep:
            timeout = max(timeout, 180)
        if len(seeds) >= 3:
            timeout = max(timeout, 150)

        # Resolve local mode and warehouse path
        overrides: dict[str, object] = {"total_timeout": timeout, "deep_mode": self.args.deep}
        if self.args.local and self.args.local_first:
            typer.echo("Error: --local and --local-first are mutually exclusive.", err=True)
            raise typer.Exit(1)
        if self.args.local or self.args.local_first:
            import os

            overrides["local_mode"] = "local_only" if self.args.local else "local_first"
            overrides["warehouse_path"] = self.args.warehouse_path or os.environ.get(
                "DOMAIN_SCOUT_WAREHOUSE_PATH",
                str(Path.home() / ".local" / "share" / "ct-warehouse"),
            )
        if self.args.subsidiaries_path:
            overrides["subsidiaries_path"] = self.args.subsidiaries_path

        config = (
            ScoutConfig.from_profile(self.args.profile, **overrides)  # type: ignore[arg-type]
            if self.args.profile
            else ScoutConfig(**overrides)  # type: ignore[arg-type]
        )

        cache = None
        if self.args.use_cache:
            cache = _get_cache_or_exit(self.args.cache_dir)

        try:
            s = Scout(config=config, cache=cache)
            result = s.discover(
                company_name=self.args.name,
                location=self.args.location,
                seed_domain=seeds,
                industry=self.args.industry,
            )
        except KeyboardInterrupt:
            typer.echo("\\nAborted.", err=True)
            raise typer.Exit(1) from None
        finally:
            if cache:
                cache.close()

        if self.args.output == "json":
            typer.echo(result.model_dump_json(indent=2))
        else:
            _print_table(result)
```

Then `scout` becomes:
```python
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
        typer.Option("--subsidiaries-path", help="Path to subsidiaries CSV"),
    ] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Verbose logging")] = False,
) -> None:
    \"\"\"Discover domains associated with a company.\"\"\"
    args = ScoutCliArgs(
        name=name,
        location=location,
        seed=seed,
        industry=industry,
        deep=deep,
        output=output,
        timeout=timeout,
        profile=profile,
        use_cache=use_cache,
        cache_dir=cache_dir,
        local=local,
        local_first=local_first,
        warehouse_path=warehouse_path,
        subsidiaries_path=subsidiaries_path,
        verbose=verbose,
    )
    ScoutRunner(args).run()
```

Is this what the user wants?
"CLI handlers should generally delegate to a service class. The arguments can be grouped into a single configuration object."
Yes! This directly addresses the rationale. The `scout` function delegates to a service class (`ScoutRunner`), and the arguments are grouped into a single configuration object (`ScoutCliArgs`).
And the `scout` function preserves its explicit arguments and `# noqa: PLR0913`, so Typer works correctly and we obey the memory constraints!
""")
