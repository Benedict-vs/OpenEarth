"""Catalog read endpoints and custom-dataset CRUD."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from openearth.settings import Settings
from openearth_api.app import create_app

DEM_TOML = """
[dataset]
id = "dem"
title = "Copernicus DEM GLO-30"
collection_id = "COPERNICUS/DEM/GLO30"
attribution = "ESA"
default_scale_m = 30

[products.DEM]
name = "Elevation"
vis_min = 0
vis_max = 4000
valid_min = -500
valid_max = 9000
display_unit = "m"
"""


def test_list_catalog_builtins(client: TestClient) -> None:
    body = client.get("/api/catalog").json()
    ids = {ds["id"] for ds in body}
    assert ids == {"s5p", "s2", "s1"}
    s2 = next(ds for ds in body if ds["id"] == "s2")
    assert s2["is_custom"] is False
    ndvi = next(p for p in s2["products"] if p["key"] == "NDVI")
    assert ndvi["requires_builder"] is False
    anomaly = next(p for p in s2["products"] if p["key"] == "CH4_ANOMALY")
    assert anomaly["requires_builder"] is True


def test_get_single_dataset_and_404(client: TestClient) -> None:
    assert client.get("/api/catalog/s5p").json()["id"] == "s5p"
    response = client.get("/api/catalog/nope")
    assert response.status_code == 404
    assert "s5p" in response.json()["detail"]  # error lists valid ids


def test_create_custom_dataset_persists_and_appears(
    client: TestClient, test_settings: Settings
) -> None:
    response = client.post("/api/catalog/custom", json={"toml": DEM_TOML})
    assert response.status_code == 201
    body = response.json()
    assert body["id"] == "dem"
    assert body["is_custom"] is True
    assert (test_settings.data_dir / "catalog.d" / "dem.toml").is_file()
    assert "dem" in {ds["id"] for ds in client.get("/api/catalog").json()}


def test_custom_dataset_survives_app_restart(client: TestClient, test_settings: Settings) -> None:
    client.post("/api/catalog/custom", json={"toml": DEM_TOML})
    # Simulate a restart: fresh registry, fresh app over the same data dir.
    from openearth.catalog import clear_user_datasets

    clear_user_datasets()
    with TestClient(create_app(settings=test_settings)) as second_client:
        assert second_client.get("/api/catalog/dem").status_code == 200


def test_create_duplicate_and_builtin_ids_conflict(client: TestClient) -> None:
    assert client.post("/api/catalog/custom", json={"toml": DEM_TOML}).status_code == 201
    assert client.post("/api/catalog/custom", json={"toml": DEM_TOML}).status_code == 409
    builtin = DEM_TOML.replace('id = "dem"', 'id = "s2"')
    assert client.post("/api/catalog/custom", json={"toml": builtin}).status_code == 409


def test_create_invalid_toml_gives_422_with_loader_message(client: TestClient) -> None:
    bad = DEM_TOML.replace("vis_min = 0", "vismin = 0")
    response = client.post("/api/catalog/custom", json={"toml": bad})
    assert response.status_code == 422
    assert "unknown key" in response.json()["detail"]
    assert "vismin" in response.json()["detail"]


def test_delete_custom_dataset(client: TestClient, test_settings: Settings) -> None:
    client.post("/api/catalog/custom", json={"toml": DEM_TOML})
    assert client.delete("/api/catalog/custom/dem").status_code == 204
    assert client.get("/api/catalog/dem").status_code == 404
    assert not (test_settings.data_dir / "catalog.d" / "dem.toml").exists()


def test_delete_builtin_and_unknown(client: TestClient) -> None:
    assert client.delete("/api/catalog/custom/s2").status_code == 409
    assert client.delete("/api/catalog/custom/ghost").status_code == 404


def test_lifespan_loads_catalog_dir(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, ee_project=None, data_dir=tmp_path / "data")
    catalog_dir = settings.data_dir / "catalog.d"
    catalog_dir.mkdir(parents=True)
    (catalog_dir / "dem.toml").write_text(DEM_TOML)
    with TestClient(create_app(settings=settings)) as client:
        assert client.get("/api/catalog/dem").status_code == 200
