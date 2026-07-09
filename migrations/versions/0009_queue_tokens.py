"""Queue / token system for hospital OPD departments.

Each successful booking in hospital mode generates a daily sequential token
(e.g. "A-42") so patients know their position in the queue. Receptionists
call tokens via the admin dashboard; the patient receives an SMS notification.

Revision ID: 0009
Revises: 0008
Create Date: 2026-06-19
"""
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS appointment_tokens (
            id             SERIAL PRIMARY KEY,
            appointment_id UUID NOT NULL REFERENCES appointments(id) ON DELETE CASCADE,
            hospital_id    INTEGER NOT NULL REFERENCES hospitals(id) ON DELETE CASCADE,
            department_id  INTEGER NOT NULL REFERENCES clinics(id) ON DELETE CASCADE,
            doctor_id      INTEGER REFERENCES doctors(id) ON DELETE SET NULL,
            token_date     DATE NOT NULL,
            token_number   INTEGER NOT NULL,
            token_prefix   TEXT NOT NULL DEFAULT 'A',
            status         TEXT NOT NULL DEFAULT 'waiting'
                           CHECK (status IN
                               ('waiting','called','in_progress','completed','skipped')),
            called_at      TIMESTAMPTZ,
            completed_at   TIMESTAMPTZ,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (department_id, token_date, token_number)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_tokens_dept_date_status "
        "ON appointment_tokens (department_id, token_date, status)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS appointment_tokens")
