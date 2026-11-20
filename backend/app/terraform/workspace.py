"""Safe materialization of DB-stored Terraform files into a run workspace."""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from app.config import get_settings
from app.core.errors import AppError

settings = get_settings()


def _safe_relative(path: str) -> Path:
    cleaned = path.replace("\\", "/").lstrip("/")
    relative = Path(cleaned)
    if relative.is_absolute() or any(part == ".." for part in relative.parts):
        raise AppError(f"Unsafe file path in Terraform project: {path}")
    return relative


def write_files(files: dict[str, str], root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for path, content in files.items():
        relative = _safe_relative(path)
        target = (root / relative).resolve()
        if not str(target).startswith(str(root.resolve())):
            raise AppError(f"Unsafe file path in Terraform project: {path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def materialize(files: dict[str, str], *, project_id: str, run_id: str) -> Path:
    root = Path(settings.workspace_root) / project_id / run_id
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    write_files(files, root)
    return root


def cleanup(root: Path) -> None:
    shutil.rmtree(root, ignore_errors=True)


class temporary_workspace:
    def __init__(self, files: dict[str, str]) -> None:
        self.files = files
        self._tmp: tempfile.TemporaryDirectory[str] | None = None
        self.root: Path | None = None

    def __enter__(self) -> Path:
        self._tmp = tempfile.TemporaryDirectory(prefix="tfagent-")
        self.root = Path(self._tmp.name)
        write_files(self.files, self.root)
        return self.root

    def __exit__(self, *exc: object) -> None:
        if self._tmp:
            self._tmp.cleanup()
