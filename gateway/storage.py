"""Local filesystem storage with future S3/R2-ready interface."""

from __future__ import annotations

import hashlib
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import BinaryIO

from gateway.config import Settings, get_settings


class StorageBackend(ABC):
    @abstractmethod
    def put_file(self, key: str, src: Path | BinaryIO, *, category: str = "outputs") -> str:
        ...

    @abstractmethod
    def get_path(self, key: str, *, category: str = "outputs") -> Path:
        ...

    @abstractmethod
    def exists(self, key: str, *, category: str = "outputs") -> bool:
        ...

    @abstractmethod
    def delete(self, key: str, *, category: str = "outputs") -> None:
        ...

    @abstractmethod
    def job_dir(self, job_id: str) -> Path:
        ...

    @abstractmethod
    def temp_dir(self, job_id: str) -> Path:
        ...


class LocalStorage(StorageBackend):
    """Filesystem storage under STORAGE_PATH. Never requires S3."""

    CATEGORIES = ("jobs", "outputs", "thumbnails", "temp")

    def __init__(self, root: Path | None = None) -> None:
        settings = get_settings()
        self.root = Path(root or settings.storage_path)
        for cat in self.CATEGORIES:
            (self.root / cat).mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str, category: str) -> Path:
        if category not in self.CATEGORIES:
            raise ValueError(f"unknown storage category: {category}")
        safe = key.lstrip("/").replace("..", "")
        path = (self.root / category / safe).resolve()
        if not str(path).startswith(str((self.root / category).resolve())):
            raise ValueError("path escape blocked")
        return path

    def put_file(self, key: str, src: Path | BinaryIO, *, category: str = "outputs") -> str:
        dest = self._resolve(key, category)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(src, Path):
            shutil.copy2(src, dest)
        else:
            with open(dest, "wb") as f:
                shutil.copyfileobj(src, f)
        return str(dest)

    def get_path(self, key: str, *, category: str = "outputs") -> Path:
        return self._resolve(key, category)

    def exists(self, key: str, *, category: str = "outputs") -> bool:
        return self._resolve(key, category).exists()

    def delete(self, key: str, *, category: str = "outputs") -> None:
        path = self._resolve(key, category)
        if path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path, ignore_errors=True)

    def job_dir(self, job_id: str) -> Path:
        path = self.root / "jobs" / job_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def temp_dir(self, job_id: str) -> Path:
        path = self.root / "temp" / job_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def output_key(self, job_id: str, filename: str = "final.mp4") -> str:
        return f"{job_id}/{filename}"

    def file_hash(self, path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()


def get_storage(settings: Settings | None = None) -> LocalStorage:
    settings = settings or get_settings()
    return LocalStorage(settings.storage_path)
