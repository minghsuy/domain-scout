# How It Works

domain-scout runs a multi-phase async pipeline that cross-references several public data sources to build a high-confidence map of an organization's domains.

## Pipeline overview

```
Input: company name + optional seed domain(s)
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
          ├─ Phase 3: Post-processing
          │   ├─ DNS resolution
          │   ├─ Step 3b: GeoDNS rescue (--deep mode)
          │   ├─ Step 3c: RDAP corroboration
          │   └─ Step 3d: Fingerprint corroboration (--mode fingerprint)
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

Standard A/AAAA/NS/MX/TXT resolution via Google (8.8.8.8) and Cloudflare (1.1.1.1) public resolvers. Used for:

- Validating that discovered domains actually resolve
- Infrastructure comparison (shared nameservers or IP /24 prefixes)
- Fingerprint extraction (MX tenant IDs, NS zones, SPF includes) in fingerprint mode

### Shodan GeoDNS (deep mode)

Shodan's free GeoDNS API (`geonet.shodan.io`) resolves domains from ~5 global locations (US, UK, Germany, Netherlands, Singapore). Catches regional domains that don't resolve from US-based resolvers.

## Discovery strategies

### Strategy A: Organization name search

Searches CT logs for certificates where the Subject Organization (O=) field matches the target company name. Uses fuzzy matching (rapidfuzz) with a configurable threshold (default: 0.65).

**Org-name normalization** handles real-world variations:

- **Legal suffixes** stripped before comparison: Inc, LLC, Ltd, GmbH, S.p.A., K.K., Bhd, and 20+ others. Ambiguous suffixes (Group, Holdings, Co, AG, SA, SE, NV, AB) are only stripped from the end of a name to preserve names like "Group Nine Media", "SA Power Networks", and "AB InBev"
- **Abbreviation expansion**: Intl→International, Tech→Technology, Mgmt→Management, etc.
- **DBA/subsidiary clauses** stripped: "ACME LLC DBA ACME CLOUD" matches both "Acme" and "Acme Cloud"
- **Acronym detection**: IBM matches "International Business Machines", HP matches "Hewlett Packard"
- **Brand aliases**: hardcoded mappings for names that differ completely (Foxconn↔Hon Hai, Petrobras↔Petroleo Brasileiro)
- **Conglomerate guard**: penalizes matches between sibling entities sharing a brand prefix (Samsung Electronics ≠ Samsung SDI)

### Strategy B: Seed domain expansion

If a seed domain is provided, finds all certificates that cover the seed domain and extracts other domains from their SANs. This reveals related domains — like when Walmart's cert also covers `samsclub.com`, `bodegaaurrera.com.mx`, and `asda.com`.

**CDN filter:** Certificates with 10+ unrelated base domains and low org match score are flagged as CDN/multi-tenant certs and their non-seed SANs are excluded.

### Strategy C: Domain guessing

Generates domain candidates from the company name (e.g., "Palo Alto Networks" -> `paloaltonetworks.com`, `paloalto.com`, `pan.com`) across common TLDs, then DNS-resolves them.

### Fingerprint verification (`--mode fingerprint`)

When `--mode fingerprint` is used, Strategy A (CT org search) is skipped — DV certificates have no org name, so this strategy returns nothing. Instead, a DNS fingerprint corroboration phase runs after DNS resolution:

1. Extracts MX tenant IDs, NS zones, and SPF includes from each seed domain
2. For each candidate found by other strategies (B, C, D), extracts its fingerprint
3. Compares against the seed fingerprint — matching MX tenants, private NS zones, or custom SPF includes are added as evidence

See [Fingerprint Mode](fingerprint-mode.md) for details on supported providers and signal filtering.

## Confidence scoring

Each discovered domain receives a confidence score from 0.0 to 1.0:

| Signal | Score |
|--------|-------|
| Cross-seed verified (found from 2+ seeds) | 0.90 |
| CT org match (O= field matches company) | 0.85 |
| SAN co-occurrence (on same cert as seed) | 0.80 |
| Seed subdomain | 0.75 |
| RDAP registrant match | 0.70 |
| CT seed-related (found in seed search) | 0.40 |
| DNS guess only | 0.30 |

**Corroboration signals** (boost existing scores, don't set base scores):

| Condition | Effect |
|-----------|--------|
| RDAP registrant match or MX tenant match (`fp:mx_tenant`) | Treated as strong org-level signal |
| 3+ independent sources + strong signal | +0.10 (Level 3) |
| 2 independent sources or strong signal | +0.05 (Level 2) |
| DNS resolves only | +0.00 (Level 1) |
| No DNS resolution | -0.05 (Level 0) |
| Shares infrastructure with seed | +0.05 |

Domains below the inclusion threshold (default: 0.60) are filtered out. Non-resolving domains are also filtered unless they are the seed domain itself (or `include_non_resolving` is set, as in the `broad` profile).

**Discovery profiles** adjust these thresholds as presets:

| Profile | `org_match_threshold` | `inclusion_threshold` | `seed_confirm_threshold` | `include_non_resolving` |
|---------|----------------------|----------------------|--------------------------|------------------------|
| `broad` | 0.50 | 0.40 | 0.45 | yes |
| `balanced` (default) | 0.65 | 0.60 | 0.60 | no |
| `strict` | 0.80 | 0.75 | 0.75 | no |

Use `--profile broad` for maximum recall or `--profile strict` for high-precision results.

## Timeout budget

The entire pipeline runs under a configurable total timeout (default: 120s, bumped to 180s in deep mode). Each phase has a sub-budget:

- Seed validation: 15s
- All strategies: remaining - 10s reserve
- DNS resolution: remaining - 2s reserve
- GeoDNS: remaining - 3s reserve
- RDAP corroboration: 15s cap
- Fingerprint corroboration (fingerprint mode): 30s cap
- Infrastructure checks: 10s hard cap

If any phase times out, completed results are preserved and the pipeline continues with what it has.
