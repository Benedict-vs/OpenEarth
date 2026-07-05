"""API test fixtures: hermetic settings, fresh app, isolated user registry.

No Earth Engine anywhere: settings are constructed explicitly (no env, no
.env file), ``ee_project`` stays ``None`` so the lifespan never attempts EE
init, and EE-touching routes get their dependencies overridden per test.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openearth.catalog import clear_user_datasets
from openearth.settings import Settings
from openearth_api.app import create_app


@pytest.fixture(autouse=True)
def _isolated_user_registry() -> Iterator[None]:
    clear_user_datasets()
    yield
    clear_user_datasets()


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, ee_project=None, data_dir=tmp_path / "data")


@pytest.fixture
def app(test_settings: Settings) -> FastAPI:
    return create_app(settings=test_settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    # Context manager runs the lifespan (catalog dir load, cache open).
    with TestClient(app) as test_client:
        yield test_client
