.PHONY: install test lint format check build clean docker-build docker-run eval

install:
	uv sync --all-groups --all-extras

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

eval:
	uv run python -m domain_scout.eval --mode baseline

check: format lint test

build:
	uv build

clean:
	rm -rf dist/ build/ *.egg-info

docker-build:
	docker build -t domain-scout-ct .

docker-run:
	docker run -p 8080:8080 domain-scout-ct
