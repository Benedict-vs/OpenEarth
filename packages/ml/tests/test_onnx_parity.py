"""torch ↔ onnxruntime parity at a non-training shape (CI-runnable, untrained tiny model).

No committed weights: the model is built fresh here. Validates that the exported
ONNX is dynamic-shape (runs at a size other than the export dummy) and that ORT's
sigmoid matches torch's within 1e-4 — the serve path the API relies on.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import onnxruntime as ort
import torch

from openearth_ml.export import export_onnx
from openearth_ml.models import build_unet


def test_torch_ort_parity_dynamic_shape(tmp_path: Path) -> None:
    torch.manual_seed(0)
    model = build_unet(in_channels=5, encoder_weights=None)
    model.eval()
    path = tmp_path / "m.onnx"
    exporter = export_onnx(model, path, example_hw=(128, 128))
    assert exporter in {"dynamo", "torchscript"}

    sess = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    name = sess.get_inputs()[0].name

    # a shape *different* from the export dummy (still a /32 multiple for the U-Net)
    x = np.random.default_rng(1).normal(size=(1, 5, 96, 96)).astype(np.float32)
    with torch.no_grad():
        torch_sig = torch.sigmoid(model(torch.from_numpy(x))).numpy()
    ort_logits = sess.run(None, {name: x})[0]
    ort_sig = 1.0 / (1.0 + np.exp(-ort_logits))

    assert torch_sig.shape == ort_sig.shape == (1, 1, 96, 96)
    assert float(np.max(np.abs(torch_sig - ort_sig))) <= 1e-4
