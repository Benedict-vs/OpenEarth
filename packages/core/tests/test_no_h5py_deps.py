"""h5py must never appear under ``packages/`` — it is a script/test-only dep.

h5py reads the S2CH4 benchmark's netCDF4/HDF5 files, which only
``scripts/s2ch4_benchmark.py`` (and its fixture tests, via importlib) do. It
lives in the dev dependency group, NOT in any runtime package; this static scan
(same shape as ``test_no_ml_deps.py``) keeps it out of core/api/ml source.
"""

from __future__ import annotations

import ast
from pathlib import Path

import openearth
import openearth_api
import openearth_ml

FORBIDDEN = {"h5py"}


def _imported_roots(source: str) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def test_packages_never_import_h5py() -> None:
    files = [
        f
        for pkg in (openearth, openearth_api, openearth_ml)
        for f in Path(pkg.__path__[0]).rglob("*.py")
    ]
    assert len(files) >= 20, f"suspiciously few source files scanned: {len(files)}"

    offenders = [
        f"{f}: imports {sorted(_imported_roots(f.read_text()) & FORBIDDEN)}"
        for f in files
        if _imported_roots(f.read_text()) & FORBIDDEN
    ]
    assert not offenders, "h5py leaked into a runtime package:\n" + "\n".join(offenders)
