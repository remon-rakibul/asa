"""clinic-editable greeting message

Adds clinics.greeting_message: a per-clinic opening line the admin can edit from
the dashboard. NULL/empty means "fall back to the LLM-generated greeting".

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-19
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE clinics ADD COLUMN IF NOT EXISTS greeting_message TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE clinics DROP COLUMN IF EXISTS greeting_message")
