"""Hospital reception phone — where a phone call is transferred for a human.

When the voice agent escalates on a real phone (SIP) call, the call is
transferred (SIP REFER) to this number instead of only flagging the
conversation for later follow-up. Empty = no transfer; the escalation is
recorded as before.

Revision ID: 0032
Revises: 0031
Create Date: 2026-10-20
"""
from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE hospitals ADD COLUMN IF NOT EXISTS reception_phone TEXT NOT NULL DEFAULT ''"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE hospitals DROP COLUMN IF EXISTS reception_phone")
