"""Terraform process execution with a strict environment allow-list."""
from __future__ import annotations

import os
import queue
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import BASE_DIR, get_settings
from app.core.security import redact

settings = get_settings()

_ALLOWED_ENV_EXACT = {
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "COMSPEC",
    "TEMP",
    "TMP",
    "TMPDIR",
    "HOME",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "LANG",
    "LC_ALL",
    "TERM",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "TF_PLUGIN_CACHE_DIR",
    "TF_LOG",
    "TF_LOG_PATH",
    "TF_WORKSPACE",
    "TF_DATA_DIR",
}
_ALLOWED_ENV_PREFIXES = ("ARM_", "TF_VAR_", "AZURE_", "ACTIONS_ID_TOKEN_", "SYSTEM_")


@dataclass
class CommandResult:
    args: list[str]
    exit_code: int
    output: str
    duration_ms: int
    timed_out: bool = False
    cancelled: bool = False
    lines: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and not self.cancelled


class TerraformNotInstalled(RuntimeError):
    pass


def find_terraform_binary() -> str:
    configured = settings.terraform_binary
    if configured and configured not in ("terraform", "terraform.exe"):
        path = Path(configured)
        if path.exists():
            return str(path)
    discovered = shutil.which(configured) if configured else None
    if discovered:
        return discovered
    for relative in (
        "tools/terraform-amd64/terraform.exe",
        "tools/terraform/terraform.exe",
        "tools/terraform/terraform",
    ):
        candidate = BASE_DIR.parent / relative
        if candidate.exists():
            return str(candidate)
    if shutil.which("terraform"):
        return "terraform"
    raise TerraformNotInstalled(
        "Terraform CLI was not found. Install Terraform >= 1.6 and set TERRAFORM_BINARY."
    )


def terraform_version() -> str | None:
    try:
        binary = find_terraform_binary()
    except TerraformNotInstalled:
        return None
    try:
        result = subprocess.run(
            [binary, "version", "-json"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if result.returncode == 0:
            import json

            data = json.loads(result.stdout)
            return data.get("terraform_version")
        first = (result.stdout or result.stderr).splitlines()
        return first[0] if first else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def build_environment(extra: dict[str, str] | None = None) -> dict[str, str]:
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        if key in _ALLOWED_ENV_EXACT or key.upper().startswith(_ALLOWED_ENV_PREFIXES):
            env[key] = value
    env["TF_IN_AUTOMATION"] = "1"
    env["TF_INPUT"] = "0"
    env["CHECKPOINT_DISABLE"] = "1"
    env["TF_PLUGIN_CACHE_DIR"] = settings.terraform_plugin_cache_dir
    Path(settings.terraform_plugin_cache_dir).mkdir(parents=True, exist_ok=True)
    for key in list(env):
        if key.startswith("TF_CLI_ARGS"):
            env.pop(key, None)
    if extra:
        env.update(extra)
    return env


class TerraformExecutor:
    def __init__(self, binary: str | None = None) -> None:
        self.binary = binary or find_terraform_binary()

    def run(
        self,
        args: list[str],
        *,
        cwd: str | Path,
        timeout: int | None = None,
        extra_env: dict[str, str] | None = None,
        on_line: Callable[[str], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> CommandResult:
        command = [self.binary, *args]
        started = time.perf_counter()
        timeout = timeout or settings.terraform_timeout_seconds
        creationflags = 0
        if os.name == "nt":
            creationflags = 0x08000000  # CREATE_NO_WINDOW

        try:
            process = subprocess.Popen(
                command,
                cwd=str(cwd),
                env=build_environment(extra_env),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )
        except OSError as exc:
            raise TerraformNotInstalled(f"Could not start Terraform: {exc}") from exc

        output_queue: queue.Queue[str | None] = queue.Queue()

        def reader() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                output_queue.put(line.rstrip("\r\n"))
            output_queue.put(None)

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()

        lines: list[str] = []
        timed_out = False
        cancelled = False
        deadline = started + timeout
        while True:
            try:
                item = output_queue.get(timeout=0.2)
            except queue.Empty:
                item = ""
            if item is None:
                break
            if item:
                safe = redact(item)
                lines.append(safe)
                if on_line:
                    try:
                        on_line(safe)
                    except Exception:  # noqa: BLE001 - stream callbacks must not kill execution
                        pass
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                process.kill()
                break
            if time.perf_counter() > deadline:
                timed_out = True
                process.kill()
                break

        try:
            exit_code = process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            exit_code = -9
        thread.join(timeout=2)

        # Drain any remaining lines after process exit.
        while True:
            try:
                leftover = output_queue.get_nowait()
            except queue.Empty:
                break
            if leftover:
                safe = redact(leftover)
                lines.append(safe)

        return CommandResult(
            args=args,
            exit_code=exit_code,
            output="\n".join(lines),
            duration_ms=int((time.perf_counter() - started) * 1000),
            timed_out=timed_out,
            cancelled=cancelled,
            lines=lines,
        )


def executor_info() -> dict[str, Any]:
    version = terraform_version()
    return {
        "terraform_version": version,
        "available": version is not None,
        "binary": (find_terraform_binary() if version else None),
    }
