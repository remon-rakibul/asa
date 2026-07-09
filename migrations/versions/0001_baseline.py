"""baseline schema (doctor_schedule + appointments)

Idempotent (IF NOT EXISTS) so it is a safe no-op on databases that already
contain the original single-tenant schema.

Revision ID: 0001
Revises:
Create Date: 2026-06-18
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS doctor_schedule (
            id            SERIAL PRIMARY KEY,
            day_of_week   SMALLINT NOT NULL UNIQUE,
            start_time    TIME NOT NULL,
            end_time      TIME NOT NULL,
            slot_duration INTEGER NOT NULL DEFAULT 30,
            CONSTRAINT day_of_week_range CHECK (day_of_week BETWEEN 0 AND 6),
            CONSTRAINT valid_time_range  CHECK (end_time > start_time),
            CONSTRAINT positive_duration CHECK (slot_duration > 0)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS appointments (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            patient_name     TEXT NOT NULL,
            patient_age      INTEGER NOT NULL,
            patient_mobile   TEXT NOT NULL,
            scheduled_at     TIMESTAMPTZ NOT NULL,
            duration_mins    INTEGER NOT NULL DEFAULT 30,
            status           TEXT NOT NULL DEFAULT 'confirmed',
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            reminder_sent_at TIMESTAMPTZ DEFAULT NULL,
            CONSTRAINT valid_status CHECK (status IN ('confirmed', 'cancelled')),
            CONSTRAINT positive_age CHECK (patient_age > 0 AND patient_age < 130)
        )
        """
    )

    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_appointments_scheduled "
        "ON appointments (scheduled_at) WHERE status = 'confirmed'"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_confirmed_slot "
        "ON appointments (scheduled_at) WHERE status = 'confirmed'"
    )

    op.execute(
        """
        INSERT INTO doctor_schedule (day_of_week, start_time, end_time, slot_duration)
        VALUES (0,'09:00','17:00',30),(1,'09:00','17:00',30),(2,'09:00','17:00',30),
               (3,'09:00','17:00',30),(4,'09:00','17:00',30)
        ON CONFLICT (day_of_week) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS appointments")
    op.execute("DROP TABLE IF EXISTS doctor_schedule")
