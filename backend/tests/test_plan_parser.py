from __future__ import annotations

from app.terraform.plan_parser import parse_plan_json


def _change(address: str, type_name: str, actions: list[str], **extra) -> dict:
    return {
        "address": address,
        "type": type_name,
        "name": address.split(".")[-1],
        "module_address": extra.get("module_address", ""),
        "change": {
            "actions": actions,
            "replace_paths": extra.get("replace_paths", []),
        },
    }


def test_plan_counts_and_destructive_classification() -> None:
    data = {
        "terraform_version": "1.9.0",
        "resource_changes": [
            _change("module.compute.azurerm_linux_virtual_machine.app[0]", "azurerm_linux_virtual_machine", ["create"]),
            _change("module.compute.azurerm_linux_virtual_machine.app[1]", "azurerm_linux_virtual_machine", ["create"]),
            _change("module.database.azurerm_mssql_database.db", "azurerm_mssql_database", ["delete"]),
            _change(
                "module.networking.azurerm_virtual_network.vnet",
                "azurerm_virtual_network",
                ["delete", "create"],
                replace_paths=[["address_space"]],
            ),
            _change("module.networking.azurerm_network_security_group.nsg", "azurerm_network_security_group", ["update"]),
            _change("module.storage.azurerm_storage_account.sa", "azurerm_storage_account", ["no-op"]),
        ],
        "output_changes": {
            "application_url": {"after": "https://example", "sensitive": False},
            "database_password": {"after": "secret", "sensitive": True},
        },
    }
    summary = parse_plan_json(data, plan_hash="sha256:abc", source_digest="sha256:def")

    assert summary.add == 2
    assert summary.change == 1
    assert summary.destroy == 1
    assert summary.replace == 1
    assert summary.total == 5
    assert summary.plan_hash == "sha256:abc"
    assert summary.source_digest == "sha256:def"

    destructive = {op.address: op for op in summary.destructive_operations}
    assert "module.database.azurerm_mssql_database.db" in destructive
    assert destructive["module.database.azurerm_mssql_database.db"].severity == "critical"
    assert "module.networking.azurerm_virtual_network.vnet" in destructive
    assert any(op.action == "update" and op.severity == "medium" for op in summary.destructive_operations)
    assert summary.outputs["database_password"] == "[sensitive]"
    assert summary.outputs["application_url"] == "https://example"
    # Highest severity first.
    assert summary.destructive_operations[0].severity == "critical"


def test_empty_plan() -> None:
    summary = parse_plan_json({"resource_changes": [], "output_changes": {}})
    assert summary.total == 0
    assert summary.destructive_operations == []
