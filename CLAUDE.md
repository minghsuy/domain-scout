# CLAUDE.md

## Project

domain-scout — Discover internet domains associated with a business entity via CT logs, RDAP, DNS, and Shodan GeoDNS.

## Quick Commands

```bash
make install       # uv sync --all-groups
make test          # unit tests only (excludes integration)
make test-integration  # hits real external services (crt.sh, RDAP, DNS)
make lint          # ruff check + mypy --strict
make format        # ruff fix + ruff format
make check         # format + lint + test
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
├── models.py           # Pydantic models: EntityInput, DiscoveredDomain, EvidenceRecord, RunMetadata, ScoutResult, CertRecord
├── config.py           # ScoutConfig dataclass (all tunables + discovery profiles)
├── _logging.py         # structlog configuration (WARNING+stderr defaults)
├── sources/
│   ├── ct_logs.py      # crt.sh Postgres (primary) + JSON API (fallback)
│   ├── rdap.py         # RDAP via rdap.org (universal bootstrap)
│   └── dns_utils.py    # DNS resolution checker
├── matching/
│   └── entity_match.py # Org-name similarity scoring (rapidfuzz, acronyms, brand aliases)
└── tests/
    ├── test_ct.py
    ├── test_matching.py
    ├── test_multi_seed.py
    ├── test_evidence.py     # profiles, RunMetadata, EvidenceRecord
    └── test_integration.py  # marked "integration", deselected by default
```

## Architecture Notes

- **crt.sh Postgres is primary, JSON API is fallback** — dates must be normalized (JSON returns strings, Postgres returns datetime objects)
- **RDAP uses rdap.org** (universal bootstrap), NOT ARIN — needed for ccTLDs like .it
- .it ccTLD doesn't support RDAP at all (404 is expected, not a bug)
- psycopg2-binary runs via `run_in_executor` (sync driver in async code)
- Multi-seed: `--seed` is repeatable, runs parallel CT expansions per seed, cross-seed verification boosts confidence
- Evidence is structured (`EvidenceRecord` with `source_type`, `cert_id`, `similarity_score`) — not plain strings
- `RunMetadata` captures tool version, timestamp, config snapshot for audit reproducibility
- Discovery profiles: `--profile broad|balanced|strict` via `ScoutConfig.from_profile()`
- Org-name matching: acronym detection (CamelCase-aware), abbreviation expansion, DBA dual-match, brand aliases, conglomerate guard
- Input length capped at 500 chars to prevent O(n*m) DoS from adversarial cert org fields

## Conventions

- No insurance/underwriting language in public-facing files (README, commits, PR descriptions)
- SPEC.md is gitignored (contains internal context)
- Security reports (*-threat-model.md, *_report.md) are gitignored
- License: MIT
- mypy has ~13-16 pre-existing errors in untouched files — don't fix unrelated type errors

## Testing

- **166 unit tests** + 3 integration tests (deselected by default)
- Integration tests hit real crt.sh, RDAP, and DNS — use `make test-integration`
- Seed domain choice significantly affects live results — different seeds find different SANs
