"""ONNX export + model manifest — the serving contract for the onnxruntime API.

The network is fully convolutional, so it exports with dynamic H/W and the API
pads serve-time chips to ``PAD_MULTIPLE`` with ``channels.pad_to_multiple``. The
manifest (a sibling ``.json`` of the ``.onnx``) carries the channel order and the
``ChannelStats`` the API applies verbatim — the API reads them from here, never
from code constants, so a model swapped without its manifest fails loudly.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import numpy as np
import torch
from torch import nn

from openearth.methane.channels import CHANNELS, PAD_MULTIPLE

OPSET = 18
THRESHOLD = 0.5
MIN_PX = 5
LICENSE_LINE = "trained on CH4Net (CC-BY-NC-ND 4.0) — do not redistribute; not for commercial use."


def export_onnx(
    model: nn.Module, path: Path, *, opset: int = OPSET, example_hw: tuple[int, int] = (128, 128)
) -> str:
    """Export *model* to ONNX with dynamic batch/H/W. Returns the exporter used.

    Tries the dynamo exporter first (recommended for opset ≥ 18); falls back to
    the TorchScript exporter with ``dynamic_axes`` if dynamo is unavailable/fails.
    """
    model.eval()
    dummy = torch.zeros(1, len(CHANNELS), *example_hw)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        torch.onnx.export(
            model,
            (dummy,),
            str(path),
            input_names=["input"],
            output_names=["logits"],
            opset_version=opset,
            dynamo=True,
            dynamic_shapes={"x": {0: "batch", 2: "height", 3: "width"}},
        )
        return "dynamo"
    except Exception as exc:  # dynamo API churns; the TorchScript path is the safety net
        print(f"dynamo export failed ({type(exc).__name__}: {exc}); using TorchScript exporter")
        torch.onnx.export(
            model,
            (dummy,),
            str(path),
            input_names=["input"],
            output_names=["logits"],
            opset_version=opset,
            dynamic_axes={
                "input": {0: "batch", 2: "height", 3: "width"},
                "logits": {0: "batch", 2: "height", 3: "width"},
            },
        )
        return "torchscript"


def measure_latency_ms(onnx_path: Path, hw: tuple[int, int], *, runs: int = 30) -> float:
    """Median single-chip onnxruntime CPU latency (ms) at padded size *hw*."""
    import onnxruntime as ort

    sess = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    name = sess.get_inputs()[0].name
    x = np.zeros((1, len(CHANNELS), *hw), dtype=np.float32)
    for _ in range(3):  # warm-up
        sess.run(None, {name: x})
    times = []
    for _ in range(runs):
        t0 = time.perf_counter()
        sess.run(None, {name: x})
        times.append((time.perf_counter() - t0) * 1000.0)
    return float(np.median(times))


def _git_hash() -> str:
    """Full HEAD hash, ``-dirty`` when the tree has uncommitted changes (fix 10b)."""
    try:
        h = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        dirty = subprocess.check_output(["git", "status", "--porcelain"], text=True).strip()
        return f"{h}-dirty" if dirty else h
    except Exception:
        return "unknown"


def write_manifest(
    path: Path,
    *,
    model_version: str,
    stats: dict,
    exporter: str,
    latency_ms_p50: float,
    latency_chip_hw: tuple[int, int],
    provenance: dict,
    threshold: float = THRESHOLD,
) -> None:
    """The serving contract next to the ``.onnx`` (lives in data_dir — not committed)."""
    manifest = {
        "model_version": model_version,
        "onnx_file": f"{model_version}.onnx",
        "channels": list(CHANNELS),
        "channel_stats": stats,  # {channels, median, mad} — applied verbatim at serve time
        "pad_multiple": PAD_MULTIPLE,
        "opset": OPSET,
        "exporter": exporter,
        "input_layout": "NCHW; channels in `channels` order; normalized by channel_stats",
        "threshold": threshold,  # deployed = median of the CV folds' inner-val thresholds
        "min_px": MIN_PX,
        "latency_ms_p50": round(latency_ms_p50, 2),
        "latency_chip_hw": list(latency_chip_hw),
        "provenance": {"git_hash": _git_hash(), **provenance},
        "license": LICENSE_LINE,
        "not_for_public_deployment": (
            "CC-BY-NC-ND: the trained weights derive from CH4Net; do not publish/redistribute."
        ),
    }
    path.write_text(json.dumps(manifest, indent=2))


def load_channel_stats(run_dir: Path) -> dict:
    return json.loads((run_dir / "channel_stats.json").read_text())


def build_deployment(
    run_dir: Path, out_dir: Path, *, model_version: str = "plume_unet_v1", encoder: str = "resnet18"
) -> dict:
    """Load the deployed checkpoint → ONNX + manifest in *out_dir*; return the manifest."""
    from openearth_ml.models import build_unet

    stats = load_channel_stats(run_dir)
    model = build_unet(encoder_name=encoder, encoder_weights=None)
    state = torch.load(run_dir / "deployed.pt", map_location="cpu")
    model.load_state_dict(state)

    onnx_path = out_dir / f"{model_version}.onnx"
    exporter = export_onnx(model, onnx_path)
    chip_hw = (128, 96)  # a representative padded serve-time chip
    latency = measure_latency_ms(onnx_path, chip_hw)

    cv_f1, cv_protocol, threshold = None, None, THRESHOLD
    eval_json = Path("scripts/data/ml_eval_v2.json")
    if eval_json.exists():
        ev = json.loads(eval_json.read_text())
        cv_f1 = ev["aggregate"]["model_scene_f1"]
        cv_protocol = ev.get("cv_protocol")
        # Deployed threshold = median of the folds' inner-val-selected thresholds.
        threshold = float(ev["aggregate"].get("deployed_threshold", THRESHOLD))

    write_manifest(
        out_dir / f"{model_version}.json",
        model_version=model_version,
        stats=stats,
        exporter=exporter,
        latency_ms_p50=latency,
        latency_chip_hw=chip_hw,
        threshold=threshold,
        provenance={
            "encoder": encoder,
            "cv_scene_f1": cv_f1,
            # The number carries its protocol qualifier wherever it resurfaces (fix 10b).
            "cv_protocol": cv_protocol,
            "trained_on": "CH4Net (recovered metadata); weights not committed",
        },
    )
    return {"exporter": exporter, "latency_ms_p50": latency, "onnx": str(onnx_path)}


def main() -> None:
    import argparse

    from openearth.settings import get_settings

    p = argparse.ArgumentParser(description="Export the deployed U-Net to ONNX + manifest.")
    p.add_argument("--model-version", default="plume_unet_v1")
    args = p.parse_args()
    data_dir = get_settings().data_dir
    run_dir = data_dir / "ml" / "runs" / args.model_version
    out_dir = data_dir / "ml" / "models"
    info = build_deployment(run_dir, out_dir, model_version=args.model_version)
    print(f"exported via {info['exporter']}: {info['onnx']}")
    print(f"latency_ms_p50 = {info['latency_ms_p50']:.1f} ms  (gate < 1000)")
    print("  (ONNX + manifest are CH4Net derivatives — never commit; under data_dir/ml/models)")


if __name__ == "__main__":
    main()
