UV ?= uv
UV_CACHE_DIR ?= .cache/uv
export UV_CACHE_DIR

.PHONY: help sync format lint typecheck test test-verbose coverage check build smoke wiki clean

help:
	@$(UV) run dpcompat --help

sync:
	$(UV) sync --all-groups

format:
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

lint:
	$(UV) run ruff format --check .
	$(UV) run ruff check .

typecheck:
	$(UV) run mypy
	$(UV) run pyright

test:
	$(UV) run pytest -q

test-verbose:
	$(UV) run pytest -vv

coverage:
	$(UV) run pytest --cov=dpcompat --cov-report=term-missing

check: lint typecheck test

build: check
	$(UV) build

smoke:
	$(UV) run dpcompat versions
	$(UV) run dpcompat inspect examples/simple_pack

wiki:
	$(UV) run python scripts/sync_wiki.py --output .wiki

clean:
	$(UV) run python scripts/clean.py
