"""Runtime configuration via environment variables / .env (prefix ``OPENEARTH_``)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OPENEARTH_",
        env_file=".env",
        extra="ignore",
    )

    ee_project: str | None = None
    data_dir: Path = Path("data")
    ee_max_concurrency: int = 8
    # Optional override for the CH4 absorption LUT (for retrieval experiments);
    # None loads the packaged ``ch4_lut_v3.npz``.
    lut_path: Path | None = None
    # getMapId tile URLs are valid for ~4 h (undocumented, treated as an
    # assumption to measure). Consumers re-mint well before this expires.
    tile_ttl_seconds: int = 4 * 3600
    # ML tier (Phase 5): the ONNX model served via onnxruntime; None resolves to
    # ``data_dir/ml/models/plume_unet_v1.onnx`` (manifest = sibling ``.json``). The
    # weights are a CH4Net derivative, so they live in data_dir, never the repo.
    ml_model_path: Path | None = None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
