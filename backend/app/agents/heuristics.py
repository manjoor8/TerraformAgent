"""Deterministic Azure Solution Architect heuristics.

These run when no LLM is configured (and as the grounding/validation baseline when
one is). They are intentionally rule-based and auditable: every decision is
derived from explicit requirement text.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from app.agents import catalog
from app.agents.schemas import (
    ArchitectureDecisionSpec,
    ArchitectureResource,
    ArchitectureReview,
    ArchitectureSpec,
    AvailabilitySpec,
    ClarificationQuestion,
    CostEstimate,
    CostLine,
    NetworkSpec,
    Recommendation,
    RequirementAnalysis,
    RequirementItem,
    SecuritySpec,
    SubnetSpec,
)

CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "compute": [
        "app service", "virtual machine", "vm", "kubernetes", "aks", "container",
        "server", "autoscal", "instance", "linux", "windows", "sku", "node pool",
    ],
    "network": [
        "vnet", "virtual network", "subnet", "private", "public", "load balancer",
        "application gateway", "front door", "firewall", "dns", "vpn", "expressroute",
        "network security group", "nsg", "ip address", "https", "nat",
    ],
    "database": [
        "database", "azure sql", "sql server", "mssql", "postgres", "mysql",
        "cosmos", "backup retention", "zone redundancy", "managed instance",
    ],
    "storage": ["storage account", "blob", "file share", "disk", "object storage"],
    "security": [
        "key vault", "managed identity", "rbac", "compliance", "encrypt", "defender",
        "diagnostic", "secret", "certificate", "private endpoint", "waf",
    ],
    "availability": [
        "high availability", "availability zone", "disaster recovery", "rpo", "rto",
        "multi-region", "multi region", "sla", "99.9", "failover", "redundan",
    ],
    "cost": ["budget", "cost", "cheap", "optimiz", "reserved", "savings", "pricing"],
    "governance": ["tag", "policy", "naming", "environment", "separation", "lock", "landing zone"],
    "integration": ["api", "integration", "message", "service bus", "event hub", "queue"],
}

PLATFORM_VM_WORDS = (
    "virtual machine", " vms", "vm ", "application server", "app server", "servers",
    "windows server", "linux server",
)
PLATFORM_AKS_WORDS = ("aks", "kubernetes", "k8s", "container orchestration")
PLATFORM_APPSERVICE_WORDS = ("app service", "web app", "web application", "website", "paas")

DB_ENGINE_PATTERNS = {
    "postgres": ("postgres", "postgresql"),
    "mysql": ("mysql", "mariadb"),
    "mssql": ("azure sql", "sql database", "sql server", "mssql", "managed instance", " sql "),
}

DEPLOY_KEYWORDS = (
    "deploy", "design", "architect", "build", "create", "provision", "generate",
    "review", "recommend", "plan", "estimate", "cost", "improve", "change",
)

INFRA_KEYWORDS = tuple(
    dict.fromkeys(
        [kw for kws in CATEGORY_KEYWORDS.values() for kw in kws]
        + list(PLATFORM_VM_WORDS)
        + list(PLATFORM_AKS_WORDS)
        + list(PLATFORM_APPSERVICE_WORDS)
        + ["azure", "cloud", "infrastructure", "terraform", "environment"]
    )
)


@dataclass
class AgentContext:
    project_name: str
    description: str = ""
    environment: str = "dev"
    region: str = "eastus"
    messages: list[str] = field(default_factory=list)
    requirements: list[dict[str, Any]] = field(default_factory=list)
    documents: list[dict[str, Any]] = field(default_factory=list)
    answered_questions: list[dict[str, Any]] = field(default_factory=list)
    feedback: str | None = None
    existing_architecture: dict[str, Any] | None = None

    def text(self, include_feedback: bool = True) -> str:
        parts = [self.description, self.project_name]
        parts.extend(self.messages)
        parts.extend(r.get("description", "") for r in self.requirements)
        for doc in self.documents:
            extraction = doc.get("extraction") or {}
            parts.extend(extraction.get("requirements", []) or [])
            parts.extend(extraction.get("components", []) or [])
            parts.extend(extraction.get("constraints", []) or [])
            parts.append(doc.get("filename", ""))
        for qa in self.answered_questions:
            parts.append(str(qa.get("question", "")))
            parts.append(str(qa.get("answer", "")))
        if include_feedback and self.feedback:
            parts.append(self.feedback)
        return "\n".join(p for p in parts if p).lower()


# ---------------------------------------------------------------------------
# Requirement analysis
# ---------------------------------------------------------------------------
def classify_requirement(text: str) -> str:
    lowered = text.lower()
    scores = {cat: sum(1 for kw in kws if kw in lowered) for cat, kws in CATEGORY_KEYWORDS.items()}
    best = max(scores, key=lambda c: scores[c])
    return best if scores[best] > 0 else "other"


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?;])\s+|\n+|(?<=,)\s+(?=(?:and|with|it|the)\b)", text)
    return [p.strip(" -•\t") for p in parts if p and 8 <= len(p.strip()) <= 600]


def extract_requirements(ctx: AgentContext) -> list[RequirementItem]:
    seen = {r.get("description", "").strip().lower() for r in ctx.requirements}
    items: list[RequirementItem] = []
    for message in ctx.messages:
        for sentence in _sentences(message):
            lowered = sentence.lower()
            if not any(kw in lowered for kw in INFRA_KEYWORDS):
                continue
            if not any(kw in lowered for kw in DEPLOY_KEYWORDS) and len(sentence.split()) < 5:
                continue
            key = sentence.strip().lower()
            if key in seen:
                continue
            seen.add(key)
            items.append(
                RequirementItem(
                    requirement_type=classify_requirement(sentence),  # type: ignore[arg-type]
                    description=sentence.strip(),
                    priority="mandatory",
                    source="conversation",
                )
            )
    for doc in ctx.documents:
        extraction = doc.get("extraction") or {}
        for statement in extraction.get("requirements", []) or []:
            key = str(statement).strip().lower()
            if key and key not in seen:
                seen.add(key)
                items.append(
                    RequirementItem(
                        requirement_type=classify_requirement(str(statement)),  # type: ignore[arg-type]
                        description=str(statement)[:2000],
                        priority="mandatory",
                        source=f"document:{doc.get('filename', 'upload')}",
                    )
                )
    return items[:60]


def _detect_platform(text: str) -> str | None:
    if any(w in text for w in PLATFORM_AKS_WORDS):
        return "aks"
    if any(w in text for w in PLATFORM_APPSERVICE_WORDS) and not any(
        w in text for w in ("virtual machine", "application server")
    ):
        return "app_service"
    if any(w in text for w in PLATFORM_VM_WORDS):
        return "vm"
    if "web" in text or "http" in text:
        return "app_service"
    return None


def _detect_db_engine(text: str) -> str | None:
    padded = f" {text} "
    for engine, needles in DB_ENGINE_PATTERNS.items():
        if any(n in padded for n in needles):
            return engine
    if "database" in text or "data store" in text:
        return "mssql"
    return None


def _detect_region(text: str) -> str | None:
    for alias, region in catalog.REGION_ALIASES.items():
        if alias in text:
            return region
    return None


def _detect_environment(text: str) -> str | None:
    if "production" in text or "prod " in text:
        return "production"
    if "staging" in text:
        return "staging"
    if "development" in text or " dev " in text:
        return "dev"
    return None


def _detect_budget(text: str) -> float | None:
    match = re.search(r"(?:budget|cost|spend|usd|\$)\D{0,12}(\d[\d,]{1,9})", text)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


def _wants_private(text: str) -> bool:
    private = any(
        phrase in text
        for phrase in ("private", "no public", "internal only", "not exposed", "internal network")
    )
    public = any(
        phrase in text
        for phrase in (
            "public internet",
            "internet-facing",
            "internet facing",
            "publicly accessible",
            "exposed to the internet",
            "public ip",
            "public endpoint",
        )
    )
    if private and not public:
        return True
    if public and not private:
        return False
    return "production" in text or "prod " in text


def _wants_ha(text: str) -> bool:
    return any(
        phrase in text
        for phrase in (
            "high availability", "highly available", "availability zone", "zone redundant",
            "zone-redundant", "ha ", "sla", "failover", "99.9", "99.95", "99.99",
        )
    )


def _wants_multiregion(text: str) -> bool:
    return any(
        phrase in text
        for phrase in ("multi-region", "multi region", "multiple regions", "global", "disaster recovery")
    )


def _count_instances(text: str) -> int:
    patterns = (
        r"(\d+)\s+(?:application\s+|web\s+|app\s+)?(?:servers?|vms?|virtual machines?|instances?|nodes?)",
        r"(?:two|three|four|five)\s+(?:application\s+|web\s+)?(?:servers?|vms?|instances?)",
    )
    words = {"two": 2, "three": 3, "four": 4, "five": 5}
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            value = match.group(1)
            if value.isdigit():
                return max(1, min(20, int(value)))
            return words.get(value, 2)
    return 2


def _wants_oses(text: str) -> str:
    if "windows" in text:
        return "windows"
    return "linux"


def _answered_text(ctx: AgentContext) -> str:
    return "\n".join(
        f"{qa.get('question', '')} {qa.get('answer', '')}" for qa in ctx.answered_questions
    ).lower()


def _questions_for(ctx: AgentContext, text: str) -> list[ClarificationQuestion]:
    answered = _answered_text(ctx)
    combined = f"{text}\n{answered}"
    questions: list[ClarificationQuestion] = []

    platform = _detect_platform(combined)
    if platform is None:
        questions.append(
            ClarificationQuestion(
                id="q-compute-platform",
                category="application architecture",
                question="How should the application be hosted in Azure?",
                why_it_matters="The hosting model determines the subnet design, scaling model, patching responsibility and a large part of the monthly cost.",
                options=["Azure App Service (recommended for web apps)", "Virtual Machines", "Azure Kubernetes Service"],
                recommended="Azure App Service (recommended for web apps)",
                blocking=True,
            )
        )
    elif platform == "vm" and "windows" not in combined and "linux" not in combined:
        questions.append(
            ClarificationQuestion(
                id="q-os",
                category="application architecture",
                question="Should the virtual machines run Windows or Linux?",
                why_it_matters="The OS choice changes the image, licensing cost, patching and hardening baseline.",
                options=["Linux (recommended)", "Windows"],
                recommended="Linux (recommended)",
                blocking=True,
            )
        )

    if _detect_region(combined) is None:
        questions.append(
            ClarificationQuestion(
                id="q-region",
                category="availability",
                question="Which Azure region should host the infrastructure?",
                why_it_matters="Region affects latency, data residency, service availability and price.",
                options=["UAE North", "East US", "West Europe", "UK South"],
                recommended=catalog.region_label(ctx.region),
                blocking=True,
            )
        )

    if _detect_db_engine(combined) is not None and "sql" not in combined and "postgres" not in combined and "mysql" not in combined:
        questions.append(
            ClarificationQuestion(
                id="q-database-engine",
                category="database",
                question="Which database engine should be used?",
                why_it_matters="Engine selection drives the Terraform resource type, HA options and licensing cost.",
                options=["Azure SQL Database (recommended)", "PostgreSQL Flexible Server", "MySQL Flexible Server"],
                recommended="Azure SQL Database (recommended)",
                blocking=True,
            )
        )

    if not any(w in combined for w in ("private", "public", "internet", "vpn", "expressroute")):
        questions.append(
            ClarificationQuestion(
                id="q-ingress",
                category="networking",
                question="Should the workload be reachable from the public internet or only from private networks?",
                why_it_matters="This decides whether we deploy a public ingress (Application Gateway / App Service) or private endpoints and private DNS only.",
                options=["Private only (recommended for production)", "Public HTTPS", "Both"],
                recommended="Private only (recommended for production)",
                blocking=True,
            )
        )

    if "budget" not in combined and "cost" not in combined and _detect_budget(combined) is None:
        questions.append(
            ClarificationQuestion(
                id="q-budget",
                category="cost",
                question="What is the approximate monthly budget for this environment?",
                why_it_matters="The budget determines the SKU and availability trade-offs we should propose.",
                options=["Under $500/month", "$500–$2,000/month", "Above $2,000/month", "No fixed budget"],
                recommended="$500–$2,000/month",
                blocking=False,
            )
        )

    if _wants_ha(combined) and not any(w in combined for w in ("rpo", "rto", "backup retention")):
        questions.append(
            ClarificationQuestion(
                id="q-rpo-rto",
                category="availability",
                question="What recovery targets apply (RPO/RTO) and how long must backups be retained?",
                why_it_matters="RPO/RTO decide zone redundancy, geo-redundant backup and the DR strategy (and its cost).",
                options=["RPO 1h / RTO 4h (recommended)", "RPO 15m / RTO 1h", "RPO 24h / RTO 24h"],
                recommended="RPO 1h / RTO 4h (recommended)",
                blocking=False,
            )
        )

    if not any(w in combined for w in ("existing vnet", "existing network", "hub", "peering", "vpn", "expressroute")):
        questions.append(
            ClarificationQuestion(
                id="q-existing-network",
                category="networking",
                question="Is there an existing VNet or hub network this workload must integrate with?",
                why_it_matters="Existing address space and peering constraints can make a proposed CIDR unusable or require a different subnet plan.",
                options=["No, create a new VNet (recommended)", "Yes, integrate with an existing hub"],
                recommended="No, create a new VNet (recommended)",
                blocking=False,
            )
        )

    if "compliance" not in combined and "regulat" not in combined and "pci" not in combined and "hipaa" not in combined:
        questions.append(
            ClarificationQuestion(
                id="q-compliance",
                category="security",
                question="Are there specific compliance or data-residency requirements?",
                why_it_matters="Compliance obligations can mandate private networking, customer-managed keys, region restrictions and longer log retention.",
                options=["None specific", "ISO 27001", "PCI DSS", "HIPAA"],
                recommended="None specific",
                blocking=False,
            )
        )

    blocking = [q for q in questions if q.blocking]
    non_blocking = [q for q in questions if not q.blocking]
    return (blocking[:3] + non_blocking[:2])[:4]


def analyze_requirements(ctx: AgentContext) -> RequirementAnalysis:
    text = ctx.text()
    extracted = extract_requirements(ctx)
    questions = _questions_for(ctx, text)
    blocking = [q for q in questions if q.blocking]

    platform = _detect_platform(text)
    db = _detect_db_engine(text)
    private = _wants_private(text)
    region = _detect_region(text) or ctx.region
    env = _detect_environment(text) or ctx.environment
    budget = _detect_budget(text)

    conflicts: list[str] = []
    if "windows" in text and "linux" in text:
        conflicts.append("Both Windows and Linux are requested; the target OS for the application tier is ambiguous.")
    if _wants_private(text) and any(w in text for w in ("public", "internet-facing")):
        conflicts.append("The workload is described as both private-only and internet-facing.")
    if _wants_multiregion(text) and any(w in text for w in ("single region", "one region")):
        conflicts.append("Single-region and multi-region deployment are both requested.")
    if "no public" in text and "public ip" in text:
        conflicts.append("Public IPs are requested while public exposure is stated as prohibited.")

    missing: list[str] = []
    if platform is None:
        missing.append("Application hosting platform")
    if db is None and "database" not in text:
        missing.append("Database requirements (engine, tier, HA)")
    if ("concurrent" in text or "users" in text) and "autoscal" not in text:
        missing.append("Scaling policy for the stated concurrency target")
    if budget is None:
        missing.append("Monthly budget")
    if region is None:
        missing.append("Target Azure region")
    if not any(w in text for w in ("private", "public", "internet", "vpn")):
        missing.append("Network exposure model")

    readiness = 25
    readiness += 15 if platform else 0
    readiness += 10 if region else 0
    readiness += 10 if not conflict_ingress(platform, private, text) else 0
    readiness += 10 if db else 5
    readiness += 10 if budget else 0
    readiness += 10 if any(w in text for w in ("production", "staging", "dev", "environment")) else 0
    readiness += 10 if any(w in text for w in ("private", "public", "internet", "vpn")) else 0
    readiness -= 10 * len(conflicts)
    readiness = max(0, min(100, readiness))

    assumptions = []
    if platform is None:
        assumptions.append("Assuming Azure App Service for the web tier unless the user specifies VMs or AKS.")
    if not private and env == "production":
        assumptions.append("Assuming internet-facing ingress with TLS termination for the production web tier.")
    assumptions.append(f"Assuming Azure region {catalog.region_label(region)} unless changed.")
    assumptions.append("Assuming a new VNet is created; no existing hub/peering integration unless confirmed.")
    if db:
        assumptions.append("Assuming a managed PaaS database with private connectivity and point-in-time restore.")

    summary_bits = []
    summary_bits.append(
        f"Understood a {env} workload"
        + (f" on {platform.replace('_', ' ')}" if platform else "")
        + (f" with {db.upper() if db == 'mssql' else db} database" if db else "")
        + (" using private networking" if private else "")
        + f" in {catalog.region_label(region)}."
    )
    if blocking:
        summary_bits.append(
            f"{len(blocking)} decision(s) are still needed before the architecture can be finalized."
        )
    else:
        summary_bits.append("The core requirements are sufficient to propose an architecture.")

    return RequirementAnalysis(
        summary=" ".join(summary_bits),
        requirements=extracted,
        questions=questions,
        missing_information=missing,
        conflicts=conflicts,
        assumptions=assumptions,
        requirements_complete=not blocking and not conflicts,
        readiness_score=readiness,
    )


def conflict_ingress(platform: str | None, private: bool, text: str) -> bool:
    return platform is None and not private


# ---------------------------------------------------------------------------
# Architecture design
# ---------------------------------------------------------------------------
def _slug(value: str, max_len: int = 40) -> str:
    value = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    return (value or "app")[:max_len].strip("-")


def _compact(value: str, max_len: int) -> str:
    value = re.sub(r"[^a-z0-9]+", "", value.lower())
    return (value or "app")[:max_len]


def _subnet_plan(count: int) -> list[str]:
    base = 10
    return [f"10.0.{base + i}.0/24" for i in range(count)]


def design_architecture(ctx: AgentContext, analysis: RequirementAnalysis | None = None) -> ArchitectureSpec:
    text = ctx.text()
    analysis = analysis or analyze_requirements(ctx)
    slug = _slug(ctx.project_name, 24)
    env = _detect_environment(text) or ctx.environment or "dev"
    region = _detect_region(text) or ctx.region or "eastus"
    is_prod = env == "production"

    platform = _detect_platform(text)
    db_engine = _detect_db_engine(text)
    private = _wants_private(text)
    ha = _wants_ha(text) or is_prod
    multiregion = _wants_multiregion(text)
    os_choice = _wants_oses(text)
    instances = _count_instances(text)
    budget = _detect_budget(text)
    has_compute = platform is not None
    requested_storage = any(w in text for w in ("storage", "blob", "file share", "object storage"))
    has_storage = requested_storage or db_engine is not None or has_compute
    include_kv = has_compute or db_engine is not None or has_storage or "key vault" in text
    include_monitoring = (
        has_compute
        or db_engine is not None
        or any(w in text for w in ("monitor", "log analytics", "observab", "diagnostic"))
    )
    has_web = has_compute and (
        platform in ("app_service", "aks")
        or any(w in text for w in ("web", "https", "http", "application", "api"))
    )
    public_ingress = not private
    name_prefix = f"{slug}-{env}"[:42]
    vnet_name = f"vnet-{name_prefix}"[:64]
    key_vault_name = f"kv-{slug}"[:24].rstrip("-")
    storage_name = _compact(f"{slug}{env}sa", 24)
    law_name = f"log-{name_prefix}"[:63]
    appi_name = f"appi-{name_prefix}"[:63]
    rg_name = f"rg-{name_prefix}"[:90]

    # subnet allocation -----------------------------------------------------
    subnet_specs: list[SubnetSpec] = []
    cidrs = _subnet_plan(5)
    app_subnet = SubnetSpec(name="snet-app", cidr=cidrs[0], purpose="application", nsg="nsg-app")
    subnet_specs.append(app_subnet)
    idx = 1
    db_subnet = None
    if db_engine:
        db_subnet = SubnetSpec(name="snet-data", cidr=cidrs[idx], purpose="database", nsg="nsg-data")
        subnet_specs.append(db_subnet)
        idx += 1
    pe_subnet = None
    if private:
        pe_subnet = SubnetSpec(
            name="snet-private-endpoints", cidr=cidrs[idx], purpose="private endpoints"
        )
        subnet_specs.append(pe_subnet)
        idx += 1
    gateway_subnet = None
    if platform == "vm" and public_ingress:
        gateway_subnet = SubnetSpec(
            name="snet-gateway", cidr=cidrs[idx], purpose="application gateway"
        )
        subnet_specs.append(gateway_subnet)
        idx += 1
    if not has_compute and not db_engine:
        # Network-only foundation: provide a second general-purpose subnet.
        subnet_specs.append(
            SubnetSpec(name="snet-general", cidr=cidrs[idx], purpose="general")
        )

    # nsg rules -------------------------------------------------------------
    admin_source = "10.0.0.0/8"
    app_rules = [
        {
            "name": "allow-https-from-gateway",
            "priority": 100,
            "direction": "Inbound",
            "access": "Allow",
            "protocol": "Tcp",
            "destination_port_ranges": ["443"],
            "source_address_prefixes": [gateway_subnet.cidr] if gateway_subnet else ["Internet"],
        },
        {
            "name": "allow-http-from-gateway",
            "priority": 110,
            "direction": "Inbound",
            "access": "Allow",
            "protocol": "Tcp",
            "destination_port_ranges": ["80"],
            "source_address_prefixes": [gateway_subnet.cidr] if gateway_subnet else ["Internet"],
        },
        {
            "name": "allow-ssh-from-admin",
            "priority": 200,
            "direction": "Inbound",
            "access": "Allow",
            "protocol": "Tcp",
            "destination_port_ranges": ["22"],
            "source_address_prefixes": [admin_source],
        },
        {
            "name": "allow-azure-load-balancer",
            "priority": 300,
            "direction": "Inbound",
            "access": "Allow",
            "protocol": "*",
            "destination_port_ranges": ["*"],
            "source_address_prefixes": ["AzureLoadBalancer"],
        },
        {
            "name": "deny-all-inbound",
            "priority": 4000,
            "direction": "Inbound",
            "access": "Deny",
            "protocol": "*",
            "destination_port_ranges": ["*"],
            "source_address_prefixes": ["*"],
        },
    ]
    data_rules = [
        {
            "name": "allow-db-from-app",
            "priority": 100,
            "direction": "Inbound",
            "access": "Allow",
            "protocol": "Tcp",
            "destination_port_ranges": ["1433" if db_engine == "mssql" else "5432"],
            "source_address_prefixes": [app_subnet.cidr],
        },
        {
            "name": "deny-all-inbound",
            "priority": 4000,
            "direction": "Inbound",
            "access": "Deny",
            "protocol": "*",
            "destination_port_ranges": ["*"],
            "source_address_prefixes": ["*"],
        },
    ]

    private_dns: list[str] = []
    if db_engine:
        private_dns.append(catalog.PRIVATE_DNS_ZONES.get(db_engine, ""))
    if private:
        if include_kv:
            private_dns.append(catalog.PRIVATE_DNS_ZONES["key_vault"])
        if has_storage:
            private_dns.append(catalog.PRIVATE_DNS_ZONES["storage"])
        if platform == "app_service":
            private_dns.append(catalog.PRIVATE_DNS_ZONES["app_service"])
    private_dns = [z for z in dict.fromkeys(private_dns) if z]

    if not has_compute:
        app_rules = [rule for rule in app_rules if rule["name"] == "deny-all-inbound"]
    elif platform != "vm":
        app_rules = [
            rule
            for rule in app_rules
            if rule["name"] not in ("allow-ssh-from-admin", "allow-azure-load-balancer")
        ]

    nsg_rules = {"nsg-app": app_rules}
    if db_engine:
        nsg_rules["nsg-data"] = data_rules

    network = NetworkSpec(
        id="vnet",
        name=vnet_name,
        address_space=["10.0.0.0/16"],
        subnets=subnet_specs,
        nsg_rules=nsg_rules,
        nat_gateway=private and platform in ("vm", "aks"),
        private_dns_zones=private_dns,
        route_tables=[],
    )

    # resources -------------------------------------------------------------
    resources: list[ArchitectureResource] = [
        ArchitectureResource(
            id="rg",
            type="azurerm_resource_group",
            name=rg_name,
            purpose="Resource container and lifecycle boundary",
            module="resource-group",
            config={"location": region},
        ),
        ArchitectureResource(
            id="vnet",
            type="azurerm_virtual_network",
            name=vnet_name,
            purpose="Isolated network boundary for the workload",
            module="networking",
            config={"address_space": ["10.0.0.0/16"]},
        ),
    ]
    for subnet in subnet_specs:
        resources.append(
            ArchitectureResource(
                id=f"subnet-{subnet.name}",
                type="azurerm_subnet",
                name=subnet.name,
                purpose=f"{subnet.purpose} subnet",
                module="networking",
                private=True,
                config={"cidr": subnet.cidr},
            )
        )
    resources.extend(
        [
            ArchitectureResource(
                id="nsg-app",
                type="azurerm_network_security_group",
                name="nsg-app",
                purpose="Least-privilege inbound rules for the application subnet",
                module="networking",
                depends_on=["subnet-snet-app"],
            ),
        ]
    )
    if db_engine:
        resources.append(
            ArchitectureResource(
                id="nsg-data",
                type="azurerm_network_security_group",
                name="nsg-data",
                purpose="Database subnet restricted to the application subnet",
                module="networking",
                depends_on=["subnet-snet-data"],
            )
        )
    if network.nat_gateway:
        resources.append(
            ArchitectureResource(
                id="nat",
                type="azurerm_nat_gateway",
                name=f"nat-{name_prefix}",
                purpose="Deterministic outbound egress without public IPs on workloads",
                module="networking",
                private=True,
            )
        )

    if platform == "vm":
        resources.extend(
            [
                ArchitectureResource(
                    id="identity",
                    type="azurerm_user_assigned_identity",
                    name=f"id-{name_prefix}",
                    purpose="Managed identity for Azure API access without secrets",
                    module="compute",
                ),
                ArchitectureResource(
                    id="vms",
                    type="azurerm_linux_virtual_machine" if os_choice == "linux" else "azurerm_windows_virtual_machine",
                    name=f"vm-{slug}",
                    purpose=f"{instances} application servers in the application subnet",
                    sku="Standard_D2s_v5",
                    module="compute",
                    private=True,
                    zone_redundant=ha,
                    depends_on=["identity", "subnet-snet-app"],
                    config={"count": instances, "zone_redundant": ha, "os": os_choice},
                ),
            ]
        )
        if public_ingress:
            resources.extend(
                [
                    ArchitectureResource(
                        id="appgw-pip",
                        type="azurerm_public_ip",
                        name=f"pip-appgw-{name_prefix}",
                        purpose="Public frontend for the Application Gateway",
                        module="networking",
                        config={"sku": "Standard"},
                    ),
                    ArchitectureResource(
                        id="appgw",
                        type="azurerm_application_gateway",
                        name=f"agw-{name_prefix}",
                        purpose="HTTPS ingress with WAF_v2 protection",
                        sku="WAF_v2",
                        module="compute",
                        zone_redundant=ha,
                        depends_on=["appgw-pip", "subnet-snet-gateway", "vms"],
                    ),
                ]
            )
    elif platform == "app_service":
        resources.extend(
            [
                ArchitectureResource(
                    id="asp",
                    type="azurerm_service_plan",
                    name=f"asp-{name_prefix}",
                    purpose="Compute plan for the web application",
                    sku="P1v3" if is_prod else "B1",
                    module="compute",
                ),
                ArchitectureResource(
                    id="app",
                    type="azurerm_linux_web_app",
                    name=f"app-{name_prefix}",
                    purpose="HTTPS web application with managed identity and VNet integration",
                    sku="P1v3" if is_prod else "B1",
                    module="compute",
                    private=private,
                    depends_on=["asp"],
                    config={"https_only": True, "min_tls_version": "1.2"},
                ),
                ArchitectureResource(
                    id="identity",
                    type="azurerm_user_assigned_identity",
                    name=f"id-{name_prefix}",
                    purpose="Managed identity for Key Vault and storage access",
                    module="compute",
                ),
            ]
        )
    elif platform == "aks":
        resources.extend(            [
                ArchitectureResource(
                    id="aks",
                    type="azurerm_kubernetes_cluster",
                    name=f"aks-{name_prefix}",
                    purpose="Managed Kubernetes cluster for containerized workloads",
                    sku="Standard",
                    module="compute",
                    private=private,
                    zone_redundant=ha,
                    depends_on=["subnet-snet-app"],
                    config={"node_count": max(2, instances), "private_cluster": private},
                ),
                ArchitectureResource(
                    id="identity",
                    type="azurerm_user_assigned_identity",
                    name=f"id-{name_prefix}",
                    purpose="Cluster identity without static credentials",
                    module="compute",
                ),
            ]
        )

    if db_engine:
        sql_sku = "GP_Gen5_2" if db_engine == "mssql" else (
            "GP_Standard_D2ds_v5" if db_engine == "postgres" else "GP_Standard_D2ds_v4"
        )
        resources.extend(
            [
                ArchitectureResource(
                    id="db-server",
                    type={
                        "mssql": "azurerm_mssql_server",
                        "postgres": "azurerm_postgresql_flexible_server",
                        "mysql": "azurerm_mysql_flexible_server",
                    }[db_engine],
                    name=f"sql-{slug}" if db_engine == "mssql" else f"{db_engine}-{slug}",
                    purpose="Managed relational database with TLS 1.2 and backup retention",
                    sku=sql_sku,
                    module="database",
                    private=private,
                    zone_redundant=ha,
                    depends_on=["subnet-snet-data"],
                    config={"engine": db_engine, "backup_retention_days": 35 if is_prod else 7},
                ),
                ArchitectureResource(
                    id="db",
                    type="azurerm_mssql_database" if db_engine == "mssql" else f"azurerm_{db_engine}_database",
                    name=f"{slug}-db",
                    purpose="Application database",
                    sku=sql_sku,
                    module="database",
                    depends_on=["db-server"],
                    config={"max_size_gb": 128 if is_prod else 32},
                ),
            ]
        )

    if has_storage:
        resources.extend(
            [
                ArchitectureResource(
                    id="storage",
                    type="azurerm_storage_account",
                    name=storage_name,
                    purpose="Private blob storage for application artifacts",
                    sku="Standard_ZRS" if ha else "Standard_LRS",
                    module="storage",
                    private=private,
                    depends_on=["subnet-snet-app"],
                    config={"public_network_access": not private, "min_tls_version": "TLS1_2"},
                ),
                ArchitectureResource(
                    id="storage-container",
                    type="azurerm_storage_container",
                    name="app-data",
                    purpose="Application data container",
                    module="storage",
                    depends_on=["storage"],
                ),
            ]
        )

    if include_kv:
        resources.append(
            ArchitectureResource(
                id="key-vault",
                type="azurerm_key_vault",
                name=key_vault_name,
                purpose="Secrets and certificate management with RBAC and private access",
                sku="standard",
                module="key-vault",
                private=private,
                depends_on=["identity"] if has_compute else [],
            )
        )
    if include_monitoring:
        resources.append(
            ArchitectureResource(
                id="log-analytics",
                type="azurerm_log_analytics_workspace",
                name=law_name,
                purpose="Centralized logs and metrics retention",
                module="monitoring",
                config={"retention_days": 90 if is_prod else 30},
            )
        )
    if has_web and include_monitoring:
        resources.append(
            ArchitectureResource(
                id="app-insights",
                type="azurerm_application_insights",
                name=appi_name,
                purpose="Application performance monitoring and distributed tracing",
                module="monitoring",
                depends_on=["log-analytics"],
            )
        )

    if private:
        pe_targets: list[str] = []
        if include_kv:
            pe_targets.append("key-vault")
        if db_engine == "mssql":
            pe_targets.append("db-server")
        if has_storage:
            pe_targets.append("storage")
        if platform == "app_service":
            pe_targets.append("app")
        if pe_targets:
            resources.append(
                ArchitectureResource(
                    id="private-dns",
                    type="azurerm_private_dns_zone",
                    name=",".join(private_dns) if private_dns else "privatelink",
                    purpose="Private DNS resolution for private endpoints",
                    module="networking",
                    private=True,
                    config={"zones": private_dns},
                )
            )
        for target in pe_targets:
            resources.append(
                ArchitectureResource(
                    id=f"pe-{target}",
                    type="azurerm_private_endpoint",
                    name=f"pe-{target}-{name_prefix}"[:64],
                    purpose=f"Private connectivity for {target}",
                    module="networking",
                    private=True,
                    depends_on=[target, "subnet-snet-private-endpoints"],
                )
            )

    if include_monitoring:
        resources.append(
            ArchitectureResource(
                id="diagnostics",
                type="azurerm_monitor_diagnostic_setting",
                name="central-diagnostics",
                purpose="Diagnostic settings forwarding logs and metrics to Log Analytics",
                module="monitoring",
                depends_on=["log-analytics"],
            )
        )

    # cost ------------------------------------------------------------------
    cost_lines: list[CostLine] = []
    for resource in resources:
        line = _cost_for_resource(resource, instances, has_storage, db_engine)
        if line:
            cost_lines.append(line)
    if private:
        pe_count = sum(1 for r in resources if r.type == "azurerm_private_endpoint")
        line = catalog.price_line("private_endpoint:standard", pe_count)
        cost_lines.append(CostLine(**line))
    total = round(sum(line.monthly for line in cost_lines), 2)

    cost = CostEstimate(currency="USD", monthly_total=total, lines=cost_lines)

    # recommendations -------------------------------------------------------
    recommendations = _baseline_recommendations(
        private=private, is_prod=is_prod, ha=ha, db_engine=db_engine, platform=platform, budget=budget
    )

    private_services = private and (
        include_kv or has_storage or db_engine == "mssql" or platform == "app_service"
    )
    rbac_entries: list[dict[str, str]] = []
    if include_kv:
        rbac_entries.append({"principal": "app", "role": "Key Vault Secrets User", "scope": "key-vault"})
    if has_storage:
        rbac_entries.append(
            {"principal": "app", "role": "Storage Blob Data Contributor", "scope": "storage"}
        )

    security = SecuritySpec(
        managed_identity=has_compute or include_kv,
        key_vault=include_kv,
        private_endpoints=private_services,
        diagnostics=include_monitoring,
        rbac=rbac_entries,
        notes=[
            "All service-to-service authentication uses managed identities; no keys are embedded in code.",
            "TLS 1.2 is enforced as the minimum on every endpoint.",
            "Public network access is disabled wherever private endpoints are used.",
        ],
    )

    availability = AvailabilitySpec(
        zones=ha,
        multi_region=multiregion,
        backup=(
            f"Azure SQL point-in-time restore with {35 if is_prod else 7}-day retention, "
            "geo-redundant backup disabled by default"
            if db_engine == "mssql"
            else "Platform-native automated backups; verify retention meets the RPO"
        ),
        rpo="1h" if ha else "24h",
        rto="4h" if ha else "24h",
        dr_strategy=(
            "Zone-redundant deployment, verified restore procedure, and infrastructure as code "
            "for rebuilding into a secondary region if required."
            if not multiregion
            else "Active-passive secondary region with geo-replicated data and documented failover runbook."
        ),
    )

    decisions = [
        ArchitectureDecisionSpec(
            decision=(
                f"Use {platform.replace('_', ' ')} for the application tier"
                if platform
                else "Deploy a segmented virtual network foundation without compute"
            ),
            rationale=(
                "Matches the requested hosting model and minimizes operational overhead while "
                "supporting the stated availability target."
            ),
            source_requirement=_first_matching(ctx, ["app service", "virtual machine", "kubernetes", "web"]),
        ),
        ArchitectureDecisionSpec(
            decision="Deploy a new hub-less VNet with segmented subnets and NSGs",
            rationale="Provides network isolation and a least-privilege path between tiers.",
            source_requirement=_first_matching(ctx, ["network", "vnet", "subnet"]),
        ),
        ArchitectureDecisionSpec(
            decision="Use managed identities plus Key Vault instead of stored credentials",
            rationale="Removes long-lived secrets from configuration and supports least-privilege RBAC.",
            source_requirement=_first_matching(ctx, ["key vault", "secret", "security"]),
        ),
        ArchitectureDecisionSpec(
            decision="Use zone-redundant SKUs for production" if ha else "Start single-zone with a documented DR path",
            rationale="Balances availability against cost based on the stated criticality.",
            source_requirement=_first_matching(ctx, ["high availability", "availability", "sla"]),
        ),
    ]

    assumptions = list(dict.fromkeys(
        analysis.assumptions
        + [
            f"Region {catalog.region_label(region)}.",
            "A dedicated VNet (10.0.0.0/16) without existing peering or hub integration.",
            "SSH public keys and database administrator credentials are supplied through pipeline secrets, never committed.",
            "Cost figures are indicative retail estimates, not a quotation.",
        ]
    ))
    limitations = [
        "Architecture diagram is a logical view; it is not a scale drawing of the deployed network.",
        "Latency, throughput and quota validation require a live Terraform plan against the target subscription.",
        "Azure Policy and subscription quotas can reject resources that are valid here.",
    ]
    if multiregion:
        limitations.append("Multi-region active-active data replication is not implemented in v1; this design uses active-passive patterns.")

    summary = _architecture_summary(
        ctx, platform, db_engine, private, ha, region, instances, rg_name, total
    )

    return ArchitectureSpec(
        summary=summary,
        region=region,
        environments=[env],
        resources=resources,
        networks=[network],
        security=security,
        availability=availability,
        cost=cost,
        recommendations=recommendations,
        assumptions=assumptions,
        limitations=limitations,
        decisions=decisions,
    )


def _first_matching(ctx: AgentContext, keywords: list[str]) -> str | None:
    for req in ctx.requirements:
        description = str(req.get("description", ""))
        if any(kw in description.lower() for kw in keywords):
            return description[:500]
    return None


def _cost_for_resource(
    resource: ArchitectureResource, instances: int, has_storage: bool, db_engine: str | None
) -> CostLine | None:
    if resource.type == "azurerm_linux_virtual_machine" or resource.type == "azurerm_windows_virtual_machine":
        sku = resource.sku or "Standard_D2s_v5"
        return CostLine(**catalog.price_line(f"vm:{sku}", instances))
    if resource.type == "azurerm_service_plan":
        sku = resource.sku or "P1v3"
        return CostLine(**catalog.price_line(f"app_service:{sku}", 1))
    if resource.type == "azurerm_application_gateway":
        return CostLine(**catalog.price_line("app_gateway:WAF_v2", 1))
    if resource.type == "azurerm_public_ip":
        return CostLine(**catalog.price_line("public_ip:Standard", 1))
    if resource.type == "azurerm_nat_gateway":
        return CostLine(**catalog.price_line("nat:Standard", 1))
    if resource.type == "azurerm_mssql_database":
        return CostLine(**catalog.price_line("sql:GP_Gen5_2", 1))
    if resource.type == "azurerm_postgresql_flexible_server":
        return CostLine(**catalog.price_line("postgres:GP_Standard_D2ds_v5", 1))
    if resource.type == "azurerm_mysql_flexible_server":
        return CostLine(**catalog.price_line("mysql:GP_Standard_D2ds_v4", 1))
    if resource.type == "azurerm_storage_account":
        return CostLine(**catalog.price_line("storage:Standard_LRS", 100))
    if resource.type == "azurerm_key_vault":
        return CostLine(**catalog.price_line("keyvault:standard", 1))
    if resource.type == "azurerm_log_analytics_workspace":
        return CostLine(**catalog.price_line("log_analytics:PayAsYouGo", 10))
    if resource.type == "azurerm_application_insights":
        return CostLine(**catalog.price_line("app_insights:standard", 5))
    if resource.type == "azurerm_kubernetes_cluster":
        return CostLine(**catalog.price_line("aks:Standard", 1))
    return None


def _baseline_recommendations(
    *, private: bool, is_prod: bool, ha: bool, db_engine: str | None, platform: str, budget: float | None
) -> list[Recommendation]:
    recs: list[Recommendation] = []
    counter = 1

    def add(**kwargs: Any) -> None:
        nonlocal counter
        recs.append(Recommendation(id=f"rec-{counter}", **kwargs))
        counter += 1

    if not private:
        add(
            category="security",
            recommendation="Restrict the workload to private networking",
            current_limitation="The application tier is reachable from the public internet.",
            proposed_improvement="Add private endpoints, private DNS and an internal-only ingress path.",
            technical_impact="Requires VNet integration and DNS resolution changes for clients.",
            security_impact="Removes the public attack surface for data-plane services.",
            availability_impact="No negative impact; private endpoints are zone-redundant.",
            cost_impact="Adds approximately $7–$30/month per private endpoint.",
            mandatory=is_prod,
        )
    if not ha and is_prod:
        add(
            category="availability",
            recommendation="Enable availability zones for the production tier",
            current_limitation="A single zone failure takes the workload offline.",
            proposed_improvement="Deploy zone-redundant compute and database SKUs across zones.",
            technical_impact="Resource SKU changes and an apply that may replace instances.",
            security_impact="No direct security impact.",
            availability_impact="Removes the single-zone single point of failure.",
            cost_impact="Typically +20–60% for compute and database.",
            mandatory=False,
        )
    if db_engine:
        add(
            category="operations",
            recommendation="Verify database backup retention against the RPO",
            current_limitation="Default backup retention may be shorter than the agreed RPO.",
            proposed_improvement="Set short-term retention to at least 14 days and test restore quarterly.",
            technical_impact="Configuration change only; no downtime for retention increases.",
            security_impact="Supports ransomware recovery and audit requirements.",
            availability_impact="Improves recoverability.",
            cost_impact="Backup storage cost scales with retention.",
            mandatory=True,
        )
    add(
        category="governance",
        recommendation="Apply a tagging and naming standard to every resource",
        current_limitation="Without consistent tags, cost allocation and ownership are unclear.",
        proposed_improvement="Enforce project, environment, owner and managed_by tags through Azure Policy.",
        technical_impact="No runtime impact.",
        security_impact="Improves auditability and incident ownership.",
        availability_impact="No impact.",
        cost_impact="Enables chargeback and waste detection.",
        mandatory=True,
    )
    if budget is None:
        add(
            category="cost",
            recommendation="Configure a monthly budget and alert thresholds",
            current_limitation="No budget guardrail is defined for the project.",
            proposed_improvement="Create an Azure budget with 50/80/100% alerts and an action group.",
            technical_impact="Monitoring only.",
            security_impact="No impact.",
            availability_impact="No impact.",
            cost_impact="Prevents unexpected spend; Azure budgets are free.",
            mandatory=False,
        )
    if platform == "app_service":
        add(
            category="performance",
            recommendation="Enable autoscaling on the App Service plan",
            current_limitation="A fixed instance count cannot absorb traffic spikes.",
            proposed_improvement="Add autoscale rules based on CPU and HTTP queue length.",
            technical_impact="Scale-out rules and warm-up behaviour must be tested.",
            security_impact="Reduces exhaustion-based denial of service.",
            availability_impact="Improves resilience under load.",
            cost_impact="Pay only for additional instances while scaled out.",
            mandatory=False,
        )
    return recs


def _architecture_summary(
    ctx: AgentContext,
    platform: str,
    db_engine: str | None,
    private: bool,
    ha: bool,
    region: str,
    instances: int,
    rg_name: str,
    total: float,
) -> str:
    platform_label = {
        "app_service": "Azure App Service",
        "vm": f"{instances} Linux/Windows virtual machines",
        "aks": "Azure Kubernetes Service",
        None: "a segmented Azure virtual network foundation",
    }[platform]
    db_label = {
        "mssql": "Azure SQL Database",
        "postgres": "Azure Database for PostgreSQL Flexible Server",
        "mysql": "Azure Database for MySQL Flexible Server",
        None: "no relational database",
    }[db_engine]
    ingress = (
        "No application ingress is included in this network-only scope"
        if platform is None
        else (
            "Internet-facing HTTPS ingress protected by Application Gateway WAF_v2"
            if platform == "vm" and not private
            else ("Private endpoints and private DNS" if private else "Public HTTPS endpoints with TLS 1.2 minimum")
        )
    )
    return (
        f"The proposed {ctx.environment} architecture for {ctx.project_name} deploys {platform_label} "
        f"in {catalog.region_label(region)} inside resource group {rg_name}, backed by {db_label}. "
        f"{ingress} is used, and all service-to-service authentication flows through managed identities "
        f"with secrets held in Azure Key Vault. "
        f"{'Availability zones are enabled for the critical tiers. ' if ha else 'The design starts single-zone with a documented recovery path. '}"
        f"Observability is centralized in Log Analytics with diagnostic settings forwarded from each service. "
        f"Indicative run rate is approximately ${total:,.0f}/month. "
        "These figures are estimates, not an Azure quotation."
    )


# ---------------------------------------------------------------------------
# Architecture review
# ---------------------------------------------------------------------------
def review_architecture(spec: ArchitectureSpec, environment: str = "dev") -> ArchitectureReview:
    is_prod = environment == "production"
    recs: list[Recommendation] = []
    risks: list[str] = []
    score = 100
    counter = 1

    def add(**kwargs: Any) -> None:
        nonlocal counter, score
        recs.append(Recommendation(id=f"rec-{counter}", **kwargs))
        counter += 1
        severity = kwargs.get("mandatory")
        score -= 8 if severity else 3

    resources = {r.id: r for r in spec.resources}
    public_services = [
        r for r in spec.resources
        if r.private is False and r.type in (
            "azurerm_storage_account", "azurerm_mssql_server", "azurerm_key_vault",
            "azurerm_linux_web_app", "azurerm_postgresql_flexible_server",
        ) and not r.config.get("public_intended")
    ]
    if public_services and is_prod:
        add(
            category="security",
            recommendation="Disable public network access on data-plane services",
            current_limitation=", ".join(sorted({r.name for r in public_services})) + " are public.",
            proposed_improvement="Use private endpoints with private DNS zones and disable public access.",
            technical_impact="Clients must resolve private DNS; deployment pipelines need VNet connectivity.",
            security_impact="Removes internet-reachable data planes and credential attack surface.",
            availability_impact="Private endpoints are zone-redundant.",
            cost_impact="About $7/month per endpoint plus DNS.",
            mandatory=True,
        )
        risks.append("Public data-plane endpoints exist in a production environment.")
    if not spec.security.key_vault:
        add(
            category="security",
            recommendation="Introduce Azure Key Vault for secrets",
            current_limitation="No managed secret store is defined.",
            proposed_improvement="Deploy Key Vault with RBAC and managed identity access.",
            technical_impact="Applications read secrets at runtime instead of configuration.",
            security_impact="Removes long-lived secrets from code and pipelines.",
            availability_impact="Key Vault is zone-redundant when configured.",
            cost_impact="A few dollars per month for standard tier.",
            mandatory=True,
        )
        risks.append("No secret management service in the architecture.")
    if not spec.security.diagnostics:
        add(
            category="operations",
            recommendation="Enable diagnostic settings on all resources",
            current_limitation="No centralized logging is configured.",
            proposed_improvement="Forward logs and metrics to Log Analytics with alert rules.",
            technical_impact="Adds diagnostic settings and workspace ingestion.",
            security_impact="Provides detection and forensic evidence.",
            availability_impact="Enables proactive alerting.",
            cost_impact="Ingestion-based; typically $10–$50/month.",
            mandatory=True,
        )
        risks.append("Insufficient observability for incident response.")
    if not spec.availability.zones and is_prod:
        add(
            category="availability",
            recommendation="Adopt availability zones",
            current_limitation="Critical resources are single-zone.",
            proposed_improvement="Use zone-redundant or zonal deployments for compute and database.",
            technical_impact="SKU and zone parameters change; replacement may be required.",
            security_impact="No direct impact.",
            availability_impact="Survives a datacenter-level failure.",
            cost_impact="Typically 20–60% uplift.",
            mandatory=False,
        )
        risks.append("Single-zone failure can take the production workload offline.")
    if spec.availability.multi_region and not spec.availability.dr_strategy:
        add(
            category="availability",
            recommendation="Document the multi-region failover runbook",
            current_limitation="Multi-region is requested but no DR procedure is specified.",
            proposed_improvement="Define replication, traffic failover and data validation steps.",
            technical_impact="Requires Traffic Manager/Front Door and data replication.",
            security_impact="No direct impact.",
            availability_impact="Reduces RTO significantly.",
            cost_impact="Approximately doubles the regional footprint.",
            mandatory=True,
        )
    if is_prod and not any(r.type == "azurerm_management_lock" for r in spec.resources):
        add(
            category="governance",
            recommendation="Protect stateful resources with management locks",
            current_limitation="Production resources can be deleted by an operator or pipeline mistake.",
            proposed_improvement="Add CanNotDelete locks to the resource group or critical resources.",
            technical_impact="Deletion requires removing the lock first.",
            security_impact="Prevents accidental destructive actions.",
            availability_impact="Protects availability.",
            cost_impact="No cost.",
            mandatory=False,
        )
    if not any(r.type == "azurerm_log_analytics_workspace" for r in spec.resources):
        add(
            category="operations",
            recommendation="Add a Log Analytics workspace",
            current_limitation="Telemetry has no central destination.",
            proposed_improvement="Deploy a workspace with retention aligned to compliance.",
            technical_impact="Adds a monitoring module.",
            security_impact="Supports audit requirements.",
            availability_impact="Improves alerting.",
            cost_impact="Usage-based.",
            mandatory=True,
        )
    if spec.cost.monthly_total > 2000 and is_prod:
        add(
            category="cost",
            recommendation="Evaluate reserved instances or savings plans",
            current_limitation=f"Estimated run rate is ${spec.cost.monthly_total:,.0f}/month on pay-as-you-go.",
            proposed_improvement="Commit to 1-year reservations for steady-state compute and database.",
            technical_impact="Billing only; no architecture change.",
            security_impact="No impact.",
            availability_impact="No impact.",
            cost_impact="Typically 30–40% reduction on eligible resources.",
            mandatory=False,
        )

    score = max(0, min(100, score))
    return ArchitectureReview(
        overall_assessment=(
            f"{len(recs)} improvement(s) identified, "
            f"{sum(1 for r in recs if r.mandatory)} mandatory. "
            f"Overall architecture score {score}/100."
        ),
        score=score,
        recommendations=recs,
        risks=risks,
    )


# ---------------------------------------------------------------------------
# Diagram & inventory
# ---------------------------------------------------------------------------
def _node_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", value)[:48] or "node"


def _label(value: str) -> str:
    return value.replace('"', "'").replace("\n", " ")[:70]


def generate_mermaid(spec: ArchitectureSpec) -> str:
    lines: list[str] = ["flowchart TB"]
    network = spec.networks[0] if spec.networks else None
    resources = {r.id: r for r in spec.resources}

    if spec.security.private_endpoints:
        lines.append('  Users["Clients in private network / VPN"]:::external')
    else:
        lines.append('  Users["Internet users"]:::external')

    ingress = next((r for r in spec.resources if r.type == "azurerm_application_gateway"), None)
    public_ip = next((r for r in spec.resources if r.type == "azurerm_public_ip"), None)
    app = next((r for r in spec.resources if r.type in ("azurerm_linux_web_app", "azurerm_windows_web_app")), None)

    if ingress:
        lines.append(f'  Users -->|"HTTPS 443"| {_node_id(ingress.id)}')
    elif public_ip:
        lines.append(f'  Users -->|"HTTPS 443"| {_node_id(public_ip.id)}')

    subnet_members: dict[str, list[ArchitectureResource]] = {}
    if network:
        subnet_members = _subnet_membership(spec)
        lines.append(f'  subgraph VNET["VNet {_label(network.name)} · {network.address_space[0]}"]')
        lines.append("    direction TB")
        for subnet in network.subnets:
            safe = _node_id(subnet.name)
            lines.append(f'    subgraph {safe}["{_label(subnet.name)} · {subnet.cidr}"]')
            lines.append("      direction TB")
            members = subnet_members.get(subnet.name, [])
            if not members:
                lines.append(f'      {safe}_empty["reserved"]:::muted')
            for resource in members:
                label = _label(resource.name)
                if resource.type in (
                    "azurerm_mssql_server",
                    "azurerm_postgresql_flexible_server",
                    "azurerm_mysql_flexible_server",
                ):
                    lines.append(f'      {_node_id(resource.id)}[("{label}")]:::data')
                elif resource.type == "azurerm_private_endpoint":
                    pe_labels = {
                        "pe-key-vault": "Key Vault PE",
                        "pe-storage": "Storage PE",
                        "pe-db-server": "Database PE",
                        "pe-app": "App Service PE",
                    }
                    label = pe_labels.get(resource.id, "Private endpoint")
                    lines.append(f'      {_node_id(resource.id)}["{label}"]:::security')
                else:
                    cls = {"compute": "compute", "database": "data"}.get(resource.module, "compute")
                    lines.append(f'      {_node_id(resource.id)}["{label}"]:::{cls}')
            lines.append("    end")
        lines.append("  end")

    # resources outside the VNet (PaaS / management)
    for resource in spec.resources:
        if resource.type in (
            "azurerm_resource_group", "azurerm_virtual_network", "azurerm_subnet",
            "azurerm_network_security_group", "azurerm_private_dns_zone",
            "azurerm_monitor_diagnostic_setting", "azurerm_public_ip",
        ):
            continue
        if _is_inside_subnet(resource, subnet_members):
            continue
        if resource.type == "azurerm_application_gateway":
            continue
        cls = {"database": "data", "storage": "data", "key-vault": "security", "monitoring": "ops"}.get(
            resource.module, "compute"
        )
        if resource.module == "database":
            lines.append(f'  {_node_id(resource.id)}[("{_label(resource.name)}")]:::{cls}')
        else:
            lines.append(f'  {_node_id(resource.id)}["{_label(resource.name)}"]:::{cls}')

    # edges
    if ingress:
        for vm in [r for r in spec.resources if r.type in ("azurerm_linux_virtual_machine", "azurerm_windows_virtual_machine")]:
            lines.append(f'  {_node_id(ingress.id)} -->|"backend 443"| {_node_id(vm.id)}')
    if app:
        pe_app = next((r for r in spec.resources if r.id == "pe-app"), None)
        if spec.security.private_endpoints and pe_app:
            lines.append(f'  Users -->|"private DNS"| {_node_id(pe_app.id)}')
            lines.append(f'  {_node_id(pe_app.id)} -->|"HTTPS 443"| {_node_id(app.id)}')
        elif not spec.security.private_endpoints:
            lines.append(f'  Users -->|"HTTPS 443"| {_node_id(app.id)}')
    compute = next(
        (r for r in spec.resources if r.type in (
            "azurerm_linux_virtual_machine", "azurerm_windows_virtual_machine",
            "azurerm_linux_web_app", "azurerm_kubernetes_cluster",
        )),
        None,
    )
    db = next((r for r in spec.resources if r.module == "database" and r.type not in ("azurerm_mssql_database",)), None)
    storage = next((r for r in spec.resources if r.type == "azurerm_storage_account"), None)
    vault = next((r for r in spec.resources if r.type == "azurerm_key_vault"), None)
    logs = next((r for r in spec.resources if r.type == "azurerm_log_analytics_workspace"), None)
    identity = next((r for r in spec.resources if r.type == "azurerm_user_assigned_identity"), None)
    if compute:
        if db:
            lines.append(f'  {_node_id(compute.id)} -->|"TLS 1433/5432"| {_node_id(db.id)}')
        if storage:
            lines.append(f'  {_node_id(compute.id)} -->|"blob (private)"| {_node_id(storage.id)}')
        if vault:
            lines.append(f'  {_node_id(compute.id)} -->|"secrets at runtime"| {_node_id(vault.id)}')
        if logs:
            lines.append(f'  {_node_id(compute.id)} -.->|"logs & metrics"| {_node_id(logs.id)}')
        if identity:
            lines.append(f'  {_node_id(identity.id)} -.->|"managed identity"| {_node_id(compute.id)}')

    lines.extend(
        [
            "  classDef external fill:#0f172a,stroke:#38bdf8,color:#e2e8f0",
            "  classDef security fill:#3b0764,stroke:#c084fc,color:#f5d0fe",
            "  classDef compute fill:#0c4a6e,stroke:#38bdf8,color:#e0f2fe",
            "  classDef data fill:#14532d,stroke:#4ade80,color:#dcfce7",
            "  classDef network fill:#422006,stroke:#fbbf24,color:#fef3c7",
            "  classDef ops fill:#1e1b4b,stroke:#818cf8,color:#e0e7ff",
            "  classDef muted fill:#1f2937,stroke:#475569,color:#94a3b8",
        ]
    )
    return "\n".join(lines)


def _subnet_membership(spec: ArchitectureSpec) -> dict[str, list[ArchitectureResource]]:
    membership: dict[str, list[ArchitectureResource]] = {}
    network = spec.networks[0] if spec.networks else None
    if not network:
        return membership
    app_subnet = next((s.name for s in network.subnets if s.purpose == "application"), None)
    gateway_subnet = next((s.name for s in network.subnets if s.purpose == "application gateway"), None)
    data_subnet = next((s.name for s in network.subnets if s.purpose == "database"), None)
    pe_subnet = next((s.name for s in network.subnets if s.purpose == "private endpoints"), None)
    for resource in spec.resources:
        if resource.type in (
            "azurerm_linux_virtual_machine",
            "azurerm_windows_virtual_machine",
            "azurerm_kubernetes_cluster",
            "azurerm_linux_web_app",
            "azurerm_windows_web_app",
        ):
            if app_subnet:
                membership.setdefault(app_subnet, []).append(resource)
        elif resource.type in (
            "azurerm_mssql_server",
            "azurerm_postgresql_flexible_server",
            "azurerm_mysql_flexible_server",
        ):
            if data_subnet:
                membership.setdefault(data_subnet, []).append(resource)
        elif resource.type == "azurerm_private_endpoint":
            if pe_subnet:
                membership.setdefault(pe_subnet, []).append(resource)
        elif resource.type == "azurerm_application_gateway" and gateway_subnet:
            membership.setdefault(gateway_subnet, []).append(resource)
    return membership


def _is_inside_subnet(resource: ArchitectureResource, membership: dict[str, list[ArchitectureResource]]) -> bool:
    return any(resource in members for members in membership.values())


def build_inventory(spec: ArchitectureSpec) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for resource in spec.resources:
        label = catalog.SERVICE_LABELS.get(resource.type, resource.type)
        config_bits = []
        if resource.sku:
            config_bits.append(f"SKU {resource.sku}")
        if resource.private:
            config_bits.append("private")
        if resource.zone_redundant:
            config_bits.append("zone-redundant")
        if resource.config.get("cidr"):
            config_bits.append(str(resource.config["cidr"]))
        rows.append(
            {
                "component": resource.purpose or label,
                "azure_resource": label,
                "terraform_type": resource.type,
                "name": resource.name,
                "configuration": ", ".join(config_bits) or "default",
                "module": resource.module,
            }
        )
    return rows


def architecture_to_diagram_and_inventory(spec: ArchitectureSpec) -> tuple[str, list[dict[str, Any]]]:
    return generate_mermaid(spec), build_inventory(spec)
