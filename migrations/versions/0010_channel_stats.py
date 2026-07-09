"""Add channel_identifier to conversation_log and session_id to appointments.

These columns enable tracking how many calls each voice number receives
and how many appointments each number generates.

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-20
"""
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE conversation_log "
        "ADD COLUMN IF NOT EXISTS channel_identifier TEXT"
    )
    op.execute(
        "ALTER TABLE appointments "
        "ADD COLUMN IF NOT EXISTS session_id TEXT"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_conversation_log_channel_ident "
        "ON conversation_log (clinic_id, channel_identifier)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_appointments_session_id "
        "ON appointments (clinic_id, session_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_appointments_session_id")
    op.execute("DROP INDEX IF EXISTS ix_conversation_log_channel_ident")
    op.execute("ALTER TABLE appointments DROP COLUMN IF EXISTS session_id")
    op.execute("ALTER TABLE conversation_log DROP COLUMN IF EXISTS channel_identifier")
