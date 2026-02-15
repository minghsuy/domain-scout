# How It Works

domain-scout runs a multi-phase async pipeline that cross-references several public data sources to build a high-confidence map of an organization's domains.

## Pipeline overview

```
Input: company name + optional seed domain
          │
          ├─ Phase 1 (parallel)
          │   ├─ Seed validation (DNS + RDAP + CT)
          │   ├─ Strategy A: CT org name search
          │   └─ Strategy C: Domain guessing
          │
          ├─ Phase 2 (after seed validation)
          │   ├─ Strategy A': CT search on seed org name
          │   └─ Strategy B: Seed domain expansion (SANs)
          │
          ├─ Phase 3: DNS resolution
          │   └─ Step 3b: GeoDNS rescue (--deep mode)
          │
          ├─ Phase 4: Confidence scoring
          │   └─ Infrastructure sharing boost
          │
          └─ Output: scored, filtered domain list
```

## Data sources

### Certificate Transparency logs (crt.sh)

The primary discovery engine. CT logs record every TLS certificate ever issued, including the organization name (O= field) and all Subject Alternative Names (SANs) on each cert.

**Two connection methods with automatic fallback:**

1. **Postgres direct** (primary) — connects to crt.sh's public Postgres database for full-text search. Faster and more reliable.
2. **JSON API** (fallback) — queries `crt.sh/?q=...&output=json` if Postgres is down or slow.

Both are rate-limited: 5 concurrent queries with 1s burst delay between retries.

### RDAP

Registration Data Access Protocol — the modern replacement for WHOIS. Used to look up domain registrant organization names. Queries go through `rdap.org`, a universal bootstrap service that routes to the correct regional registry for any TLD (ARIN, RIPE, APNIC, etc.).

### DNS

Standard A/AAAA resolution via Google (8.8.8.8) and Cloudflare (1.1.1.1) public resolvers. Used for:

- Validating that discovered domains actually resolve
- Infrastructure comparison (shared nameservers or IP /24 prefixes)

### Shodan GeoDNS (deep mode)

Shodan's free GeoDNS API (`geonet.shodan.io`) resolves domains from ~5 global locations (US, UK, Germany, Netherlands, Singapore). Catches regional domains that don't resolve from US-based resolvers.

## Discovery strategies

### Strategy A: Organization name search

Searches CT logs for certificates where the Subject Organization (O=) field matches the target company name. Uses fuzzy matching (rapidfuzz) with a configurable threshold (default: 0.65).

### Strategy B: Seed domain expansion

If a seed domain is provided, finds all certificates that cover the seed domain and extracts other domains from their SANs. This reveals related domains — like when Walmart's cert also covers `samsclub.com`, `bodegaaurrera.com.mx`, and `asda.com`.

**CDN filter:** Certificates with 10+ unrelated base domains and low org match score are flagged as CDN/multi-tenant certs and their non-seed SANs are excluded.

### Strategy C: Domain guessing

Generates domain candidates from the company name (e.g., "Palo Alto Networks" -> `paloaltonetworks.com`, `paloalto.com`, `pan.com`) across common TLDs, then DNS-resolves them.

## Confidence scoring

Each discovered domain receives a confidence score from 0.0 to 1.0:

| Signal | Score |
|--------|-------|
| CT org match (O= field matches company) | 0.85 |
| SAN co-occurrence (on same cert as seed) | 0.80 |
| Seed subdomain | 0.75 |
| RDAP registrant match | 0.70 |
| CT seed-related (found in seed search) | 0.40 |
| DNS guess only | 0.30 |

**Boosts:**

| Condition | Boost |
|-----------|-------|
| 3+ independent sources | +0.10 |
| 2 independent sources | +0.05 |
| DNS resolves | +0.05 |
| Org name similarity > 0.9 | +0.05 |
| Shares infrastructure with seed | +0.05 |

Domains below the inclusion threshold (default: 0.60) are filtered out. Non-resolving domains are also filtered unless they are the seed domain itself.

## Timeout budget

The entire pipeline runs under a configurable total timeout (default: 120s, bumped to 180s in deep mode). Each phase has a sub-budget:

- Seed validation: 15s
- All strategies: remaining - 10s reserve
- DNS resolution: remaining - 2s reserve
- GeoDNS: remaining - 3s reserve
- Infrastructure checks: 10s hard cap

If any phase times out, completed results are preserved and the pipeline continues with what it has.
