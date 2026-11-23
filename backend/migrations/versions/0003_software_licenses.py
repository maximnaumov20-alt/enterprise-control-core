"""Add tenant-scoped software licence inventory.

Revision ID: 0003_software_licenses
Revises: 0002_tenants_auth_audit
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003_software_licenses"
down_revision: Union[str, None] = "0002_tenants_auth_audit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "software_licenses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("publisher", sa.String(length=120), nullable=True),
        sa.Column("version", sa.String(length=80), nullable=True),
        sa.Column("license_type", sa.String(length=16), nullable=False),
        sa.Column("license_reference", sa.String(length=120), nullable=True),
        sa.Column("seats_total", sa.Integer(), nullable=False),
        sa.Column("seats_used", sa.Integer(), nullable=False),
        sa.Column("purchased_at", sa.Date(), nullable=True),
        sa.Column("expires_at", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "organization_id", "license_reference", name="uq_software_org_reference"
        ),
        sa.CheckConstraint("seats_total >= 1", name="ck_software_seats_total_positive"),
        sa.CheckConstraint(
            "seats_used >= 0 AND seats_used <= seats_total",
            name="ck_software_seats_used_valid",
        ),
    )
    op.create_index("ix_software_licenses_organization_id", "software_licenses", ["organization_id"])
    op.create_index("ix_software_licenses_name", "software_licenses", ["name"])


def downgrade() -> None:
    op.drop_index("ix_software_licenses_name", table_name="software_licenses")
    op.drop_index("ix_software_licenses_organization_id", table_name="software_licenses")
    op.drop_table("software_licenses")
