# CLAUDE.md

## Project

domain-scout — Discover internet domains associated with a business entity via CT logs, RDAP, DNS, and Shodan GeoDNS.

## Quick Commands

```bash
make install       # uv sync --all-groups --all-extras
make test          # unit tests only (excludes integration)
make test-integration  # hits real external services (crt.sh, RDAP, DNS)
make lint          # ruff check + mypy --strict
make format        # ruff fix + ruff format
make check         # format + lint + test
make eval          # run evaluation harness against the baseline substrate
make eval-baselines  # (re)generate the git-ignored baseline substrate + manifest
```

## Tech Stack

- Python 3.12, async (asyncio)
- **Pydantic** for models, **Typer** for CLI, **structlog** for logging
- **httpx** (async HTTP), **dnspython**, **psycopg2-binary** (crt.sh Postgres), **rapidfuzz** (string matching)
- **uv** for dependency management (not pip)
- **ruff** for linting/formatting, **mypy --strict** for type checking
- **pytest** + pytest-asyncio + pytest-timeout

## Project Layout

```
domain_scout/
├── cli.py              # Typer CLI (entry point: domain_scout.cli:app)
├── scout.py            # Main orchestrator (Scout class)
├── models.py           # Pydantic models (EntityInput, DiscoveredDomain, ScoutResult, delta models, etc.)
├── delta.py            # Delta reporting: compute_delta between two ScoutResult runs
├── config.py           # ScoutConfig dataclass (all tunables + discovery profiles)
├── api.py              # FastAPI REST API (/scan, /diff, /health, /ready, /cache/*)
├── cache.py            # DuckDB TTL cache for CT/RDAP queries
├── eval.py             # Evaluation harness: precision/recall against labeled ground truth
├── eval_ground_truth.yaml  # Ground truth: owned_domains and not_owned lists per entity
├── _logging.py         # structlog configuration (WARNING+stderr defaults)
├── _metrics.py         # Prometheus metrics (optional, no-ops without prometheus-client)
├── sources/
│   ├── ct_logs.py      # crt.sh Postgres (primary) + JSON API (fallback) + circuit breaker
│   ├── rdap.py         # RDAP via rdap.org (universal bootstrap)
│   ├── dns_utils.py    # DNS resolution checker (A/AAAA/NS/MX/TXT)
│   ├── dns_fingerprint.py # DNS fingerprint extraction + matching for DV-cert companies
│   ├── local_parquet.py # LocalParquetSource + HybridCTSource for CT warehouse
│   └── ctscout_remote.py # CTScout remote API source (ctscout.dev warehouse)
├── matching/
│   └── entity_match.py # Org-name similarity scoring (rapidfuzz, acronyms, brand aliases)
└── tests/
    ├── conftest.py          # Shared test fixtures (mock_result factory)
    ├── test_acceptance.py   # Walmart fixture tests with source-level mocks
    ├── test_api.py          # REST API endpoint tests
    ├── test_cache.py        # DuckDB cache tests
    ├── test_cli.py          # CLI command tests (scout, serve, diff, cache)
    ├── test_config.py       # ScoutConfig validation and profiles
    ├── test_ct.py
    ├── test_delta.py        # delta reporting, CLI diff, API /diff
    ├── test_dns_utils.py    # DNS checker unit tests
    ├── test_fingerprint.py  # DNS fingerprint extraction, matching, MX tenant parsing
    ├── test_eval.py         # Evaluation harness unit tests
    ├── test_evidence.py     # profiles, RunMetadata, EvidenceRecord
    ├── test_integration.py  # marked "integration", deselected by default
    ├── test_invariants.py   # CI gate invariants for automated PRs
    ├── test_local_parquet.py # LocalParquetSource + HybridCTSource tests
    ├── test_matching.py
    ├── test_metrics.py      # Prometheus metrics tests
    ├── test_models.py       # Pydantic model validation tests
    ├── test_multi_seed.py
    ├── test_rdap.py         # RDAP lookup parsing tests
    ├── test_scout_internals.py # _extract_sans helper tests
    ├── test_scorer.py       # Learned scorer tests
    ├── test_ctscout_remote.py # CTScout remote API source tests
    └── test_subsidiary.py   # Subsidiary-aware CT search
```

## Architecture Notes

- **crt.sh Postgres is primary, JSON API is fallback** — dates must be normalized (JSON returns strings, Postgres returns datetime objects)
- **The crt.sh JSON API does not expose the certificate subject organization** — org searches with `verify_org=True` skip the JSON fallback and raise `CTOrgSearchUnavailableError` when Postgres is down (surfaced in `RunMetadata.errors`; metric `ct_queries_total{backend="json", status="skipped_org"}`); `verify_org=False` callers still fall back (#163)
- **RDAP uses rdap.org** (universal bootstrap), NOT ARIN — skips 35 ccTLDs not in IANA bootstrap (see `docs/rdap-cctld-support.md`)
- `RDAP_SKIP_TLDS` frozenset in `rdap.py` prevents wasted lookups and noisy 404 logs; review quarterly against IANA bootstrap at `https://data.iana.org/rdap/dns.json`
- psycopg2-binary runs via `run_in_executor` (sync driver in async code)
- Multi-seed: `--seed` is repeatable, runs parallel CT expansions per seed, cross-seed verification boosts confidence
- Evidence is structured (`EvidenceRecord` with `source_type`, `cert_id`, `similarity_score`) — not plain strings
- `RunMetadata` captures tool version, timestamp, config snapshot for audit reproducibility
- Discovery profiles: `--profile broad|balanced|strict` via `ScoutConfig.from_profile()`
- Org-name matching: acronym detection (CamelCase-aware), abbreviation expansion, DBA dual-match, brand aliases, conglomerate guard, positional suffix anchoring
- Positional suffix anchoring: ambiguous suffixes (Group, Holdings, Co, AG, SA, SE, NV, AB) only stripped from end of name; dotted forms (S.A., N.V., Co.) stripped at any position
- Input length capped at 500 chars to prevent O(n*m) DoS from adversarial cert org fields
- Corroboration-level scoring: Level 3 (+0.10) resolves+RDAP+multi-source, Level 2 (+0.05) resolves+(RDAP or high sim or multi-source), Level 1 (±0.00) resolves only, Level 0 (−0.05) no resolution
- RDAP corroboration phase runs on top N resolving candidates, adds `rdap_registrant_match` source
- Circuit breaker for crt.sh Postgres: shared `_CircuitBreaker` class variable on `CTLogSource`, skips Postgres after `cb_failure_threshold` consecutive failures, probes after `cb_recovery_timeout` seconds

- **Fingerprint mode** (`--mode fingerprint`): for DV-cert companies where CT org search is blind. Skips Strategy A, adds DNS fingerprint verification as post-processing step (between RDAP corroboration and scoring). Extracts MX tenant IDs (Proofpoint, M365, Barracuda, IronPort, FireEye), NS zones, IP /24 prefixes, and SPF includes from seeds and candidates. MX tenant match treated as equivalent to `rdap_registrant_match` in corroboration scoring. Implies `--deep` mode.
- Shodan reverse DNS candidate generation is deferred (see #110) — fingerprint mode works entirely with standard DNS queries (free, unlimited)

## Eval substrate (`baselines/`)

- `make eval` scores recorded `BaselineRecord` snapshots (`baselines/{label_id}.json`: the `ScoutResult` **plus** per-domain score-time `ScoringInputs`, substrate schema 2, #187) against `eval_ground_truth.yaml`. The whole `baselines/` dir — snapshots **and** `manifest.json` — is git-ignored: it's a locally-generated, point-in-time artifact, never committed (which is why a fresh checkout has none; issue #188).
- **Regenerate with `make eval-baselines`** (`= python -m domain_scout.eval --mode record`). It runs live discovery (default `ScoutConfig`, `use_learned_scorer=False`), writes one JSON per entity, and writes `baselines/manifest.json` referencing exactly the files that run produced. `make eval-baselines LIMIT=3` records a smoke-scale subset. Re-running is safe; it overwrites the manifest to reference only the current run's outputs.
- **The manifest is the substrate's source of truth.** Each entry carries `sha256` + `domains`; the manifest header stamps `generated_at`, `tool_version`, `scorer` (learned-scorer identity at record time), and `git_commit` — so decay is *detectable*, not silent (the gate-3 defect behind #188). It is **not** byte-identical across runs (CT is point-in-time); the *process* is what's reproducible.
- **The manifest is mandatory.** `make eval` fails loudly (`EvalSubstrateError`, non-zero exit) when there is no manifest, when the manifest is unparseable, or when it references an absent or sha-mismatched file. Loose `{label_id}.json` files without a manifest are **not** trusted (an interrupted `record` run can leave a partial set) — the manifest is proof of a completed run, so `record` writes it atomically and last. So `make eval` requires a prior `make eval-baselines`; a missing/partial substrate must never read as a passing/neutral eval. It also warns (stderr) when the substrate's recorded scorer differs from the current one.
- **Substrate schema is versioned** (`manifest.substrate_schema`, currently 2). Since #187, `record` captures each domain's score-time inputs (pre-`_infra_boost` sources, pre-dedup evidence aggregates, boost outcome) so the eval's learned leg replays production scoring exactly instead of approximating it from post-pipeline state. A substrate recorded under an older schema is refused loudly with a re-record message — never silently scored the approximated way.
- Full-sweep cost: 399 ground-truth entities × one live discovery each, serialized, bounded by `total_timeout=90s` and the crt.sh rate limits/circuit breaker. Budget a long single-threaded run; use `LIMIT` for quick refreshes.

## Conventions

- No domain-specific use-case language in public-facing files (README, commits, PR descriptions)
- SPEC.md is gitignored (contains internal context)
- Security reports (*-threat-model.md, *_report.md) are gitignored
- License: MIT
- mypy must pass clean (0 errors) — CI enforces this

## Testing

- **565 unit tests** + 4 integration tests (deselected by default)
- Integration tests hit real crt.sh, RDAP, and DNS — use `make test-integration`
- Seed domain choice significantly affects live results — different seeds find different SANs
