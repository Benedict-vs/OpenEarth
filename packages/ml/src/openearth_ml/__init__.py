"""OpenEarth ML tier — U-Net training, evaluation, and ONNX export.

This package is the *only* place torch and segmentation-models-pytorch appear in
the monorepo. It depends on ``openearth`` (core) for the shared channel stack and
retrieval, but neither core nor the API ever imports it — the API serves the
trained model through onnxruntime alone. Live steps (chip export, training,
export) are manual scripts; CI trains nothing.
"""

__all__: list[str] = []
