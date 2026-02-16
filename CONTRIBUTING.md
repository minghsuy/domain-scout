# Contributing to domain-scout

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (dependency manager)

## Setup

```bash
git clone https://github.com/minghsuy/domain-scout.git
cd domain-scout
uv sync --all-groups
```

## Development Commands

```bash
make test          # Run unit tests (mocked external calls)
make lint          # Lint with ruff + type check with mypy
make format        # Auto-fix lint issues + format code
make check         # format + lint + test (run before pushing)
```

### Integration Tests

Integration tests hit live external services (crt.sh, RDAP, DNS). Run them locally before submitting changes to CT or RDAP code:

```bash
make test-integration
```

## Pull Request Process

1. Branch from `main`
2. Include tests for new functionality
3. Run `make check` — CI must pass
4. Open a PR against `main`

## Code Style

- **Formatter/linter**: ruff (line-length 100)
- **Type checking**: mypy with `--strict`
- **Testing**: pytest + pytest-asyncio

ruff and mypy are enforced in CI. Run `make format` to auto-fix formatting issues before committing.
