"""Make users.clinic_id nullable to support platform_admin accounts.

Revision ID: 0012
Revises: 0011
Create Date: 2026-06-24
"""
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ALTER COLUMN clinic_id DROP NOT NULL")


def downgrade() -> None:
    op.execute("UPDATE users SET clinic_id = 1 WHERE clinic_id IS NULL")
    op.execute("ALTER TABLE users ALTER COLUMN clinic_id SET NOT NULL")
