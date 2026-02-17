.PHONY: install test lint format check build clean

install:
	uv sync --all-groups

test:
	uv run pytest domain_scout/tests -m "not integration" -v

test-integration:
	uv run pytest domain_scout/tests -m integration -v --timeout=120

lint:
	uv run ruff check domain_scout/
	uv run mypy domain_scout/ --ignore-missing-imports

format:
	uv run ruff check --fix domain_scout/
	uv run ruff format domain_scout/

check: format lint test

build:
	uv build

clean:
	rm -rf dist/ build/ *.egg-info
