"""separate voice greeting

Adds clinics.voice_greeting_message so the admin can set a distinct opening line
for phone calls vs. text chat. NULL/empty means "fall back to the text greeting,
then the LLM-generated greeting".

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-19
"""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE clinics ADD COLUMN IF NOT EXISTS voice_greeting_message TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE clinics DROP COLUMN IF EXISTS voice_greeting_message")
