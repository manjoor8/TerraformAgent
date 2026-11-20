from __future__ import annotations

import ipaddress
import re

import pytest

from app.agents.heuristics import AgentContext, analyze_requirements, design_architecture, generate_mermaid
from app.agents.schemas import ArchitectureSpec
from app.agents.validation import ArchitectureValidationError, normalize_and_validate
from app.terraform.generator import ProjectGenInput, TerraformProjectGenerator

REQUIREMENTS = (
    "I want a highly available web application in Azure with Azure App Service, an Azure SQL "
    "Database, a storage account and private networking. It must be accessible through HTTPS, "
    "serve 5000 concurrent users, run in UAE North and stay within a 2000 USD monthly budget."
)


def _context() -> AgentContext:
    return AgentContext(
        project_name="Contoso Portal",
        description=REQUIREMENTS,
        environment="production",
        region="uaenorth",
        messages=[REQUIREMENTS],
    )


def _design() -> ArchitectureSpec:
    ctx = _context()
    analysis = analyze_requirements(ctx)
    spec = design_architecture(ctx, analysis)
    return normalize_and_validate(spec, environment="production", use_live_pricing=False).spec


def test_requirement_analysis_generates_manageable_questions() -> None:
    analysis = analyze_requirements(_context())
    assert len(analysis.questions) <= 4
    assert analysis.readiness_score >= 70
    assert analysis.requirements
    assert any("web" in requirement.description.lower() for requirement in analysis.requirements)


def test_architecture_covers_azure_layers_and_private_networking() -> None:
    spec = _design()
    types = {resource.type for resource in spec.resources}
    for expected in {
        "azurerm_resource_group",
        "azurerm_virtual_network",
        "azurerm_linux_web_app",
        "azurerm_mssql_server",
        "azurerm_storage_account",
        "azurerm_key_vault",
        "azurerm_log_analytics_workspace",
        "azurerm_private_endpoint",
    }:
        assert expected in types, f"missing {expected}"
    assert spec.security.private_endpoints is True
    assert spec.security.key_vault is True
    assert spec.cost.monthly_total > 0
    assert spec.availability.zones is True
    assert spec.decisions
    assert spec.assumptions


def test_network_cidrs_are_valid_non_overlapping_and_inside_vnet() -> None:
    spec = _design()
    network = spec.networks[0]
    vnet = ipaddress.ip_network(network.address_space[0])
    seen: list[ipaddress.IPv4Network] = []
    for subnet in network.subnets:
        net = ipaddress.ip_network(subnet.cidr)
        assert net.subnet_of(vnet)
        assert all(not net.overlaps(other) for other in seen)
        seen.append(net)
    gateway = [subnet for subnet in network.subnets if subnet.purpose == "application gateway"]
    assert gateway == [], "private App Service design must not expose a public Application Gateway"
    assert any(subnet.purpose == "private endpoints" for subnet in network.subnets)


def test_nsg_rules_never_allow_open_ssh_from_the_internet() -> None:
    spec = _design()
    for nsg, rules in spec.networks[0].nsg_rules.items():
        for rule in rules:
            if rule.get("access") != "Allow":
                continue
            source = [str(prefix).lower() for prefix in rule.get("source_address_prefixes", [])]
            ports = [str(port) for port in rule.get("destination_port_ranges", [])]
            if "any" in source or "*" in source:
                assert "22" not in ports, f"{nsg} allows SSH from the internet"
                assert "3389" not in ports, f"{nsg} allows RDP from the internet"


def test_overlapping_subnets_are_rejected() -> None:
    spec = _design()
    spec.networks[0].subnets[1].cidr = spec.networks[0].subnets[0].cidr
    with pytest.raises(ArchitectureValidationError):
        normalize_and_validate(spec, environment="production", use_live_pricing=False)


def test_design_with_existing_requirements_links_decisions() -> None:
    ctx = _context()
    ctx.requirements = [
        {
            "requirement_type": "compute",
            "description": "Use Azure App Service for the web tier with autoscaling.",
            "priority": "mandatory",
        },
        {
            "requirement_type": "network",
            "description": "All data services must use private endpoints and private DNS.",
            "priority": "mandatory",
        },
    ]
    analysis = analyze_requirements(ctx)
    spec = design_architecture(ctx, analysis)
    assert any(decision.source_requirement for decision in spec.decisions)
    assert any("App Service" in (decision.source_requirement or "") for decision in spec.decisions)


def test_public_vm_design_gets_a_gateway_subnet() -> None:
    message = (
        "Deploy two Linux application servers behind an Azure Application Gateway with a public IP "
        "and HTTPS, plus Azure SQL and a Key Vault in East US."
    )
    ctx = AgentContext(
        project_name="Public Portal",
        description=message,
        environment="production",
        region="eastus",
        messages=[message],
    )
    spec = design_architecture(ctx, analyze_requirements(ctx))
    gateway = [subnet for subnet in spec.networks[0].subnets if subnet.purpose == "application gateway"]
    assert gateway, [subnet.purpose for subnet in spec.networks[0].subnets]
    assert ipaddress.ip_network(gateway[0].cidr).prefixlen <= 24
    assert any(resource.type == "azurerm_application_gateway" for resource in spec.resources)


def test_diagram_reflects_actual_resources() -> None:
    spec = _design()
    diagram = generate_mermaid(spec)
    assert diagram.startswith("flowchart TB")
    assert "subgraph VNET" in diagram
    assert any(resource.name in diagram for resource in spec.resources if resource.type == "azurerm_key_vault")
    assert "private network" in diagram


def test_generator_creates_modular_project_without_secrets() -> None:
    spec = _design()
    info = ProjectGenInput(
        project_name="Contoso Portal",
        project_id="test-project-id",
        environment="production",
        region="uaenorth",
        tags={"owner": "platform"},
        backend={"mode": "local"},
        subscription_id="00000000-0000-0000-0000-000000000000",
    )
    project = TerraformProjectGenerator(spec, info).build()
    files = project.files

    assert "versions.tf" in files
    assert "main.tf" in files
    assert "environments/production/main.tf" in files
    assert "environments/production/backend.tf" not in files  # backend lives in versions.tf
    assert "modules/networking/main.tf" in files
    assert "modules/key-vault/main.tf" in files
    assert "modules/compute/main.tf" in files
    assert "scripts/validate.sh" in files
    assert "README.md" in files
    assert "azurem" not in files["versions.tf"].lower()

    for path, content in files.items():
        assert "ChangeMe123" not in content, f"test password leaked into {path}"
        assert "SuperSecret" not in content, f"secret leaked into {path}"
    assert "hashicorp/azurerm" in files["versions.tf"]
    assert "~> 4.0" in files["versions.tf"]
    assert project.module_map
    assert any("azurerm_key_vault" in row["terraform_type"] for row in project.module_map)


def test_generated_hcl_is_formatted() -> None:
    spec = _design()
    info = ProjectGenInput(
        project_name="Formatting Test",
        project_id="format-project",
        environment="dev",
        region="eastus",
        backend={"mode": "local"},
    )
    project = TerraformProjectGenerator(spec, info).build()
    for path, content in project.files.items():
        if path.endswith(".tf"):
            assert "\r" not in content
            assert not re.search(r"\n\n\n", content), f"extra blank lines in {path}"
