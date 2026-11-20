"""llm configuration table

Revision ID: 7c1a2b3d4e5f
Revises: 83fc7f8fb5e3
Create Date: 2026-11-20
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7c1a2b3d4e5f"
down_revision: str | None = "83fc7f8fb5e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_configurations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False, server_default="deepseek"),
        sa.Column("model", sa.String(length=120), nullable=False, server_default="deepseek-chat"),
        sa.Column(
            "base_url",
            sa.String(length=500),
            nullable=False,
            server_default="https://api.deepseek.com/v1",
        ),
        sa.Column("api_key_encrypted", sa.Text(), nullable=True),
        sa.Column("api_key_last4", sa.String(length=8), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(length=36), nullable=True),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("llm_configurations")
