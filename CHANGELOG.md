# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- The learned-scorer artifact's own persisted metrics are now consumed at load
  time (#183). An acceptance gate skips the isotonic calibration layer when the
  artifact reports `lr_calibrated_ece > lr_ece` — true for the shipped v1
  artifact (0.2182 vs 0.0072), so raw LR probabilities are used and
  `scorer_version()` stamps a `+uncal` suffix; a future artifact with good
  calibration gets the layer back automatically. The artifact schema gains
  `inference_unavailable_features` declaring features the model uses but
  inference zero-fills (`org_matches_different_entity`, coefficient +0.204 —
  confidence biased low where that signal would fire); the loader warns once at
  load, and rejects features it can't compute at all. The eval harness now runs
  both scorer paths: `make eval` reports a heuristic leg (recorded ranking) and
  a learned leg (initially an approximation re-scoring persisted evidence;
  made an exact replay by #187 — see Changed), so the learned scorer is
  exercised before `use_learned_scorer` ever flips on.
- `DiscoveredDomain` now carries `scorer_id` and `scorer_version` (schema 1.1,
  #184): the heuristic ladder stamps `heuristic/<rule-set date>` and the learned
  scorer stamps `learned_lr/<artifact version>@<training date>`, so a persisted
  confidence value is no longer ambiguous about which of the two incomparable
  scorers produced it. Additive and defaulted to `unknown` — pre-1.1 result
  JSON still validates, and downstream consumers that read `confidence` are
  unaffected. `domain-scout diff` no longer reports confidence deltas between
  differing scorer identities (a scorer switch used to surface as hundreds of
  spurious "confidence changed" entries); it emits a single run-level
  `scorer_changed` warning instead, while non-confidence changes (`resolves`,
  `sources`, `rdap_org`) are still reported per domain.
- Eval substrate generator (`python -m domain_scout.eval --mode record`, wired as
  `make eval-baselines`): runs live discovery for the ground-truth entities and
  (re)writes the git-ignored `baselines/` substrate plus a provenance
  `baselines/manifest.json` referencing exactly the files that run produced
  (#188). Each manifest entry carries a `sha256`; the header stamps
  `generated_at`, `tool_version`, `scorer` identity, and `git_commit`, so the
  point-in-time snapshot's decay is detectable rather than silent. `--limit N`
  records a smoke-scale subset. Re-running is safe and overwrites the manifest to
  reference only the current run's outputs.

### Changed
- The eval's learned leg now replays production scoring **exactly** instead of
  approximating it (#187, substrate schema 2). `--mode record` captures each
  candidate domain's score-time inputs (`ScoringInputs`: pre-`_infra_boost`
  sources, pre-dedup `evidence_count`/`unique_cert_count`/`rdap_similarity`,
  and whether the boost later fired) into the baseline record
  (`BaselineRecord = ScoutResult + scoring_inputs`), and the learned leg scores
  from those — same gating, same feature inputs, same boost formula/cap/rounding
  as a live `use_learned_scorer=True` run (parity is asserted test-for-test
  against the real pipeline). Previously it re-scored persisted post-pipeline
  state, understating boosted domains' confidence (measured −0.0084 on the
  parity fixture; up to ~0.19 at mid-range operating points for the v1
  artifact). The manifest stamps `substrate_schema`; a substrate recorded under
  the old schema is refused loudly with a re-record message rather than
  silently scored the approximated way — regenerate with `make eval-baselines`.
  Remaining (disclosed) gap vs a live learned run: which candidates
  `_infra_boost` checked and which domains cleared `inclusion_threshold` were
  decided by the recorded run's heuristic confidences; a live learned run could
  select slightly different sets. That is a property of pipeline selection, not
  of scoring — the scoring inputs themselves are now exact.
- `make eval` now fails loudly (`EvalSubstrateError`, non-zero exit) instead of
  printing a warning and rendering an empty — and misleadingly "passing" —
  report when the baseline substrate is missing (#188). The `manifest.json` is
  now required and authoritative: no manifest, an unparseable manifest, or a
  referenced file that is absent or whose sha256 no longer matches is a hard
  error, and only manifest-listed files are evaluated. Loose `{label_id}.json`
  files without a manifest are not trusted (an interrupted `record` run can leave
  a partial set), so `record` writes the manifest atomically and last. `make
  eval` therefore requires a prior `make eval-baselines`. A substrate recorded
  under a different scorer than the current one emits a stderr warning. (The
  learned leg's post-pipeline re-scoring approximation, #187, was untouched
  there and is fixed by the substrate-schema-2 entry above.)
- The `ct_org_match` source now requires a strict word-bounded org-name match in
  addition to the fuzzy `org_match_threshold`. The fuzzy scorer credited
  raw-substring hits (`Aon` in `kaonavi`, `Generali` in `Generalist`),
  generic-word overlap (`… Insurance Group`), and bare single-token/city
  collisions, producing single-entity wrong-owner attributions that no frequency
  filter caught (e.g. Munich Re → UniCredit/HVB banking domains, Promutuel →
  Liberty Mutual). The new gate (`strict_org_name_match`, ported from
  insurance-market-db#200) rejects those classes while preserving exact,
  legal-suffixed, hyphenated (`Coca-Cola` → `Coca-Cola Company`), and
  abbreviation-form (`Palo Alto Tech` → `Palo Alto Tech Inc`) matches. The
  distinctive core and the certificate text now undergo identical normalization
  — hyphens fold to spaces on both sides and abbreviations are never expanded —
  so byte-identical names always match (an initial port drift folded the text
  but not the core, dropping every hyphenated/abbreviated name). This is
  precision-first, so some legitimate matches are still intentionally dropped:
  acronym-only cert-org matches (cert org `IBM` for target `International
  Business Machines`) and multi-word targets that share only one distinctive
  token with the cert org (`Sony Group` vs `Sony Corporation`, `Sompo Holdings`
  vs `Sompo Japan Insurance`) are not promoted to `ct_org_match` — the ≥2
  significant-token rule that rejects generic-word/city collisions (`Zurich
  Insurance Group` vs `Zurich Airport`) also requires the stripped generic word
  to reappear. This recall limitation is shared verbatim with the reference and
  left as-is: the reviewer-suggested relaxations all reintroduce the city-
  collision false positives the rule defends against. Other sources and
  thresholds are unchanged. (#174)
- `CTLogSource.search_by_org` now raises `CTOrgSearchUnavailableError` when the
  crt.sh Postgres backend is unavailable and org verification is required,
  instead of silently returning `[]` (the JSON fallback cannot verify subject
  org, so it was discarding 100% of records and reporting a false empty). The
  `Scout` pipeline catches this and records a "results partial" entry in
  `RunMetadata.errors`. Direct callers of the unexported `CTLogSource` should
  handle the new exception. New metric label: `ct_queries_total{status="skipped_org"}`.
  (#163)

### Fixed

- **First-instance-wins breakers** — CT/RDAP circuit breakers moved to
  class-level registries keyed by `(failure_threshold, recovery_timeout)`, so
  effective thresholds no longer depend on instance construction order; an
  autouse conftest fixture resets the shared state between tests (#172, #190)
- **RDAP semaphore sizing** — the shared per-loop semaphore is now sized from
  the process-wide `RDAP_MAX_CONCURRENT` constant instead of the first
  instance's config; `ScoutConfig.max_rdap_concurrent` is deprecated (kept for
  compatibility, non-default values log a warning) (#172)

## [0.11.0] - 2026-04-01

### Added

- **GLEIF corporate hierarchy expansion** — `Scout.discover()` now expands queries to known subsidiaries via GLEIF corporate tree data (#122)
  - `find_entity()`: 4-stage matching (exact → icase → prefix → fuzzy) with subsidiary-count tie-breaking
  - `expand_corporate_tree()`: includes `IS_ULTIMATELY_CONSOLIDATED_BY` for multi-hop trees
  - Sibling dedup penalty (-0.15) prevents cross-attribution between sister entities
  - New `gleif` optional dependency: `pip install domain-scout-ct[gleif]`
- **`gleif-ingest` CLI command** — downloads GLEIF golden copy from gleif.org and builds local DuckDB file
- **`--gleif-db` CLI option** and `DOMAIN_SCOUT_GLEIF_DB` env var for specifying GLEIF database path
- **Signal metadata on evidence** — `EvidenceRecord.signal_type` and `signal_weight` fields expose which signals contributed to confidence (#123)
- **Subsidiary context in evidence** — descriptions for subsidiary domains now include the subsidiary name (e.g., "matches subsidiary 'PacifiCorp'") (#123)
- **Public RDAP extract functions** — `extract_registrar()`, `extract_dates()`, `extract_nameservers()`, `get_full_info()` (#119)

### Fixed

- **RDAP semaphore event loop** — semaphore now created lazily in `_ensure_semaphore()` bound to the current event loop, fixing `RuntimeError` in batch/notebook contexts (#120)
- **Evidence dedup** — cert-org evidence grouped by `(source_type, cert_org)` instead of per-cert, eliminating duplicate records (#123)
- **RDAP for subsidiaries** — registrant checked against cert org names (not just query entity), so subsidiary domains get proper RDAP corroboration (#123)

## [0.10.0] - 2026-03-21

### Added

- **Server-default environment variables** for API deployment: `DOMAIN_SCOUT_WAREHOUSE_PATH`, `DOMAIN_SCOUT_SUBSIDIARIES_PATH`, `DOMAIN_SCOUT_LOCAL_MODE` (#117)
- `subsidiaries_path` field on `/scan` request body
- Auto-enable `local_first` when `DOMAIN_SCOUT_WAREHOUSE_PATH` is set without explicit mode
- `_reject_traversal()` helper for centralized path validation

### Changed

- `ScanRequest.local_mode` defaults to `None` (server default) instead of `"disabled"` — backward compatible
- `get_app()` uses `typing.get_args(LocalMode)` for forward-compatible mode validation

### Fixed

- Explicit `DOMAIN_SCOUT_LOCAL_MODE=disabled` is now respected when warehouse path is also configured

## [0.9.0] - 2026-03-21

### Added

- **DNS fingerprint expansion mode** (`--mode fingerprint`): discover domains for companies using DV certificates where CT org search is blind. Extracts MX tenant IDs, NS zones, and SPF includes from seed domains, then verifies candidates against the fingerprint (#109)
- MX tenant parsing for Proofpoint, Microsoft 365, Barracuda, IronPort, and FireEye
- Shared-infra blocklists for NS zones (Cloudflare, AWS, Azure, Google, GoDaddy, etc.) and SPF includes (M365, Google, SendGrid, etc.) to prevent false positive matches
- `DNSChecker.get_mx_records()` and `get_txt_records()` with in-memory caching
- `DiscoveryMode` type: `--mode default|fingerprint` CLI flag
- `fp_candidate_limit` config field (default 200)
- 30+ new unit tests and acceptance tests for fingerprint mode

### Changed

- Fingerprint mode skips CT org search (Strategy A) and implies `--deep` mode
- MX tenant match treated as equivalent to `rdap_registrant_match` in corroboration scoring
- Mimecast excluded from MX tenant parsing (shared infrastructure, not per-customer)
- IP /24 prefix matching removed from fingerprint signals (CDN false positives)

### Fixed

- Claude Code Review workflow: PR number resolution for comment-triggered runs (#112)
- Claude Code Review workflow: feedback loop from bot comments re-triggering reviews (#113)

## [0.8.1] - 2026-03-19

### Fixed

- Changelog link on PyPI now uses absolute URL (relative links don't work on PyPI)
- Added CTScout API link to README header
- Docs deploy workflow fixed for newer Ubuntu runners

## [0.8.0] - 2026-03-19

### Added

- CTScout remote data source: query ctscout.dev warehouse with a free API key
  - `ctscout_api_key` config field + `--api-key` CLI flag
  - `CTSCOUT_API_KEY` env var for zero-config usage
  - Makes `pip install domain-scout-ct` useful out of the box (no local warehouse needed)
- RDAP rate limiter: semaphore (default 3 concurrent) + circuit breaker (3 failures → 30s cooldown)
- Config fields: `max_rdap_concurrent`, `rdap_cb_failure_threshold`, `rdap_cb_recovery_timeout`
- Issue templates (bug report, feature request) and SECURITY.md
- MkDocs Material documentation site with GitHub Pages deployment

### Changed

- Claude Code Review workflow uses custom prompt with `gh pr comment` instead of broken plugin

## [0.7.0] - 2026-03-07

### Added
- **Learned scorer** — 11-feature logistic regression model with isotonic calibration (GZ AUC 0.9992, 0 FN), replaces heuristic scoring when model file present (#36)
- **RDAP skip-TLDs** — skip RDAP lookups for 35 ccTLDs not in IANA bootstrap registry, eliminating noisy 404 logs (#37)
- **DuckDB local CT source** — `LocalParquetSource` and `HybridCTSource` for CT warehouse files (#38)
- **API key authentication** — `X-API-Key` header support for sensitive endpoints (`/scan`, `/diff`, `/cache/*`) (#58)
- **CONVENTIONS.md** — guardrails for automated tooling (Jules, Copilot) (#69)
- **Invariant tests** — CI gate tests to prevent regressions from automated PRs (#70)
- **Claude Code review** — GitHub Action for AI-assisted PR review (#71)
- `docs/rdap-cctld-support.md` — documents ccTLD RDAP landscape and maintenance schedule
- Unit tests for `_extract_sans` helper (#39), seed expansion error handling (#53)
- Shared test fixture `conftest.py` with `mock_result()` factory (#73)

### Changed
- Seed validation reuses CT records for expansion, eliminating duplicate crt.sh queries (#73)
- `resolves()` delegates to cached `get_ips()`, eliminating redundant DNS queries per seed (#73)
- DNS infrastructure check caches nameserver and IP lookups (#67)
- Seed validation pre-calculates base domains for O(1) lookup (#43)
- `_check` functions in `scout.py` refactored to use early returns (#50)
- `_strategy_org_search` refactored to reduce nesting (#68)
- RDAP architecture note in CLAUDE.md updated to reference skip-TLDs and quarterly review cadence

### Fixed
- **Path traversal vulnerability** in `warehouse_path` API parameter (#60)
- Unbounded list size in `seed_domain` input (#46)

### Removed
- Unused `CertRecord` model (#42)

## [0.6.0] - 2026-02-25

### Added
- **Subsidiary-aware CT search** — discovers domains for corporate subsidiaries, ranked by brand distinctness scoring
- Expanded eval ground truth from 22 to 399 entities
- Adaptive k denominator in eval precision (precision@k uses min(k, owned_domains) as denominator)

### Changed
- Eval table shows "Found X/Y" instead of raw recall decimal for readability

### Fixed
- `normalize_org_name` was stripping "Inc" from inside words (e.g., "Incyte" → "yte")
- Subsidiary filter hardened against real-world data edge cases
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
