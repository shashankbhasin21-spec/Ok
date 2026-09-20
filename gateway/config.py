"""Gateway configuration — fail closed when required secrets are missing."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    api_key: str = Field(default="", alias="API_KEY")
    worker_token: str = Field(default="", alias="WORKER_TOKEN")
    database_url: str = Field(
        default="sqlite:///./data/video_gateway.db",
        alias="DATABASE_URL",
    )
    storage_path: Path = Field(default=Path("./data"), alias="STORAGE_PATH")
    video_worker_url: str = Field(default="", alias="VIDEO_WORKER_URL")
    default_engine: str = Field(default="auto", alias="DEFAULT_ENGINE")
    model_cache: Path = Field(default=Path("./models"), alias="MODEL_CACHE")
    hf_home: Path = Field(default=Path("./models/hf"), alias="HF_HOME")
    hf_token: str = Field(default="", alias="HF_TOKEN")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    max_vram_percent: float = Field(default=90.0, alias="MAX_VRAM_PERCENT")
    gateway_host: str = Field(default="0.0.0.0", alias="GATEWAY_HOST")
    gateway_port: int = Field(default=8080, alias="GATEWAY_PORT")
    allow_unauthenticated: bool = Field(
        default=False,
        alias="ALLOW_UNAUTHENTICATED",
    )
    min_social_duration: float = Field(default=30.0, alias="MIN_SOCIAL_DURATION")
    default_aspect_ratio: str = Field(default="9:16", alias="DEFAULT_ASPECT_RATIO")
    default_fps: int = Field(default=30, alias="DEFAULT_FPS")
    compute_cost_per_gpu_hour_usd: float = Field(
        default=0.50,
        alias="COMPUTE_COST_PER_GPU_HOUR_USD",
    )
    auth_mode: Literal["required", "dev"] = Field(default="required", alias="AUTH_MODE")

    @property
    def auth_required(self) -> bool:
        # AUTH_MODE=required must fail closed even when API_KEY is missing
        # (missing key → 503 in require_api_key, never open access).
        if self.allow_unauthenticated or self.auth_mode == "dev":
            return False
        return True

    def ensure_dirs(self) -> None:
        for sub in ("jobs", "outputs", "thumbnails", "temp"):
            (self.storage_path / sub).mkdir(parents=True, exist_ok=True)
        self.model_cache.mkdir(parents=True, exist_ok=True)
        self.hf_home.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    os.environ.setdefault("HF_HOME", str(settings.hf_home))
    return settings
