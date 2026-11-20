"""End-to-end Terraform validation pipeline with deterministic policy gating."""
from __future__ import annotations

import json
import re
import shutil
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from app.agents.schemas import PolicyFinding, ValidationCheck, ValidationReport
from app.config import get_settings
from app.terraform import policy
from app.terraform.executor import (
    CommandResult,
    TerraformExecutor,
    TerraformNotInstalled,
)
from app.terraform.workspace import temporary_workspace

settings = get_settings()


@dataclass
class ValidationOutcome:
    report: ValidationReport
    log: str
    policy_findings: list[PolicyFinding] = field(default_factory=list)
    files_count: int = 0
    modules: int = 0


class ValidationEngine:
    def __init__(self, executor: TerraformExecutor | None = None) -> None:
        self.executor = executor

    def _executor(self) -> TerraformExecutor:
        if self.executor is None:
            self.executor = TerraformExecutor()
        return self.executor

    def validate(
        self,
        files: dict[str, str],
        *,
        environment: str,
        disabled_rules: list[str] | None = None,
        on_event: Callable[[str, dict], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> ValidationOutcome:
        report = ValidationReport(status="passed")
        log_lines: list[str] = []
        checks: list[ValidationCheck] = []

        def emit(kind: str, payload: dict) -> None:
            if on_event:
                try:
                    on_event(kind, payload)
                except Exception:  # noqa: BLE001
                    pass

        def add_log(line: str) -> None:
            log_lines.append(line)
            emit("log", {"source": "validation", "line": line})

        env_dir = f"environments/{environment}"
        modules = len({p.split("/")[1] for p in files if p.startswith("modules/") and p.count("/") >= 2})

        try:
            executor = self._executor()
        except TerraformNotInstalled as exc:
            report.status = "error"
            report.checks = [
                ValidationCheck(name="terraform_cli", status="error", details=str(exc))
            ]
            report.summary = {"files": len(files), "modules": modules, "errors": 1, "warnings": 0}
            return ValidationOutcome(
                report=report,
                log="\n".join(log_lines + [str(exc)]),
                files_count=len(files),
                modules=modules,
            )

        with temporary_workspace(files) as root:
            workdir = root / env_dir if (root / env_dir).exists() else root

            # 1. formatting -------------------------------------------------
            emit("validation", {"check": "terraform_fmt", "status": "running"})
            fmt_result = executor.run(
                ["fmt", "-check", "-recursive", "."],
                cwd=root,
                timeout=120,
                on_line=add_log,
                cancel_event=cancel_event,
            )
            unformatted = [
                line.strip()
                for line in fmt_result.output.splitlines()
                if line.strip() and not line.startswith("Error")
            ]
            checks.append(
                ValidationCheck(
                    name="terraform_fmt",
                    status="passed" if fmt_result.ok else "failed",
                    duration_ms=fmt_result.duration_ms,
                    errors=0 if fmt_result.ok else len(unformatted) or 1,
                    details=(
                        "All files are formatted."
                        if fmt_result.ok
                        else "Unformatted files: " + ", ".join(unformatted[:20])
                    ),
                )
            )

            # 2. init (backend disabled) ------------------------------------
            emit("validation", {"check": "terraform_init", "status": "running"})
            init_result = executor.run(
                ["init", "-backend=false", "-input=false", "-no-color"],
                cwd=workdir,
                timeout=300,
                on_line=add_log,
                cancel_event=cancel_event,
            )
            checks.append(
                ValidationCheck(
                    name="terraform_init",
                    status="passed" if init_result.ok else "failed",
                    duration_ms=init_result.duration_ms,
                    errors=0 if init_result.ok else 1,
                    details=_tail(init_result.output),
                )
            )

            validate_result: CommandResult | None = None
            # 3. validate ---------------------------------------------------
            if init_result.ok:
                emit("validation", {"check": "terraform_validate", "status": "running"})
                validate_result = executor.run(
                    ["validate", "-json", "-no-color"],
                    cwd=workdir,
                    timeout=180,
                    on_line=add_log,
                    cancel_event=cancel_event,
                )
                parsed = _parse_validate_json(validate_result.output)
                checks.append(
                    ValidationCheck(
                        name="terraform_validate",
                        status="passed" if validate_result.ok and parsed["valid"] else "failed",
                        duration_ms=validate_result.duration_ms,
                        errors=parsed["errors"],
                        warnings=parsed["warnings"],
                        details=parsed["details"],
                    )
                )
            else:
                checks.append(
                    ValidationCheck(
                        name="terraform_validate",
                        status="skipped",
                        details="Skipped because terraform init failed.",
                    )
                )

            # 4. security policy -------------------------------------------
            emit("validation", {"check": "policy", "status": "running"})
            policy_result = policy.evaluate_policy(
                files, environment=environment, disabled_rules=disabled_rules
            )
            checks.append(
                ValidationCheck(
                    name="policy",
                    status=policy_result.status,
                    errors=sum(1 for f in policy_result.findings if f.severity in ("critical", "high")),
                    warnings=sum(1 for f in policy_result.findings if f.severity in ("medium", "low")),
                    details=f"{len(policy_result.findings)} finding(s) across {policy_result.checked_files} file(s).",
                    findings=policy_result.findings,
                )
            )

            # 5. secret scan (explicit, separate check) --------------------
            secret_findings = policy.scan_secrets(files)
            checks.append(
                ValidationCheck(
                    name="secret_scan",
                    status="failed" if secret_findings else "passed",
                    errors=len(secret_findings),
                    details=(
                        f"{len(secret_findings)} potential secret(s) found."
                        if secret_findings
                        else "No hardcoded credentials detected."
                    ),
                    findings=secret_findings,
                )
            )

            # 6. provider/dependency check --------------------------------
            provider_details, provider_ok = _provider_check(root, workdir)
            checks.append(
                ValidationCheck(
                    name="provider_versions",
                    status="passed" if provider_ok else "failed",
                    details=provider_details,
                )
            )

            # 7. optional external scanners --------------------------------
            checks.extend(_external_scanners(root, add_log, cancel_event))

        report.checks = checks
        failed = [c for c in checks if c.status in ("failed", "error")]
        report.status = "failed" if failed else "passed"
        report.summary = {
            "files": len(files),
            "modules": modules,
            "errors": sum(c.errors for c in checks),
            "warnings": sum(c.warnings for c in checks),
            "critical": sum(
                1
                for c in checks
                for f in c.findings
                if f.severity == "critical"
            ),
            "failed_checks": [c.name for c in failed],
        }
        log_lines.append(f"Validation {report.status.upper()}: " + json.dumps(report.summary))
        emit("validation", {"check": "complete", "status": report.status, "summary": report.summary})
        return ValidationOutcome(
            report=report,
            log="\n".join(log_lines),
            policy_findings=[f for c in checks for f in c.findings],
            files_count=len(files),
            modules=modules,
        )


def _tail(text: str, lines: int = 25) -> str:
    parts = [p for p in text.splitlines() if p.strip()]
    return "\n".join(parts[-lines:]) if parts else "No output."


def _parse_validate_json(output: str) -> dict:
    start = output.find("{")
    end = output.rfind("}")
    if start == -1 or end == -1:
        return {"valid": False, "errors": 1, "warnings": 0, "details": _tail(output)}
    try:
        data = json.loads(output[start : end + 1])
    except json.JSONDecodeError:
        return {"valid": False, "errors": 1, "warnings": 0, "details": _tail(output)}
    diagnostics = data.get("diagnostics") or []
    details = []
    for diagnostic in diagnostics[:10]:
        summary = diagnostic.get("summary", "")
        detail = diagnostic.get("detail", "")
        severity = diagnostic.get("severity", "error")
        details.append(f"[{severity}] {summary}: {detail}"[:600])
    return {
        "valid": bool(data.get("valid")),
        "errors": int(data.get("error_count", 0)),
        "warnings": int(data.get("warning_count", 0)),
        "details": "\n".join(details) if details else "Configuration is valid.",
    }


def _provider_check(root: Path, workdir: Path) -> tuple[str, bool]:
    lock = workdir / ".terraform.lock.hcl"
    if not lock.exists():
        lock = root / ".terraform.lock.hcl"
    if not lock.exists():
        return "No provider lock file was produced.", False
    content = lock.read_text(encoding="utf-8", errors="replace")
    match = re.search(r'provider\s+"registry\.terraform\.io/hashicorp/azurerm"\s*\{[^}]*version\s*=\s*"([^"]+)"', content, re.S)
    if not match:
        return "azurerm provider was not recorded in the lock file.", False
    version = match.group(1)
    details = f"azurerm provider selected: {version} (pinned by project constraint)."
    return details, True


def _external_scanners(
    root: Path,
    add_log: Callable[[str], None],
    cancel_event: threading.Event | None,
) -> list[ValidationCheck]:
    checks: list[ValidationCheck] = []
    for name, binary, args in (
        ("tflint", settings.tflint_binary, ["--format", "compact", "--recursive"]),
        ("checkov", settings.checkov_binary, ["-d", ".", "--framework", "terraform", "--quiet", "--output", "json"]),
    ):
        path = shutil.which(binary)
        if not path:
            checks.append(
                ValidationCheck(
                    name=name,
                    status="skipped",
                    details=f"{name} is not installed on this worker. Built-in policy checks ran instead.",
                )
            )
            continue
        add_log(f"Running {name}...")
        try:
            executor = TerraformExecutor(binary=path)
            result = executor.run(args, cwd=root, timeout=300, cancel_event=cancel_event, on_line=add_log)
            checks.append(
                ValidationCheck(
                    name=name,
                    status="passed" if result.ok else "failed",
                    duration_ms=result.duration_ms,
                    errors=0 if result.ok else 1,
                    details=_tail(result.output, 15),
                )
            )
        except Exception as exc:  # noqa: BLE001
            checks.append(ValidationCheck(name=name, status="error", details=str(exc)))
    return checks
