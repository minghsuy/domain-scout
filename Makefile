.PHONY: install test lint format check build clean docker-build docker-run eval eval-baselines

install:
	uv sync --all-groups --all-extras

test:
	uv run pytest domain_scout/tests -m "not integration" -v

test-integration:
	uv run pytest domain_scout/tests -m integration -v --timeout=120 --no-cov

lint:
	uv run ruff check domain_scout/
	uv run mypy domain_scout/

format:
	uv run ruff check --fix domain_scout/
	uv run ruff format domain_scout/

eval:
	uv run python -m domain_scout.eval --mode baseline

# (Re)generate the git-ignored baseline substrate + manifest via live discovery.
# Point-in-time snapshot of CT data — see CLAUDE.md "Eval substrate". LIMIT scopes
# a smoke run, e.g. `make eval-baselines LIMIT=3`; omit LIMIT for the full sweep.
eval-baselines:
	uv run python -m domain_scout.eval --mode record $(if $(LIMIT),--limit $(LIMIT))

check: format lint test

build:
	uv build

clean:
	rm -rf dist/ build/ *.egg-info

docker-build:
	docker build -t domain-scout-ct .

docker-run:
	docker run -p 8080:8080 domain-scout-ct
