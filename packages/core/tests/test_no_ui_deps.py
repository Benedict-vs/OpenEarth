"""Phase 0 exit criterion: no Streamlit/folium anywhere under packages/.

Imports every openearth module and asserts no UI framework sneaked into the
dependency graph.
"""

from __future__ import annotations

import importlib
import pkgutil
import sys

import openearth

FORBIDDEN = ("streamlit", "folium", "branca", "altair")


def _walk_modules() -> list[str]:
    names = []
    for info in pkgutil.walk_packages(openearth.__path__, prefix="openearth."):
        names.append(info.name)
    return names


def test_every_module_imports_without_ui_frameworks() -> None:
    modules = _walk_modules()
    assert len(modules) >= 15  # sanity: the walk actually found the package

    for name in modules:
        importlib.import_module(name)

    loaded = {m.split(".")[0] for m in sys.modules}
    for forbidden in FORBIDDEN:
        assert forbidden not in loaded, f"{forbidden!r} was imported by a core module"
