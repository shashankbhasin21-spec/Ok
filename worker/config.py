"""Worker configuration."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    worker_token: str = Field(default="", alias="WORKER_TOKEN")
    model_cache: Path = Field(default=Path("./models"), alias="MODEL_CACHE")
    hf_home: Path = Field(default=Path("./models/hf"), alias="HF_HOME")
    hf_token: str = Field(default="", alias="HF_TOKEN")
    default_engine: str = Field(default="auto", alias="DEFAULT_ENGINE")
    max_vram_percent: float = Field(default=90.0, alias="MAX_VRAM_PERCENT")
    storage_path: Path = Field(default=Path("./data"), alias="STORAGE_PATH")
    worker_host: str = Field(default="0.0.0.0", alias="WORKER_HOST")
    worker_port: int = Field(default=8090, alias="WORKER_PORT")
    worker_id: str = Field(default="worker-1", alias="WORKER_ID")
    wan_repo: Path = Field(default=Path("./third_party/Wan2.2"), alias="WAN_REPO")
    ltx_repo: Path = Field(default=Path("./third_party/LTX-Video"), alias="LTX_REPO")
    framepack_repo: Path = Field(
        default=Path("./third_party/FramePack"),
        alias="FRAMEPACK_REPO",
    )
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    allow_mock_inference: bool = Field(
        default=False,
        alias="ALLOW_MOCK_INFERENCE",
        description="TEST ONLY — never enable in production.",
    )

    def ensure_dirs(self) -> None:
        self.model_cache.mkdir(parents=True, exist_ok=True)
        self.hf_home.mkdir(parents=True, exist_ok=True)
        (self.storage_path / "temp").mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("HF_HOME", str(self.hf_home))
        if self.hf_token:
            os.environ.setdefault("HF_TOKEN", self.hf_token)


@lru_cache
def get_worker_settings() -> WorkerSettings:
    s = WorkerSettings()
    s.ensure_dirs()
    return s
