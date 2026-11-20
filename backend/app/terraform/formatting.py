"""Format generated HCL with `terraform fmt` before persisting it."""
from __future__ import annotations

from pathlib import Path

from app.terraform.executor import TerraformExecutor, TerraformNotInstalled
from app.terraform.workspace import temporary_workspace

FORMATTABLE_SUFFIXES = (".tf", ".tfvars")


def format_files(files: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """Return files with `.tf`/`.tfvars` formatted by terraform fmt.

    Non-HCL files are preserved byte-for-byte. If Terraform is unavailable the
    files are returned unchanged and a note explains why.
    """
    notes: list[str] = []
    if not any(path.endswith(FORMATTABLE_SUFFIXES) for path in files):
        return files, notes
    try:
        executor = TerraformExecutor()
    except TerraformNotInstalled:
        return files, ["Terraform CLI unavailable; generated HCL was not auto-formatted."]

    try:
        with temporary_workspace(files) as root:
            result = executor.run(["fmt", "-recursive", "."], cwd=root, timeout=120)
            if not result.ok:
                notes.append(f"terraform fmt failed: {result.output[:500]}")
                return files, notes
            formatted = dict(files)
            changed = 0
            for relative in files:
                if not relative.endswith(FORMATTABLE_SUFFIXES):
                    continue
                target = root / Path(relative.replace("\\", "/"))
                if target.exists():
                    content = target.read_text(encoding="utf-8")
                    if content != files[relative]:
                        changed += 1
                    formatted[relative] = content
            if changed:
                notes.append(f"terraform fmt normalized {changed} file(s).")
            return formatted, notes
    except OSError as exc:
        notes.append(f"terraform fmt could not run: {exc}")
        return files, notes
