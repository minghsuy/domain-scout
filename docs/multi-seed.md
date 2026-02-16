# Multi-Seed Domain Discovery

## Problem

When an entity owns multiple domains, a single seed only discovers domains that share certificates with *that one seed*. Real-world testing proved this:

| | `--seed generali.it` | `--seed generali.com` |
|---|---|---|
| **Total domains** | 59 | 57 |
| **In common** | 43 | 43 |
| **Unique to this seed** | 16 | 14 |

Neither seed alone sees the full picture. The `.it` seed finds Czech/Italian/Slovak domains, while `.com` finds investment-related domains.

## Solution: Tagged Sources + Cross-Seed Boost

### How it works

1. **Seed-tagged sources:** Instead of anonymous `ct_san_expansion`, sources are tagged with which seed produced them: `ct_san_expansion:walmart.com`. This preserves provenance.

2. **Parallel per-seed expansion:** Each seed runs its own Strategy B (SAN expansion) in parallel, all rate-limited by the existing crt.sh semaphore (max 5 concurrent queries).

3. **Cross-seed detection:** After all strategies complete, domains with seed-tagged sources from 2+ independent seeds receive a `cross_seed_verified` source with 0.90 base confidence.

4. **Seed-to-seed validation:** During validation, each seed's CT records are checked for the other seeds as SANs. If seeds share a certificate, that proves common ownership.

## Usage

```bash
# Single seed (backward compatible)
domain-scout --name "Walmart" --seed walmart.com

# Multiple seeds
domain-scout --name "Walmart" --seed walmart.com --seed samsclub.com

# Three seeds with deep mode
domain-scout --name "Generali" --seed generali.it --seed generali.com --seed generali.de --deep
```

The `--seed` flag is repeatable. Using 3+ seeds auto-bumps timeout to 150s.

## Confidence scoring

| Source | Base Score | Notes |
|--------|-----------|-------|
| `cross_seed_verified` | **0.90** | Found from 2+ independent seeds |
| `ct_org_match` | 0.85 | Cert O= matches company name |
| `ct_san_expansion:{seed}` | 0.80 | SAN on same cert as a seed |
| `ct_seed_subdomain:{seed}` | 0.75 | Subdomain of a seed |
| `rdap_match` | 0.70 | RDAP registrant matches |
| `ct_seed_related:{seed}` | 0.40 | Found in CT search for a seed |
| `dns_guess` | 0.30 | Only guessed + resolved |

Boosts: multi-source (+0.05/+0.10), resolves (+0.05), org similarity (+0.05), shared infra (+0.05).

## Scenario analysis

### Walmart + Sam's Club (cross-verification works)

Seeds `walmart.com` and `samsclub.com` both independently discover `walmartlabs.com` through separate CT searches. Two independent discovery paths converging on the same domain is hard to produce by coincidence.

- `walmartlabs.com`: `ct_san_expansion:walmart.com` + `ct_san_expansion:samsclub.com` + `cross_seed_verified` = 1.00

### Generali (overlapping ccTLD seeds)

Seeds `generali.it` and `generali.com` share 43 domains in common. The overlapping domains get cross-verification boost, while seed-unique domains retain their single-seed scores.

- `generali.de`: found from both seeds + org match = 1.00 (cross-verified)
- `ceskapojistovna.cz`: found only from `.it` seed = 0.80 (SAN expansion only)

### M&A / sold subsidiary (no false cross-verification)

If Walmart sells ASDA, `asda.com` might only appear from `walmart.com`'s historical certs, not from `samsclub.com`. Since it's only found from one seed, no cross-verification boost is applied.

- `asda.com`: `ct_san_expansion:walmart.com` only = 0.85 (no cross-seed boost)

### CDN false positives

CDN domains on multi-tenant certs are filtered by the CDN detection filter (10+ unrelated base domains + low org match). If a CDN domain appears as `ct_seed_related` from multiple seeds, cross-verification is **not applied** because there are no strong sources (`ct_san_expansion` or `ct_seed_subdomain`). The score stays at 0.40 base + minor boosts = 0.50, well below the 0.60 inclusion threshold.

## Code review findings

### Code-simplifier improvements applied

1. **Extracted `_extract_contributing_seeds()` helper** — eliminates duplicate seed extraction logic in `_apply_cross_seed_boost()` and `_build_output()`.

2. **Extracted `_collect_cert_names()` helper** — deduplicates SAN + CN collection pattern used in `_strategy_org_search()` and `_strategy_seed_expansion()`.

3. **Optimized `_validate_seed()` co-hosted detection** — replaced O(n*m) nested loop with a `base_to_seed` reverse lookup dict and set intersection.

4. **Cleaned up backward compat tests** — replaced `try/except/pass` with `_STUB_RESULT` shared fixture, removing fragile exception suppression.

5. **Removed stale `type: ignore` comments** — the code-simplifier fixed type annotations properly instead of suppressing warnings, reducing total mypy errors from 16 to 13.

### Review findings (no action needed)

1. **Shared `errors` list across parallel tasks** (low risk) — Multiple async tasks append to the same `errors` list. This is safe because Python's GIL prevents concurrent list mutations, and asyncio tasks only yield at `await` points. No action needed.

2. **`_apply_cross_seed_boost` unused `seeds` parameter** (nit) — The `seeds` parameter is passed but not used in the method body. Kept for API consistency — future enhancements may need it for filtering (e.g., only count verified seeds).

3. **Source count inflation with tagged sources** (acceptable) — A domain with `ct_san_expansion:a.com` + `ct_san_expansion:b.com` + `cross_seed_verified` has 3 sources, triggering the +0.10 multi-source boost. This is intentional — being found from multiple seeds *is* stronger evidence.

## Data model changes

- **`EntityInput.seed_domain`**: `str | None` -> `list[str]`. Default: `[]`. The `discover()` method accepts `str | None | list[str]` for backward compatibility and coerces to list internally.
- **`ScoutResult.seed_domain_assessment`**: `str | None` -> `dict[str, str]`. Maps each seed domain to its assessment (`confirmed`, `suspicious`, `invalid`, `timeout`, `error`).
- **`ScoutResult.seed_cross_verification`**: `dict[str, list[str]]` (new). Maps each seed to the list of other seeds that share certificates with it.
- **`DiscoveredDomain.seed_sources`**: `list[str]` (new). Which seed domains contributed to discovering this domain.
- **`DiscoveredDomain.evidence`**: `list[str]` -> `list[EvidenceRecord]` (v0.2.0). Each evidence entry is now a structured record with `source_type`, `cert_id`, `cert_org`, `similarity_score`, and `seed_domain`. See [API Reference](api.md#evidencerecord).
- **`ScoutResult.run_metadata`**: replaces `search_metadata: dict` (v0.2.0). Typed `RunMetadata` with `schema_version`, `tool_version`, `timestamp`, and config snapshot. See [API Reference](api.md#runmetadata).

## Test coverage

103 unit tests covering:

- **Cross-seed detection** (6 tests): single seed, two seeds, mixed types, same-seed-different-types, three seeds, non-seed sources
- **Scoring** (6 tests): cross_seed_verified base, tagged source parity with old untagged, no-seeds compat, combined scoring
- **Build output** (3 tests): seed_sources population, multi-seed is_seed, empty seeds
- **Backward compat** (3 tests): string seed, None seed, list seed
- **Model changes** (5 tests): defaults, constructors, serialization
- **Simulated scenarios** (5 tests): Walmart cross-verification, Generali overlap, M&A no-false-cross, CDN false positive, unrelated domains
- **Post-M&A edge cases** (3 tests): pre-integration brand (no cross-verify), divested subsidiary with mismatched org, cross-verify across different source types
- **Post-spin-off scenarios** (4 tests): shared legacy domain (HP/HPE), child-only domain (PayPal/eBay), non-resolving transition domain excluded from output, single-seed-only domain
- **Look-alike entities** (3 tests): independent domains no cross-verify (Delta Air/Faucet), weak-only shared infrastructure correctly rejected, completely isolated seeds (Apple Inc/Hospitality)
- **Cross-verification edge cases** (8 tests): empty evidence, 5-seed domain, duplicate seed no cross-verify, boost idempotency, score capping at 1.0, seed domain own-tag-only, seed cross-verified from other seed, `_extract_contributing_seeds` direct test
- **Build output edge cases** (3 tests): non-resolving excluded despite high confidence, below-threshold excluded, descending sort order

### Known limitations documented by tests

- **Shared infrastructure with strong sources**: If two unrelated companies share a cert with `ct_san_expansion` (not just `ct_seed_related`), the domain still gets cross-verified. The CDN filter catches large multi-tenant certs (10+ base domains), but smaller shared certs could still produce false positives.
- **Boost idempotency gap**: Calling `_apply_cross_seed_boost` twice is idempotent for sources (set) but appends duplicate evidence entries (list). Documented in `test_boost_idempotency`.

### Fixed in this PR

- **Weak-evidence escalation (fixed)**: Previously, two `ct_seed_related` tags from different seeds triggered `cross_seed_verified` (0.90 base), jumping to 1.0. Now, `_apply_cross_seed_boost` requires at least one strong source (`ct_san_expansion` or `ct_seed_subdomain`) to apply the boost. Weak-only cross-seed signals stay at their base score (0.50 with boosts, below inclusion threshold).
