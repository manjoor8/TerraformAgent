"""Versioned structured-output contracts for every agent boundary."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Priority = Literal["mandatory", "optional", "assumption"]
RequirementType = Literal[
    "compute",
    "network",
    "database",
    "storage",
    "security",
    "availability",
    "cost",
    "governance",
    "integration",
    "other",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


# ---------------------------------------------------------------------------
# Requirement analyst
# ---------------------------------------------------------------------------
class RequirementItem(StrictModel):
    requirement_type: RequirementType = "other"
    description: str = Field(min_length=3, max_length=2000)
    priority: Priority = "mandatory"
    source: str = "conversation"


class ClarificationQuestion(StrictModel):
    id: str
    category: str = "general"
    question: str
    why_it_matters: str = ""
    options: list[str] = Field(default_factory=list)
    recommended: str | None = None
    blocking: bool = True


class RequirementAnalysis(StrictModel):
    summary: str
    requirements: list[RequirementItem] = Field(default_factory=list)
    questions: list[ClarificationQuestion] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    requirements_complete: bool = False
    readiness_score: int = Field(default=0, ge=0, le=100)

    @field_validator("readiness_score")
    @classmethod
    def _clamp(cls, v: int) -> int:
        return max(0, min(100, v))


# ---------------------------------------------------------------------------
# Architecture
# ---------------------------------------------------------------------------
class ArchitectureResource(StrictModel):
    id: str
    type: str
    name: str
    purpose: str = ""
    sku: str | None = None
    module: str = "compute"
    private: bool = False
    zone_redundant: bool = False
    depends_on: list[str] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)


class SubnetSpec(StrictModel):
    name: str
    cidr: str
    purpose: str = "general"
    delegation: str | None = None
    service_endpoints: list[str] = Field(default_factory=list)
    nsg: str | None = None


class NetworkSpec(StrictModel):
    id: str = "vnet"
    name: str
    address_space: list[str] = Field(default_factory=lambda: ["10.0.0.0/16"])
    subnets: list[SubnetSpec] = Field(default_factory=list)
    nsg_rules: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    nat_gateway: bool = False
    private_dns_zones: list[str] = Field(default_factory=list)
    route_tables: list[dict[str, Any]] = Field(default_factory=list)


class RbacAssignment(StrictModel):
    principal: str
    role: str
    scope: str


class SecuritySpec(StrictModel):
    managed_identity: bool = True
    key_vault: bool = True
    private_endpoints: bool = True
    diagnostics: bool = True
    encryption: str = "Azure-managed keys at rest, TLS 1.2+ in transit"
    rbac: list[RbacAssignment] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class AvailabilitySpec(StrictModel):
    zones: bool = False
    multi_region: bool = False
    backup: str = ""
    rpo: str = "24h"
    rto: str = "24h"
    dr_strategy: str = ""


class CostLine(StrictModel):
    service: str
    sku: str = ""
    quantity: float = 1
    unit: str = "month"
    hourly: float = 0
    monthly: float = 0
    source: str = "catalog"


class CostEstimate(StrictModel):
    currency: str = "USD"
    monthly_total: float = 0
    lines: list[CostLine] = Field(default_factory=list)
    disclaimer: str = (
        "Indicative estimate derived from public Azure retail prices, not a quotation. "
        "Actual cost depends on usage, region, agreements and discounts."
    )


class Recommendation(StrictModel):
    id: str
    category: Literal[
        "security", "performance", "availability", "cost", "governance", "operations"
    ] = "security"
    recommendation: str
    current_limitation: str = ""
    proposed_improvement: str = ""
    technical_impact: str = ""
    security_impact: str = ""
    availability_impact: str = ""
    cost_impact: str = "Neutral"
    mandatory: bool = False
    status: Literal["proposed", "accepted", "rejected"] = "proposed"


class ArchitectureDecisionSpec(StrictModel):
    decision: str
    rationale: str
    source_requirement: str | None = None
    alternates_considered: list[str] = Field(default_factory=list)


class ArchitectureSpec(StrictModel):
    summary: str = ""
    region: str = "eastus"
    environments: list[str] = Field(default_factory=lambda: ["dev"])
    resources: list[ArchitectureResource] = Field(default_factory=list)
    networks: list[NetworkSpec] = Field(default_factory=list)
    security: SecuritySpec = Field(default_factory=SecuritySpec)
    availability: AvailabilitySpec = Field(default_factory=AvailabilitySpec)
    cost: CostEstimate = Field(default_factory=CostEstimate)
    recommendations: list[Recommendation] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    decisions: list[ArchitectureDecisionSpec] = Field(default_factory=list)

    @field_validator("resources")
    @classmethod
    def _unique_resource_ids(cls, value: list[ArchitectureResource]) -> list[ArchitectureResource]:
        ids = [r.id for r in value]
        if len(ids) != len(set(ids)):
            raise ValueError("resource ids must be unique")
        return value


class ArchitectureReview(StrictModel):
    overall_assessment: str = ""
    score: int = Field(default=0, ge=0, le=100)
    recommendations: list[Recommendation] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Terraform / validation / plan / verification
# ---------------------------------------------------------------------------
class TerraformFileSpec(StrictModel):
    path: str
    content: str


class ResourceModuleMapping(StrictModel):
    resource: str
    terraform_type: str
    module: str
    file: str


class TerraformGenerationResult(StrictModel):
    files: list[TerraformFileSpec] = Field(default_factory=list)
    module_map: list[ResourceModuleMapping] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class TerraformFilePatch(StrictModel):
    path: str
    content: str


class TerraformRepairResult(StrictModel):
    summary: str = ""
    files: list[TerraformFilePatch] = Field(default_factory=list)


class PolicyFinding(StrictModel):
    rule_id: str
    title: str
    severity: Literal["critical", "high", "medium", "low", "info"] = "medium"
    message: str
    file: str | None = None
    line: int | None = None
    resource: str | None = None
    remediation: str = ""


class ValidationCheck(StrictModel):
    name: str
    status: Literal["passed", "failed", "skipped", "error"] = "passed"
    duration_ms: int = 0
    errors: int = 0
    warnings: int = 0
    details: str = ""
    findings: list[PolicyFinding] = Field(default_factory=list)


class ValidationReport(StrictModel):
    status: Literal["passed", "failed", "error"] = "passed"
    checks: list[ValidationCheck] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    correction_rounds: int = 0


class PlanResourceChange(StrictModel):
    address: str
    type: str
    action: str
    module: str = ""
    replacing: bool = False
    reason: str = ""


class DestructiveOperation(StrictModel):
    address: str
    type: str
    action: str
    severity: Literal["critical", "high", "medium", "low"] = "high"
    reason: str


class PlanSummary(StrictModel):
    add: int = 0
    change: int = 0
    destroy: int = 0
    replace: int = 0
    total: int = 0
    resources: list[PlanResourceChange] = Field(default_factory=list)
    destructive_operations: list[DestructiveOperation] = Field(default_factory=list)
    outputs: dict[str, Any] = Field(default_factory=dict)
    narrative: str = ""
    plan_hash: str = ""
    source_digest: str = ""
    terraform_version: str = ""
    stale: bool = False


class VerificationResource(StrictModel):
    address: str = ""
    resource_id: str = ""
    type: str = ""
    name: str = ""
    state: str = "expected"
    detail: str = ""


class VerificationReport(StrictModel):
    status: Literal["verified", "incomplete", "failed", "unsupported"] = "verified"
    checked: int = 0
    matched: int = 0
    missing: list[VerificationResource] = Field(default_factory=list)
    unexpected: list[VerificationResource] = Field(default_factory=list)
    resources: list[VerificationResource] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
