"""Add maintenance request workflow with same-organization foreign keys.

Revision ID: 0004_maintenance_requests
Revises: 0003_software_licenses
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004_maintenance_requests"
down_revision: Union[str, None] = "0003_software_licenses"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("assets") as batch:
        batch.create_unique_constraint("uq_assets_org_id_id", ["organization_id", "id"])
    with op.batch_alter_table("users") as batch:
        batch.create_unique_constraint("uq_users_org_id_id", ["organization_id", "id"])

    op.create_table(
        "maintenance_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("asset_id", sa.Integer(), nullable=False),
        sa.Column("requested_by_user_id", sa.Integer(), nullable=False),
        sa.Column("assigned_to_user_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("priority", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id", "asset_id"],
            ["assets.organization_id", "assets.id"],
            ondelete="RESTRICT",
            name="fk_maintenance_asset_same_org",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "requested_by_user_id"],
            ["users.organization_id", "users.id"],
            ondelete="RESTRICT",
            name="fk_maintenance_requester_same_org",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "assigned_to_user_id"],
            ["users.organization_id", "users.id"],
            ondelete="RESTRICT",
            name="fk_maintenance_assignee_same_org",
        ),
    )
    op.create_index("ix_maintenance_requests_organization_id", "maintenance_requests", ["organization_id"])
    op.create_index("ix_maintenance_requests_asset_id", "maintenance_requests", ["asset_id"])
    op.create_index("ix_maintenance_requests_requested_by_user_id", "maintenance_requests", ["requested_by_user_id"])
    op.create_index("ix_maintenance_requests_assigned_to_user_id", "maintenance_requests", ["assigned_to_user_id"])


def downgrade() -> None:
    op.drop_index("ix_maintenance_requests_assigned_to_user_id", table_name="maintenance_requests")
    op.drop_index("ix_maintenance_requests_requested_by_user_id", table_name="maintenance_requests")
    op.drop_index("ix_maintenance_requests_asset_id", table_name="maintenance_requests")
    op.drop_index("ix_maintenance_requests_organization_id", table_name="maintenance_requests")
    op.drop_table("maintenance_requests")
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("uq_users_org_id_id", type_="unique")
    with op.batch_alter_table("assets") as batch:
        batch.drop_constraint("uq_assets_org_id_id", type_="unique")
