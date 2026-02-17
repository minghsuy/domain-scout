# domain-scout

[![CI](https://github.com/minghsuy/domain-scout/actions/workflows/ci.yml/badge.svg)](https://github.com/minghsuy/domain-scout/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/domain-scout-ct)](https://pypi.org/project/domain-scout-ct/)

Discover internet domains associated with a business entity using Certificate Transparency logs, RDAP, and DNS.

Useful for security teams, asset inventories, and M&A due diligence — where seed domains can be wrong, misspelled, or belong to a parent company.

## Install

```bash
pip install domain-scout-ct            # core library + CLI
pip install domain-scout-ct[api]       # + REST API server
pip install domain-scout-ct[cache]     # + DuckDB query cache
pip install domain-scout-ct[all]       # everything
```

For development:

```bash
uv sync --all-groups --all-extras
```

## Usage

### CLI

```bash
# Basic usage
domain-scout --name "Guidewire Software" --location "San Mateo, CA"

# With seed domain
domain-scout --name "Palo Alto Networks" --location "Santa Clara, CA" --seed "paloaltonetworks.com"

# Multiple seeds — cross-verification boosts confidence for domains found by both
domain-scout --name "Walmart" --seed walmart.com --seed samsclub.com

# Deep mode — GeoDNS global resolution for non-resolving domains
domain-scout --name "Walmart" --seed "walmart.com" --deep

# JSON output
domain-scout --name "Acme Corp" --output json > results.json

# Verbose logging
domain-scout --name "Cloudflare" --seed "cloudflare.com" -v
```

### REST API

```bash
# Start the API server (cache enabled by default)
domain-scout serve --port 8080

# Health check
curl http://localhost:8080/health

# Run a scan
curl -X POST http://localhost:8080/scan \
  -H "Content-Type: application/json" \
  -d '{"entity": {"company_name": "Walmart", "seed_domain": ["walmart.com"]}}'

# Readiness check (probes crt.sh connectivity)
curl http://localhost:8080/ready
```

### Docker

```bash
# Build
docker build -t domain-scout-ct .

# Run API server
docker run -p 8080:8080 domain-scout-ct

# Run CLI scan
docker run domain-scout-ct scout --name "Walmart" --seed walmart.com

# Persist cache across runs
docker run -p 8080:8080 -v scout-cache:/data/cache domain-scout-ct
```

### Cache

```bash
# Enable cache for CLI scans
domain-scout scout --name "Walmart" --seed walmart.com --cache

# View cache statistics
domain-scout cache stats

# Clear cache
domain-scout cache clear
```

### Library

```python
from domain_scout import Scout

result = Scout().discover(
    company_name="Palo Alto Networks",
    location="Santa Clara, CA",
    seed_domain=["paloaltonetworks.com"],
)

for domain in result.domains:
    print(f"{domain.domain:40s}  {domain.confidence:.2f}  {domain.sources}")
```

### Async

```python
import asyncio
from domain_scout import Scout, EntityInput

async def main():
    scout = Scout()
    result = await scout.discover_async(EntityInput(
        company_name="Palo Alto Networks",
        seed_domain=["paloaltonetworks.com"],
    ))
    return result

result = asyncio.run(main())
```

## How it works

1. **Seed validation** — DNS-resolves the seed domain, checks RDAP registrant org and CT cert org names against the company name
2. **CT org search** — Queries crt.sh Postgres for certificates where the Subject Organization matches the company name
3. **Seed expansion** — Finds all SANs on certs covering the seed domain, revealing related domains (e.g., acquired companies)
4. **Domain guessing** — Generates candidates from the company name + common TLDs, resolves them, verifies via CT
5. **Cross-seed verification** — With multiple seeds, domains found independently by 2+ seeds get a confidence boost
6. **Confidence scoring** — Scores each domain 0–1 based on org match, SAN co-occurrence, DNS resolution, cross-seed verification, and shared infrastructure

### Data sources

| Source | Method | Rate limited |
|--------|--------|-------------|
| crt.sh | Postgres (primary), JSON API (fallback) | 5 concurrent queries, 1s burst delay |
| RDAP | rdap.org universal bootstrap | Per-request |
| DNS | dnspython (8.8.8.8, 1.1.1.1) | 5 concurrent |
| Shodan GeoDNS | geonet.shodan.io (deep mode) | 3 concurrent, 0.5s delay |

## Development

```bash
make install      # uv sync --all-groups
make test         # unit tests (mocked external calls)
make lint         # ruff + mypy
make format       # ruff --fix + ruff format
make check        # format + lint + test
```

Integration tests hit real crt.sh:

```bash
make test-integration
```

## License

MIT
