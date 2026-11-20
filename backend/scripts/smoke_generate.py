"""Dev helper: generate a Terraform project from a requirement description.

Usage:
    python scripts/smoke_generate.py <scenario> <output-dir> [--backend azurerm]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents.heuristics import AgentContext, analyze_requirements, design_architecture
from app.agents.validation import normalize_and_validate
from app.terraform.generator import ProjectGenInput, TerraformProjectGenerator

SCENARIOS = {
    "simple": {
        "name": "Simple Network",
        "env": "dev",
        "region": "eastus",
        "message": (
            "Create one resource group, one virtual network and two subnets and one network "
            "security group. Keep it small and cheap."
        ),
    },
    "webapp": {
        "name": "Flight Booking",
        "env": "production",
        "region": "uaenorth",
        "message": (
            "I want to deploy a highly available web application in Azure. It should have Azure App "
            "Service, an Azure SQL Database, a storage account and private networking. The application must "
            "be accessible through HTTPS. It should support 5000 concurrent users. Use UAE North. "
            "Monthly budget is 2000 USD. Compliance is ISO 27001."
        ),
    },
    "vms": {
        "name": "Payments Platform",
        "env": "production",
        "region": "westeurope",
        "message": (
            "Deploy two Linux application servers with an Azure Application Gateway, a PostgreSQL database, "
            "a key vault and monitoring. Use private networking and availability zones. Budget 3000 USD."
        ),
    },
    "document": {
        "name": "Legacy Migration",
        "env": "staging",
        "region": "eastus",
        "message": (
            "We are migrating an internal inventory system. It uses a single SQL database and a storage "
            "account. No public internet access is allowed. Design it with private endpoints."
        ),
    },
}


def main() -> None:
    scenario = sys.argv[1] if len(sys.argv) > 1 else "webapp"
    out = Path(sys.argv[2] if len(sys.argv) > 2 else f"data/smoke/{scenario}").resolve()
    data = SCENARIOS.get(scenario, SCENARIOS["webapp"])
    ctx = AgentContext(
        project_name=data["name"],
        description=data["message"],
        environment=data["env"],
        region=data["region"],
        messages=[data["message"]],
    )
    analysis = analyze_requirements(ctx)
    spec = design_architecture(ctx, analysis)
    outcome = normalize_and_validate(spec, environment=data["env"], use_live_pricing=False)
    spec, warnings = outcome.spec, outcome.warnings
    info = ProjectGenInput(
        project_name=data["name"],
        project_id=f"smoke-{scenario}",
        environment=data["env"],
        region=data["region"],
        tags={"owner": "platform-team"},
        backend={"mode": "local"},
        subscription_id="00000000-0000-0000-0000-000000000000",
    )
    result = TerraformProjectGenerator(spec, info).build()
    for path, content in result.files.items():
        target = out / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    (out / "architecture.json").write_text(
        json.dumps(spec.model_dump(mode="json"), indent=2), encoding="utf-8"
    )
    print(f"Wrote {len(result.files)} files to {out}")
    print("modules:", sorted({m['module'] for m in result.module_map}))
    for warning in warnings:
        print("WARN:", warning)
    for note in result.notes:
        print("NOTE:", note)


if __name__ == "__main__":
    main()
