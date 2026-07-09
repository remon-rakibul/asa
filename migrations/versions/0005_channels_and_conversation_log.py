"""richer channels + conversation log

- Adds clinics-facing metadata to `channels` (label, created_at) so the admin UI
  can list/manage WhatsApp / SMS / voice numbers mapped to a clinic.
- Adds `conversation_log`: an append-only record of each spoken turn (per channel)
  so the dashboard can show conversation transcripts.

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-19
"""
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE channels ADD COLUMN IF NOT EXISTS label TEXT")
    op.execute(
        "ALTER TABLE channels ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now()"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS conversation_log (
            id          BIGSERIAL PRIMARY KEY,
            clinic_id   INTEGER NOT NULL REFERENCES clinics(id) ON DELETE CASCADE,
            session_id  TEXT NOT NULL,
            channel     TEXT NOT NULL DEFAULT 'text',
            role        TEXT NOT NULL CHECK (role IN ('user','assistant')),
            text        TEXT NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_convlog_clinic_session "
        "ON conversation_log (clinic_id, session_id, created_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS conversation_log")
    op.execute("ALTER TABLE channels DROP COLUMN IF EXISTS label")
    op.execute("ALTER TABLE channels DROP COLUMN IF EXISTS created_at")
