# Getting Started

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

## Installation

```bash
pip install domain-scout-ct
```

Or from source:

```bash
git clone https://github.com/minghsuy/domain-scout.git
cd domain-scout
uv sync
```

## Quickstart with CTScout API

The fastest way to get started — no local data pipeline needed:

1. Get a free API key at [ctscout.dev](https://ctscout.dev) (no email required)
2. Run:

```bash
export CTSCOUT_API_KEY=ds_free_...
domain-scout --name "Goldman Sachs"
```

Or pass the key directly:

```bash
domain-scout --name "Goldman Sachs" --api-key ds_free_...
```

The free tier gives you 10 queries/day against 147K+ org-domain pairs. No local setup, no database, no crt.sh dependency.

## First run (without API key)

Without an API key, domain-scout queries crt.sh directly:

```bash
domain-scout --name "Cloudflare" --seed "cloudflare.com"
```

Output:

```
  Entity: Cloudflare
  Seed domain: cloudflare.com (confirmed)

  Domain                                    Conf  Resolves  Sources
  ──────────────────────────────────────── ─────  ────────  ──────────────────────────────
  seed cloudflare.com                       1.00       yes  ct_org_match, ct_seed_subdomain, dns_guess
  cloudflare.net                            0.95       yes  ct_org_match, ct_san_expansion
  cloudflaressl.com                         0.95       yes  ct_org_match, ct_san_expansion
  ...
```

## Multiple seed domains

When an entity owns multiple domains that don't share certificates, a single seed only discovers part of the picture. Use `--seed` multiple times for cross-verification:

```bash
domain-scout --name "Walmart" --seed walmart.com --seed samsclub.com
```

Domains independently discovered from 2+ seeds receive a `cross_seed_verified` confidence boost (0.90 base score), providing a strong convergence signal.

## CLI options

| Flag | Short | Description | Default |
|------|-------|-------------|---------|
| `--name` | `-n` | Company name (required) | — |
| `--seed` | `-s` | Seed domain (repeatable) | — |
| `--location` | `-l` | City, state, country | — |
| `--industry` | `-i` | Industry hint | — |
| `--mode` | `-m` | Discovery mode: `default` or `fingerprint` | `default` |
| `--deep` | `-d` | Enable GeoDNS global resolution | `false` |
| `--profile` | `-p` | Discovery profile: `broad`, `balanced`, `strict` | `balanced` |
| `--output` | `-o` | Output format: `table` or `json` | `table` |
| `--timeout` | | Total timeout in seconds | `120` |
| `--api-key` | | CTScout API key (or `CTSCOUT_API_KEY` env var) | — |
| `--cache/--no-cache` | | Enable/disable DuckDB query cache | `false` |
| `--cache-dir` | | Cache directory path | — |
| `--local` | | Use local parquet warehouse only | `false` |
| `--local-first` | | Try local warehouse, fall back to crt.sh | `false` |
| `--warehouse-path` | | Path to parquet warehouse directory | — |
| `--subsidiaries-path` | | Path to subsidiaries CSV | — |
| `--verbose` | `-v` | Verbose logging | `false` |

## JSON output

```bash
domain-scout --name "Acme Corp" --seed "acme.com" --output json > results.json
```

The JSON output is a self-contained audit artifact. Each domain includes structured `evidence` records (source type, cert ID, org name, similarity score) and the top-level `run_metadata` captures tool version, timestamp, and the full config snapshot used. See [API Reference](api.md) for the complete schema.

## Development

```bash
make install      # uv sync --all-groups
make test         # unit tests
make lint         # ruff + mypy
make format       # auto-format
make check        # format + lint + test
```

Integration tests hit live crt.sh:

```bash
make test-integration
```
