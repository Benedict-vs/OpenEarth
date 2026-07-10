"""torch / segmentation-models-pytorch must never appear under core or api.

The API serves the model through onnxruntime alone; core is pure NumPy physics.
This statically scans every source file under ``openearth`` and ``openearth_api``
for a forbidden import — a *static* scan (not a ``sys.modules`` check) so it stays
correct even when another test in the same session imports torch (e.g. the ONNX
parity test in this package).
"""

from __future__ import annotations

import ast
from pathlib import Path

import openearth
import openearth_api

FORBIDDEN = {"torch", "segmentation_models_pytorch"}


def _imported_roots(source: str) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def test_core_and_api_never_import_torch_or_smp() -> None:
    files = [f for pkg in (openearth, openearth_api) for f in Path(pkg.__path__[0]).rglob("*.py")]
    assert len(files) >= 20, f"suspiciously few source files scanned: {len(files)}"

    offenders = [
        f"{f}: imports {sorted(_imported_roots(f.read_text()) & FORBIDDEN)}"
        for f in files
        if _imported_roots(f.read_text()) & FORBIDDEN
    ]
    assert not offenders, "torch/smp leaked into core or api:\n" + "\n".join(offenders)
