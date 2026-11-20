"""Content-addressed artifact store (local filesystem; Azure Blob-ready interface)."""
from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import zipfile
from pathlib import Path

from app.config import get_settings
from app.core.errors import AppError

_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9._\-]+")


class ArtifactStore:
    def __init__(self, root: str | None = None) -> None:
        self.root = Path(root or get_settings().artifact_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    # -- path safety ------------------------------------------------------
    def _safe_key(self, key: str) -> str:
        parts = [p for p in key.replace("\\", "/").split("/") if p not in ("", ".")]
        if any(p == ".." for p in parts):
            raise AppError("Invalid artifact key")
        cleaned = []
        for part in parts:
            part = _SAFE_SEGMENT.sub("_", part).strip("._")
            if part:
                cleaned.append(part[:200])
        if not cleaned:
            raise AppError("Invalid artifact key")
        return "/".join(cleaned)

    def path(self, key: str) -> Path:
        safe = self._safe_key(key)
        target = (self.root / safe).resolve()
        if not str(target).startswith(str(self.root) + os.sep):
            raise AppError("Invalid artifact key")
        return target

    # -- operations -------------------------------------------------------
    def save(self, key: str, data: bytes) -> dict[str, str | int]:
        target = self.path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return {
            "key": self._safe_key(key),
            "sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
            "size": len(data),
        }

    def save_text(self, key: str, text: str) -> dict[str, str | int]:
        return self.save(key, text.encode("utf-8"))

    def load(self, key: str) -> bytes:
        target = self.path(key)
        if not target.exists():
            raise AppError(f"Artifact not found: {key}", code="artifact_not_found")
        return target.read_bytes()

    def load_text(self, key: str) -> str:
        return self.load(key).decode("utf-8", errors="replace")

    def exists(self, key: str) -> bool:
        return self.path(key).exists()

    def delete(self, key: str) -> None:
        target = self.path(key)
        if target.exists():
            target.unlink()

    def save_zip(self, key: str, files: dict[str, str | bytes]) -> dict[str, str | int]:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(files):
                archive.writestr(path, files[path])
        return self.save(key, buffer.getvalue())

    def materialize(self, key: str, destination: Path) -> Path:
        data = self.load(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        return destination

    def copy_into(self, source: Path, key: str) -> dict[str, str | int]:
        return self.save(key, source.read_bytes())


store = ArtifactStore()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def remove_tree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
