.PHONY: sync test test-ee lint fmt typecheck check api dev gen

sync:            ## Install/refresh the whole dev environment
	uv sync --all-packages

test:            ## Offline unit tests (no Earth Engine)
	uv run pytest

test-ee:         ## Live Earth Engine tests (needs real auth)
	OPENEARTH_EE_TESTS=1 uv run pytest -m ee

lint:            ## Ruff lint + format check
	uv run ruff check .
	uv run ruff format --check .

fmt:             ## Auto-format
	uv run ruff check --fix .
	uv run ruff format .

typecheck:       ## mypy (strict) on core
	uv run mypy

check: lint typecheck test  ## Everything CI runs

api:             ## Run the FastAPI dev server
	uv run uvicorn openearth_api.main:app --reload --port 8000

dev:             ## Run API + web dev servers together
	./scripts/dev.sh

gen:             ## Regenerate OpenAPI schema + TypeScript API types
	uv run python scripts/export_openapi.py > apps/web/openapi.json
	pnpm --dir apps/web gen
