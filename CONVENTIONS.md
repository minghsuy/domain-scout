# CONVENTIONS.md — Domain Scout Development Rules

This file encodes domain-specific context that automated tools (Jules, Copilot, etc.)
must respect when proposing changes.

## Architecture Invariants

### Phase Ordering in `_discover()`
The discovery pipeline has strict phase dependencies:
1. Seed validation (DNS + RDAP + CT) — must complete before seed-dependent strategies
2. Parallel strategies (org search, seed expansion, domain guessing, subsidiary)
3. DNS bulk resolution — must complete before RDAP corroboration
4. RDAP corroboration — depends on DNS resolution results
5. Confidence scoring — depends on all evidence collected
6. Infrastructure boost — depends on scored candidates
7. Output filtering — final step

Do NOT refactor `_discover()` into sub-methods or introduce a context dataclass.
The linear top-to-bottom flow is intentional for auditability.

### Scoring Priority Encoding
Nesting in `_score_confidence()` and `_check_*` functions encodes corroboration
priority tiers:
- Level 3: resolves + (rdap_match OR high_similarity) + 3+ sources → +0.10
- Level 2: resolves + one strong signal → +0.05
- Level 1: resolves only → +0.00
- Level 0: no resolution → -0.05

These tiers are intentionally nested if/elif chains. Do NOT flatten them into
guard clauses — the nesting makes the priority ordering explicit.

### Cross-Seed Boost Logic
`_apply_cross_seed_boost()` has nested conditionals that check:
1. Domain has sources from 2+ different seeds
2. At least one source is "strong" (ct_san_expansion or ct_org_match)

Both conditions must be true. The nesting is the logic.

## Do NOT Change

### MD5 in `local_parquet.py`
MD5 is used for **deterministic ID generation** from cert fingerprints, not
for cryptographic security. Changing the hash algorithm breaks all existing
parquet file IDs and delta reports. The `# noqa: S324` comment is intentional.

### `_normalize_time()` Validation
This function intentionally parses through `datetime.fromisoformat()` to validate
the input before re-serializing. Do NOT add a string-manipulation fast path —
invalid datetime strings must be caught and handled, not silently passed through.

### Closure Scoping in `api.py`
Route handlers in `create_app()` use closures to capture `app.state`. This is
idiomatic FastAPI factory pattern. Do NOT extract to module-level router — it
reduces testability (can't create independent app instances).

### Closure Scoping in `scout.py`
`_check` closures inside `_rdap_corroborate` and `_infra_boost` are intentionally
scoped to their parent function. Do NOT promote to instance methods.

## Refactoring Rules

### Minimum Complexity Threshold
- Functions under 15 lines do NOT need extract-method refactoring
- One level of nesting does NOT warrant a guard-clause PR
- Moving 6 lines from inline to a helper is not "reducing complexity"

### Optimization Requirements
- Optimization PRs must demonstrate impact on **realistic data sizes**
- Typical scan: <100 candidate domains, 1-5 seeds, <200 CT results
- "3.8x faster on 100K items" is irrelevant if the function processes 50 items
- Never sacrifice correctness (validation, error handling) for performance

### Behavioral Preservation
- Any change to scoring, confidence calculation, or corroboration logic
  must pass the existing eval harness (`eval_ground_truth.yaml`) with no
  regression in Precision@10 or NDCG@10
- Changes to hash functions, ID generation, or serialization format are
  breaking changes — they invalidate stored data

## Testing Rules

### Mock Patterns
- Mock at the source level (CTLogSource, RDAPLookup, DNSChecker), not at
  Scout attribute level
- Do NOT test implementation details (which internal methods were called)
- Tests that mock the entire implementation and assert mock calls are rejected
- Prefer integration-style tests with realistic fixtures (see Walmart fixture
  in test_acceptance.py)

### Error Handling Tests
- Verify graceful degradation: method returns partial results, errors are
  collected in the errors list, no exceptions propagate
- Test multiple exception types: TimeoutError, ConnectionError, generic Exception
- Verify Prometheus metrics are incremented on failure

## Data Context

### What Domain Scout Does
This tool discovers all internet domains owned by a company using Certificate
Transparency logs, RDAP, and DNS. It is NOT a generic web scraper, security
scanner, or domain registrar tool.

### Key Data Characteristics
- CT certificates have sparse org fields — many certs have no O= value
- SAN lists can contain 100+ domains on CDN/shared hosting certs
- RDAP responses vary wildly by registrar — many return no registrant data
- DNS resolution is not deterministic (geo-dependent, TTL-dependent)
- A company like Walmart owns 20-60 domains, not thousands

### Ground Truth
- 399 labeled companies in eval_ground_truth.yaml
- Key fixtures: Walmart (23 domains, 2 seeds), JPMorgan (60+ domains)
- False positive patterns: CDN domains (cloudflare, akamai), marketing
  platforms (hubspot, exacttarget), shared SSL certs
