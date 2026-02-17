# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
