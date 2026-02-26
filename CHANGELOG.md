# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.6.0] - 2026-02-25

### Added
- **Subsidiary-aware CT search** — discovers domains for EDGAR Exhibit 21 subsidiaries, ranked by brand distinctness scoring
- Expanded eval ground truth from 22 to 399 entities
- Adaptive k denominator in eval precision (precision@k uses min(k, owned_domains) as denominator)

### Changed
- Eval table shows "Found X/Y" instead of raw recall decimal for readability

### Fixed
- `normalize_org_name` was stripping "Inc" from inside words (e.g., "Incyte" → "yte")
- Subsidiary filter hardened against real EDGAR data edge cases
- Precedence bug in subsidiary ranking, defensive copy for input mutation, test gaps

## [0.5.0] - 2026-02-20

### Added
- **Evaluation harness** (`domain_scout.eval`) — measure precision@k, recall@k, NDCG@k against labeled ground truth
- 22 labeled ground truth entries across 7 sectors (finance, healthcare, retail, energy, automotive, media, technology)
- 22 pre-recorded baseline JSON files for reproducible scoring without network access
- `make eval` target runs baseline evaluation
- `eval` optional extra for pyyaml dependency (`pip install domain-scout-ct[eval]`)
- CLI entry point: `python -m domain_scout.eval [--mode baseline|live] [--output table|json] [--label ID]`
- 21 new unit tests + 1 integration test (379 unit, 4 integration total)

## [0.4.0] - 2026-02-20

### Added
- **Prometheus metrics** — optional `prometheus-client` dependency with 7 metrics: `scans_total`, `scan_duration_seconds`, `domains_found`, `ct_queries_total`, `ct_fallbacks_total`, `ct_circuit_breaker_state`, `source_errors_total`
- `/metrics` endpoint on the REST API (Prometheus scrape target)
- No-op metric stubs when `prometheus-client` is not installed (zero overhead)
- **CLI tests** — full coverage for `scout`, `serve`, `diff`, and `cache` commands
- **DNS checker tests** — resolution, nameserver, infrastructure sharing, GeoDNS
- **RDAP parsing tests** — registrant org/name/country extraction, vCard edge cases
- **ScoutConfig tests** — validation, profiles, deep mode overrides
- 53 new unit tests (358 total)

### Changed
- CT log SAN aggregation uses separate `set` tracking for O(1) deduplication
- X.509 subject parser extracted as standalone function with proper quote/escape handling
- Redundant wrapper functions removed from `ct_logs.py`
- RDAP vCard extraction returns typed `object | None` with proper `isinstance` guards
- CT JSON fallback now emits `ct_queries_total{backend=json,status=error}` counter on failure

### Fixed
- RDAP error counter was unreachable in `scout.py` — moved to `rdap.py` where exceptions are caught
- Circuit breaker state gauge now updates on all transitions (closed/open/half_open)
- CLI `_get_cache_or_exit` helper DRYs import/instantiate pattern

## [0.3.1] - 2026-02-17

### Added
- **RDAP corroboration phase** — queries RDAP registrant org on top discovered domains and adds `rdap_registrant_match` source when org matches. Configurable via `rdap_corroborate_max` (default 10, broad=15, strict=20)
- `rdap_org` field on `EvidenceRecord` and `DiscoveredDomain` (optional, backward-compatible)
- 15 new unit tests (249 total) covering corroboration levels and RDAP corroboration edge cases

### Changed
- **Corroboration-level scoring model** replaces additive boost system:
  - Level 3 (+0.10): resolves + (RDAP match or high org similarity) + multi-source
  - Level 2 (+0.05): resolves + (RDAP match or high similarity or multi-source)
  - Level 1 (±0.00): resolves only — DNS resolution is now neutral, not a free boost
  - Level 0 (−0.05): no resolution — CT-only evidence is penalized
- Most non-resolving domain scores shift −0.05; RDAP-confirmed domains gain +0.05 to +0.10
- `inclusion_threshold` defaults unaffected — lowest CT score (0.35) was already below threshold

### Fixed
- Float precision in `_infra_boost` — `0.80 + 0.05` produced `0.8500000000000001`, now uses `round(..., 2)`

## [0.3.0] - 2026-02-17

### Added
- **REST API** — `domain-scout serve` starts a FastAPI server with `/scan`, `/health`, and `/ready` endpoints
- **DuckDB query cache** — `--cache` flag caches CT and RDAP results locally (4h and 24h TTL respectively)
- **Cache CLI** — `domain-scout cache stats` and `domain-scout cache clear` commands
- **Dockerfile** — multi-stage build with non-root user and cache volume
- **Makefile targets** — `make docker-build` and `make docker-run`
- **Acceptance tests** — Walmart fixture tests with source-level mocks exercise full scoring pipeline
- **Property-based tests** — hypothesis tests for matching symmetry, normalization idempotency, score range, IPv4 rejection, cache round-trip
- 58 new unit tests (234 total)

### Fixed
- JSON fallback was setting `org_name` to CA issuer name (e.g. "DigiCert"), causing false positives when company name matched a CA — now `None`
- Confidence boost stacking was uncapped — three boosts (+0.10/+0.05/+0.05) pushed `ct_org_match` (0.85) to 1.00. Total boost now capped at +0.10
- Infrastructure boost bypassed the cap — `shared_infra` added +0.05 after scoring, pushing 0.95→1.00. Now capped at 0.95 for non-cross-seed domains
- `_normalize_time` format inconsistency — cache returned space-separated datetimes (`2025-01-01 00:00:00`), live CT returned T-separated (`2025-01-01T00:00:00`). Mixed comparison broke silently
- `extract_base_domain` processed IPv4 addresses — `8.8.8.8` returned `"8.8"` instead of `None`
- Removed `rdap_match` dead code in confidence scoring (source was never set anywhere)

### Changed
- `Scout.__init__` accepts optional `cache` parameter for transparent caching
- FastAPI, uvicorn, duckdb moved to optional extras (`[api]`, `[cache]`, `[all]`)

## [0.2.2] - 2026-02-17

### Fixed
- Package metadata lookup in `scout.py` used old name "domain-scout" instead of "domain-scout-ct" (runtime crash on fresh installs)
- Added `py.typed` marker file (required for "Typing :: Typed" classifier)
- CI now checks code formatting (`ruff format --check`)
- Tests excluded from wheel distribution (smaller package)

## [0.2.1] - 2026-02-17

### Added
- PyPI publishing via GitHub Actions with OIDC trusted publishing (tag push triggers release)
- PyPI metadata: authors, classifiers, keywords, project URLs
- `make build` and `make clean` targets
- PyPI badge in README

### Changed
- PyPI package name is `domain-scout-ct` (CLI command remains `domain-scout`)

## [0.2.0] - 2026-02-16

### Added
- Multi-seed discovery with cross-verification — domains found by 2+ seeds get a confidence boost
- Structured evidence model (`EvidenceRecord` with `source_type`, `cert_id`, `similarity_score`)
- `RunMetadata` for audit reproducibility (tool version, timestamp, config snapshot)
- Discovery profiles (`--profile broad|balanced|strict`) via `ScoutConfig.from_profile()`
- Org-name normalization: acronym detection (CamelCase-aware), abbreviation expansion, DBA dual-match, brand aliases, conglomerate guard
- Positional suffix anchoring — ambiguous suffixes (Group, Holdings, Co, AG, SA, SE, NV, AB) only stripped from end of name
- 176 unit tests

## [0.1.0] - 2026-02-15

### Added
- Initial release
- CT log discovery via crt.sh (Postgres primary, JSON API fallback)
- RDAP registrant lookup via rdap.org (universal bootstrap)
- DNS resolution checking (8.8.8.8, 1.1.1.1)
- Shodan GeoDNS deep mode for non-resolving domains
- Fuzzy org-name matching via rapidfuzz
- CLI with table and JSON output formats
- Async Python API (`Scout.discover_async()`)
