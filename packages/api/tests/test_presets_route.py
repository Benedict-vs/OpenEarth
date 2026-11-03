"""GET /api/presets/rois — pure catalog data, no EE."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_presets_include_all_categories(client: TestClient) -> None:
    body = client.get("/api/presets/rois").json()
    categories = {p["category"] for p in body}
    assert categories == {"continent", "country", "city", "methane_site"}
    names = [p["name"] for p in body]
    assert "Heidelberg (Germany)" in names


def test_methane_sites_carry_date_hints(client: TestClient) -> None:
    body = client.get("/api/presets/rois").json()
    sites = [p for p in body if p["category"] == "methane_site"]
    assert len(sites) == 7
    assert all(p["date_hint"] is not None for p in sites)
    korpezhe = next(p for p in sites if "Korpezhe" in p["name"])
    assert korpezhe["bbox"]["kind"] == "bbox"
    assert korpezhe["date_hint"][0] < korpezhe["date_hint"][1]


def test_polygon_preset_carries_outline_and_bounds(client: TestClient) -> None:
    body = client.get("/api/presets/rois").json()
    china = next(p for p in body if p["name"] == "China")
    assert china["category"] == "country"
    assert china["polygon"]["kind"] == "polygon"
    lons = [c[0] for c in china["polygon"]["coordinates"]]
    assert china["bbox"]["west"] == min(lons)
    assert china["bbox"]["east"] == max(lons)
    assert all(p["polygon"] is None for p in body if p["category"] != "country")
