"""multiple doctors per clinic

Adds a `doctors` roster table. The existing single clinics.doctor_name/phone are
backfilled as each clinic's primary doctor. The agent + notification SMS keep
reading clinics.doctor_name/phone, which we now MIRROR from the primary doctor
(see tools.database._resync_primary) — so one editable source (doctors) drives
the clinic-level fields the agent already uses.

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-19
"""
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS doctors (
            id          SERIAL PRIMARY KEY,
            clinic_id   INTEGER NOT NULL REFERENCES clinics(id) ON DELETE CASCADE,
            name        TEXT NOT NULL,
            specialty   TEXT NOT NULL DEFAULT '',
            phone       TEXT NOT NULL DEFAULT '',
            is_primary  BOOLEAN NOT NULL DEFAULT false,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    # One primary doctor per clinic.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_primary_doctor "
        "ON doctors (clinic_id) WHERE is_primary"
    )
    # Seed each clinic's existing single doctor as its primary (idempotent).
    op.execute(
        """
        INSERT INTO doctors (clinic_id, name, phone, is_primary)
        SELECT c.id, COALESCE(NULLIF(c.doctor_name, ''), 'Doctor'), c.doctor_phone, true
        FROM clinics c
        WHERE NOT EXISTS (SELECT 1 FROM doctors d WHERE d.clinic_id = c.id)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS doctors")
