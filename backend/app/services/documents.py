"""Document upload, text extraction, requirement extraction and conflict detection."""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import openpyxl
import yaml
from docx import Document as DocxDocument
from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core import audit
from app.core.errors import AppError
from app.core.security import hash_bytes, redact
from app.models import Document, Project, User, new_id
from app.services.artifacts import store
from app.services.core import serialize_document

settings = get_settings()

ALLOWED_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".txt", ".md", ".markdown", ".xlsx", ".xlsm",
    ".csv", ".json", ".yaml", ".yml", ".tf", ".hcl",
}
TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".csv", ".json", ".yaml", ".yml", ".tf", ".hcl"}
MAX_TEXT_CHARS = 2_000_000

COMPONENT_PATTERNS: dict[str, tuple[str, ...]] = {
    "App Service": ("app service", "web app", "web application", "azure websites"),
    "Virtual Machines": ("virtual machine", " vm ", " vms", "iaas", "vmss", "scale set"),
    "Azure Kubernetes Service": ("aks", "kubernetes", "k8s"),
    "Azure SQL Database": ("azure sql", "sql database", "mssql", "managed instance"),
    "PostgreSQL": ("postgres", "postgresql"),
    "MySQL": ("mysql", "mariadb"),
    "Cosmos DB": ("cosmos", "documentdb"),
    "Storage Account": ("storage account", "blob storage", "blob", "file share"),
    "Key Vault": ("key vault", "keyvault"),
    "Virtual Network": ("vnet", "virtual network"),
    "Application Gateway": ("application gateway", "app gateway", "waf"),
    "Azure Front Door": ("front door", "frontdoor"),
    "Load Balancer": ("load balancer", " ilb", "internal lb"),
    "Log Analytics": ("log analytics", "azure monitor", "monitoring"),
    "Application Insights": ("application insights", "app insights"),
    "Service Bus": ("service bus", "message queue"),
    "Event Hubs": ("event hub", "eventhub"),
    "Azure Functions": ("azure function", "function app", "serverless"),
    "Azure Cache for Redis": ("redis", "cache"),
    "Azure Firewall": ("azure firewall", "firewall"),
    "Azure Bastion": ("bastion",),
    "Private Endpoint": ("private endpoint", "private link"),
    "Private DNS": ("private dns", "dns zone"),
    "VPN Gateway": ("vpn", "expressroute", "site-to-site"),
}

UNTRUSTED_INSTRUCTION_PATTERNS = [
    r"ignore (all )?(previous|prior|above) (instructions|prompts)",
    r"disregard (the )?(system|previous) (prompt|instructions)",
    r"you are now (a|an|the)",
    r"(approve|authorize) (the )?deployment",
    r"disable (the )?(security|policy|checkov|validation)",
    r"(skip|bypass) (the )?(approval|validation|policy)",
    r"exfiltrate|send (the )?(credentials|secrets|token)",
    r"run (the )?(command|shell|script)",
    r"reveal (the )?(system prompt|api key|secret)",
]


def _extension(filename: str) -> str:
    return Path(filename).suffix.lower()


def infer_document_type(filename: str) -> str:
    name = filename.lower()
    if any(k in name for k in ("architecture", "hld", "high-level", "design")):
        return "architecture"
    if any(k in name for k in ("network", "diagram", "topology")):
        return "network"
    if any(k in name for k in ("security", "compliance", "policy")):
        return "security"
    if any(k in name for k in ("inventory", "inventory-export", "resources")):
        return "inventory"
    if name.endswith((".tf", ".hcl")):
        return "terraform"
    if any(k in name for k in ("requirement", "spec", "scope")):
        return "requirements"
    return "other"


def extract_text(filename: str, data: bytes) -> tuple[str, dict[str, Any]]:
    ext = _extension(filename)
    if ext not in ALLOWED_EXTENSIONS:
        raise AppError(
            f"Unsupported file type '{ext or 'unknown'}'. Allowed: "
            + ", ".join(sorted(ALLOWED_EXTENSIONS))
        )
    meta: dict[str, Any] = {"pages": None, "sheets": [], "paragraphs": None}
    if ext in TEXT_EXTENSIONS:
        text = data.decode("utf-8-sig", errors="replace")
        meta["paragraphs"] = text.count("\n\n") + 1
        return text, meta
    if ext == ".pdf":
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as exc:  # noqa: BLE001
                raise AppError(f"Encrypted PDF cannot be read: {exc}") from exc
        pages = []
        for page in reader.pages:
            pages.append(page.extract_text() or "")
        meta["pages"] = len(reader.pages)
        return "\n\n".join(pages), meta
    if ext == ".docx":
        document = DocxDocument(io.BytesIO(data))
        parts = [p.text for p in document.paragraphs if p.text.strip()]
        for table in document.tables:
            for row in table.rows:
                parts.append(" | ".join(cell.text.strip() for cell in row.cells))
        meta["paragraphs"] = len(document.paragraphs)
        return "\n".join(parts), meta
    if ext in (".xlsx", ".xlsm"):
        workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        parts = []
        for sheet in workbook.worksheets:
            meta["sheets"].append(sheet.title)
            parts.append(f"# Sheet: {sheet.title}")
            for row in sheet.iter_rows(values_only=True):
                values = [str(v) for v in row if v is not None]
                if values:
                    parts.append(" | ".join(values))
        return "\n".join(parts), meta
    if ext == ".doc":
        raise AppError(
            "Legacy .doc files cannot be parsed safely. Convert the document to .docx or PDF and re-upload."
        )
    raise AppError(f"Unsupported file type: {ext}")


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?;])\s+|\n+", text)
    return [p.strip(" -•\t") for p in parts if p and 10 <= len(p.strip()) <= 500]


def analyze_document(text: str) -> dict[str, Any]:
    lowered = text.lower()
    components = sorted(
        name for name, needles in COMPONENT_PATTERNS.items() if any(n in lowered for n in needles)
    )
    requirements: list[str] = []
    constraints: list[str] = []
    dependencies: list[str] = []
    for sentence in _sentences(text):
        low = sentence.lower()
        if any(k in low for k in ("must ", "shall ", "required", "require ", "needs to", "should ")):
            requirements.append(sentence)
        if any(
            k in low
            for k in (
                "no public", "private", "compliance", "iso 27001", "pci", "hipaa", "gdpr",
                "tls", "encrypt", "retention", "rpo", "rto", "sla", "region", "budget",
            )
        ):
            constraints.append(sentence)
        if any(k in low for k in ("depends on", "requires", "integrates with", "connected to", "after ")):
            dependencies.append(sentence)

    missing: list[str] = []
    if not any(w in lowered for w in ("region", "uae", "east us", "west europe", "location")):
        missing.append("Target Azure region")
    if not any(w in lowered for w in ("production", "staging", "development", "dev", "environment")):
        missing.append("Target environment")
    if "budget" not in lowered and "cost" not in lowered:
        missing.append("Budget expectation")
    if not any(w in lowered for w in ("high availability", "zone", "sla", "99.")):
        missing.append("Availability requirement")
    if not any(w in lowered for w in ("private", "public", "vpn", "internet")):
        missing.append("Network exposure model")

    conflicts: list[str] = []
    if "public" in lowered and "private" in lowered:
        conflicts.append("The document mentions both public and private exposure; the intended posture is ambiguous.")
    if "windows" in lowered and "linux" in lowered:
        conflicts.append("Both Windows and Linux are referenced; the target platform is ambiguous.")
    if "single region" in lowered and "multi-region" in lowered:
        conflicts.append("Single-region and multi-region requirements conflict.")

    untrusted = any(re.search(pattern, lowered) for pattern in UNTRUSTED_INSTRUCTION_PATTERNS)
    word_count = len(re.findall(r"\w+", text))
    return {
        "components": components,
        "requirements": list(dict.fromkeys(requirements))[:60],
        "constraints": list(dict.fromkeys(constraints))[:60],
        "dependencies": list(dict.fromkeys(dependencies))[:30],
        "missing_information": missing,
        "conflicts": conflicts,
        "untrusted_instruction_detected": untrusted,
        "word_count": word_count,
        "character_count": len(text),
    }


def _safe_filename(filename: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._\- ]+", "_", filename).strip(" .")
    return (cleaned or "document")[:180]


def upload_document(
    db: Session,
    project: Project,
    user: User,
    *,
    filename: str,
    content_type: str,
    data: bytes,
) -> Document:
    if not data:
        raise AppError("Uploaded file is empty")
    if len(data) > settings.max_upload_bytes:
        raise AppError(
            f"File exceeds the {settings.max_upload_bytes // (1024 * 1024)} MB limit"
        )
    ext = _extension(filename)
    if ext not in ALLOWED_EXTENSIONS:
        raise AppError(
            f"Unsupported file type '{ext}'. Allowed: " + ", ".join(sorted(ALLOWED_EXTENSIONS))
        )
    document_id = new_id()
    safe_name = _safe_filename(filename)
    key = f"projects/{project.id}/documents/{document_id}/{safe_name}"
    saved = store.save(key, data)
    document = Document(
        id=document_id,
        project_id=project.id,
        filename=safe_name,
        content_type=content_type or "application/octet-stream",
        size_bytes=len(data),
        storage_location=str(saved["key"]),
        content_hash=hash_bytes(data),
        document_type=infer_document_type(safe_name),
        extraction_status="pending",
    )
    db.add(document)
    db.flush()
    audit.record(
        db,
        organization_id=project.organization_id,
        project_id=project.id,
        user_id=user.id,
        action="document.uploaded",
        metadata={"document_id": document.id, "filename": safe_name, "size": len(data)},
    )
    _extract_and_store(db, document)
    return document


def _extract_and_store(db: Session, document: Document) -> None:
    document.extraction_status = "extracting"
    try:
        data = store.load(document.storage_location)
        text, meta = extract_text(document.filename, data)
        if len(text) > MAX_TEXT_CHARS:
            text = text[:MAX_TEXT_CHARS] + "\n\n[truncated for storage]"
        analysis = analyze_document(text)
        analysis.update(meta)
        document.extracted_text = redact(text)
        document.extraction = analysis
        document.extraction_status = "extracted"
        document.extraction_error = None
    except (AppError, ValueError, OSError, KeyError) as exc:
        document.extraction_status = "failed"
        document.extraction_error = str(exc)[:2000]
        document.extraction = {"error": str(exc)[:500]}
    document.extraction_error = document.extraction_error


def reextract_document(db: Session, document: Document, user: User) -> Document:
    _extract_and_store(db, document)
    audit.record(
        db,
        organization_id=document.project.organization_id,
        project_id=document.project_id,
        user_id=user.id,
        action="document.extracted",
        metadata={
            "document_id": document.id,
            "status": document.extraction_status,
            "components": len((document.extraction or {}).get("components", [])),
        },
    )
    return document


def list_documents(db: Session, project_id: str) -> list[Document]:
    return list(
        db.scalars(
            select(Document).where(Document.project_id == project_id).order_by(Document.created_at)
        )
    )


def delete_document(db: Session, document: Document, user: User) -> None:
    project = db.get(Project, document.project_id)
    store.delete(document.storage_location)
    db.delete(document)
    if project:
        audit.record(
            db,
            organization_id=project.organization_id,
            project_id=project.id,
            user_id=user.id,
            action="document.deleted",
            metadata={"document_id": document.id, "filename": document.filename},
        )


def document_summary(document: Document) -> dict[str, Any]:
    payload = serialize_document(document)
    payload["extraction"] = document.extraction
    return payload
