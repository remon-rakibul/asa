"""SMS log + editable per-department SMS templates and sender ID.

- Adds `sms_log`: an append-only record of every outbound SMS the system sends
  (booking confirmation, reminder, doctor alert, queue token, agent reply) with
  a local status (sent/failed/skipped) so the console can show SMS tracking.
  Recipient and body are stored encrypted (Fernet), matching the patient-PII
  policy used for appointments.
- Adds `clinics.sms_sender_id` (per-department From/sender override) and
  `clinics.sms_templates` (JSONB map of kind -> editable Bangla template).

Revision ID: 0017
Revises: 0016
Create Date: 2026-06-27
"""
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS sms_log (
            id          BIGSERIAL PRIMARY KEY,
            clinic_id   INTEGER REFERENCES clinics(id) ON DELETE SET NULL,
            to_number   TEXT NOT NULL,
            body        TEXT NOT NULL,
            kind        TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'sent',
            provider    TEXT,
            error       TEXT,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_sms_log_clinic_created "
        "ON sms_log (clinic_id, created_at DESC)"
    )

    op.execute(
        "ALTER TABLE clinics ADD COLUMN IF NOT EXISTS "
        "sms_sender_id TEXT NOT NULL DEFAULT ''"
    )
    op.execute(
        "ALTER TABLE clinics ADD COLUMN IF NOT EXISTS "
        "sms_templates JSONB NOT NULL DEFAULT '{}'::jsonb"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE clinics DROP COLUMN IF EXISTS sms_templates")
    op.execute("ALTER TABLE clinics DROP COLUMN IF EXISTS sms_sender_id")
    op.execute("DROP TABLE IF EXISTS sms_log")
