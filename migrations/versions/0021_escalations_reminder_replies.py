"""Human escalation queue + two-way reminder replies.

* `escalations` — conversations the agent flagged for staff follow-up
  (request_human_help tool). Staff resolve them from the Conversations view.
* `appointments.patient_confirmed_at` — stamped when the patient replies "১"
  to the 24h reminder, so the queue shows who actually confirmed.

Revision ID: 0021
Revises: 0020
Create Date: 2026-07-02
"""
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS escalations (
            id          SERIAL PRIMARY KEY,
            clinic_id   INTEGER REFERENCES clinics(id) ON DELETE CASCADE,
            session_id  TEXT NOT NULL,
            channel     TEXT NOT NULL DEFAULT 'text',
            reason      TEXT NOT NULL DEFAULT '',
            status      TEXT NOT NULL DEFAULT 'open',   -- open | resolved
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            resolved_at TIMESTAMPTZ
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_escalations_clinic_status "
        "ON escalations (clinic_id, status)"
    )
    op.execute(
        "ALTER TABLE appointments ADD COLUMN IF NOT EXISTS patient_confirmed_at TIMESTAMPTZ"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE appointments DROP COLUMN IF EXISTS patient_confirmed_at")
    op.execute("DROP TABLE IF EXISTS escalations")
