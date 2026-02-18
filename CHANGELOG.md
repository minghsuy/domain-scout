# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
