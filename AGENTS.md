# domain-scout — Agent Guidelines

## Pre-Session Checklist

Before starting any task:

1. Run `gh pr list --repo minghsuy/domain-scout --state open`. For each
   potentially overlapping PR, inspect its changed paths with
   `gh pr view <number> --repo minghsuy/domain-scout --json files`; stop if it
   already touches the target files.
2. Read `CLAUDE.md` for the current architecture and evaluation constraints.
3. Run `git status --short --branch`. If the tree is dirty, do not stash,
   clean, reset, or overwrite local/generated data; work from an isolated
   worktree based on the current upstream branch.

## Verification

On a fresh checkout or worktree, run `make install` first so the API, cache,
metrics, and evaluation extras are present. Run `make check` before pushing; it
covers formatting, Ruff, strict mypy, and the mocked unit suite.

- Do not run `make test-integration` without explicit permission; it calls live
  crt.sh, RDAP, and DNS services.
- Do not run `make eval-baselines` without explicit permission; it performs a
  long live-data recording pass and replaces the local evaluation manifest.

## Repository Boundaries

- This is the public MIT engine. Keep billing, API-key tiers, customer usage,
  and other commercial-service behavior in `domain-scout-api`.
- Keep public-facing files free of domain-specific customer/use-case language.
- Never commit `SPEC.md`, security reports, secrets, caches, or the git-ignored
  `baselines/` evaluation substrate.
- Use `uv`, not `pip`, for dependency management.
- Overlap with `CLAUDE.md` is intentional so non-Claude agents receive the
  same safety rules. Keep duplicated constraints aligned when either file
  changes.

## Code Review Rules

### Public evidence contracts

- Treat CLI/API fields, `EvidenceRecord`, `RunMetadata`, scoring inputs, and
  profile semantics as public contracts. Prefer additive compatibility, retain
  provenance, and require focused tests plus changelog documentation for
  externally observable changes.

### Async and network safety

- Flag blocking network or database calls on the event loop, unbounded fan-out,
  and missing timeouts or circuit-breaker behavior. Keep synchronous crt.sh
  Postgres work in an executor, preserve bounded concurrency, and never use the
  JSON fallback for organization-verified searches because it lacks subject
  organization data.

### Evaluation integrity

- The baseline manifest is the source of truth: missing, partial, stale-schema,
  absent-file, or hash-mismatched substrates must fail loudly. Recording must
  write snapshots first and publish the manifest atomically last; never turn a
  missing substrate into a passing or neutral evaluation.
