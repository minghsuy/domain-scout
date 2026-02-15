# Deep Mode

Deep mode adds a second-pass DNS check using Shodan's free GeoDNS API for domains that fail standard resolution.

## Why

Some domains only resolve from specific regions. For example:

- `walmart.cn` may not resolve from US DNS resolvers
- Regional subsidiaries use ccTLDs served by local nameservers
- Some domains are geo-fenced or use split-horizon DNS

Standard DNS resolution (via Google 8.8.8.8 and Cloudflare 1.1.1.1) only tests from US locations. Deep mode adds global coverage.

## How it works

After Phase 3 (DNS resolution), deep mode:

1. Collects all domains that **failed** local DNS resolution
2. Queries Shodan's GeoDNS API for each one: `GET https://geonet.shodan.io/api/geodns/{domain}`
3. The API resolves from ~5 global locations (US, UK, Germany, Netherlands, Singapore)
4. If **any** location returns DNS answers, the domain is marked as resolving
5. Rescued domains get the `geodns` source tag and the existing `+0.05` resolve boost applies

## Usage

```bash
domain-scout --name "Walmart" --seed "walmart.com" --deep
```

The `--deep` flag automatically bumps the timeout to at least 180s (from the default 120s) to account for the additional HTTP round-trips.

## Rate limiting

The GeoDNS API is free and unauthenticated. To be respectful:

- **3 concurrent requests** (configurable via `geodns_concurrency`)
- **0.5s delay** between requests per slot (configurable via `geodns_delay`)
- HTTP 500 responses are treated as NXDOMAIN (Shodan's convention)

## Configuration

All deep mode settings are in `ScoutConfig`:

```python
from domain_scout.config import ScoutConfig

config = ScoutConfig(
    deep_mode=True,
    geodns_concurrency=3,       # max concurrent GeoDNS requests
    geodns_delay=0.5,           # seconds between requests per slot
    geodns_base_url="https://geonet.shodan.io/api/geodns",
)
```

## When to use

Deep mode is most valuable for:

- **Multinational organizations** with regional domains (e.g., `.cn`, `.mx`, `.co.za`, `.co.uk`)
- **Organizations in non-US markets** where primary domains may not resolve from US DNS
- **Comprehensive asset inventories** where you need full coverage

For purely US-focused companies, deep mode typically finds no additional domains but doesn't hurt (just adds a few seconds).
