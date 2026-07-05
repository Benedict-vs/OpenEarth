"""The API must never import UI frameworks, torch, or ML stacks.

Mirrors core's ``test_no_ui_deps.py``: import every ``openearth_api``
submodule, then assert none of the forbidden top-level packages made it
into ``sys.modules``. (SQLModel and sse-starlette became first-class API
dependencies in Phase 2 — the DB/job layer — so they are no longer barred.)
"""

from __future__ import annotations

import importlib
import pkgutil
import sys

import openearth_api

FORBIDDEN = (
    "torch",
    "streamlit",
    "folium",
    "branca",
    "altair",
)


def test_api_imports_no_heavy_or_ui_deps() -> None:
    imported = ["openearth_api"]
    for module_info in pkgutil.walk_packages(openearth_api.__path__, prefix="openearth_api."):
        importlib.import_module(module_info.name)
        imported.append(module_info.name)

    assert len(imported) >= 8, f"Suspiciously few modules imported: {imported}"

    top_level = {name.split(".")[0] for name in sys.modules}
    offenders = sorted(top_level & set(FORBIDDEN))
    assert not offenders, f"Forbidden dependencies imported by the API: {offenders}"
