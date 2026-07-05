"""Dump the API's OpenAPI schema to stdout.

Works fully offline: ``create_app()`` does no Earth Engine work at creation
time (see its docstring). The web build consumes the committed
``apps/web/openapi.json`` via openapi-typescript, so web CI never needs
Python; a drift check in the python CI job keeps the two in sync.

Usage: uv run python scripts/export_openapi.py > apps/web/openapi.json
"""

from __future__ import annotations

import json
import sys

from openearth_api.app import create_app


def main() -> None:
    schema = create_app().openapi()
    json.dump(schema, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
