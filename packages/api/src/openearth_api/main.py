"""Uvicorn entrypoint: ``uvicorn openearth_api.main:app --reload``."""

from openearth_api.app import create_app

app = create_app()
