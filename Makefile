.PHONY: sync test test-ee lint fmt typecheck check legacy

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

legacy:          ## Run the frozen v1 Streamlit app
	cd legacy && uv run --project . streamlit run app/main.py
