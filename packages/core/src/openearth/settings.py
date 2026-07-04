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
    # getMapId tile URLs are valid for ~4 h (undocumented, treated as an
    # assumption to measure). Consumers re-mint well before this expires.
    tile_ttl_seconds: int = 4 * 3600


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
