# Getting Started

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

## Installation

```bash
git clone https://github.com/minghsuy/domain-scout.git
cd domain-scout
uv sync
```

Or with pip:

```bash
pip install -e .
```

## First run

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
| `--deep` | `-d` | Enable GeoDNS global resolution | `false` |
| `--output` | `-o` | Output format: `table` or `json` | `table` |
| `--timeout` | | Total timeout in seconds | `120` |
| `--verbose` | `-v` | Verbose logging | `false` |

## JSON output

```bash
domain-scout --name "Acme Corp" --seed "acme.com" --output json > results.json
```

The JSON output includes full metadata: confidence scores, evidence trails, certificate org names, first/last seen dates, and DNS resolution status for each domain.

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
