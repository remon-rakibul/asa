"""multi-tenancy: clinics, users, channels + clinic_id on existing tables

Existing rows are backfilled to a seeded 'default' clinic (created from the
current CLINIC_NAME / DOCTOR_NAME / DOCTOR_PHONE settings) so no data is lost.

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-18
"""
from alembic import op
from sqlalchemy import text

from config import settings

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS clinics (
            id                       SERIAL PRIMARY KEY,
            slug                     TEXT UNIQUE NOT NULL,
            name                     TEXT NOT NULL,
            doctor_name              TEXT NOT NULL DEFAULT 'Doctor',
            doctor_phone             TEXT NOT NULL DEFAULT '',
            timezone                 TEXT NOT NULL DEFAULT 'Asia/Dhaka',
            availability_days_ahead  INTEGER NOT NULL DEFAULT 7,
            status                   TEXT NOT NULL DEFAULT 'active',
            created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT valid_clinic_status CHECK (status IN ('active','suspended'))
        )
        """
    )

    # Seed the default clinic from current single-tenant settings.
    op.get_bind().execute(
        text(
            "INSERT INTO clinics (slug, name, doctor_name, doctor_phone, availability_days_ahead) "
            "VALUES ('default', :name, :doctor, :phone, :days) "
            "ON CONFLICT (slug) DO NOTHING"
        ),
        {
            "name": settings.clinic_name,
            "doctor": settings.doctor_name,
            "phone": settings.doctor_phone,
            "days": settings.availability_days_ahead,
        },
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id            SERIAL PRIMARY KEY,
            clinic_id     INTEGER NOT NULL REFERENCES clinics(id) ON DELETE CASCADE,
            email         TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role          TEXT NOT NULL DEFAULT 'admin',
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS channels (
            id         SERIAL PRIMARY KEY,
            clinic_id  INTEGER NOT NULL REFERENCES clinics(id) ON DELETE CASCADE,
            kind       TEXT NOT NULL,
            identifier TEXT NOT NULL,
            CONSTRAINT valid_channel_kind CHECK (kind IN ('whatsapp','sms','voice_sip','web')),
            UNIQUE (kind, identifier)
        )
        """
    )

    # --- doctor_schedule: add clinic_id, backfill, re-key uniqueness ---
    op.execute("ALTER TABLE doctor_schedule ADD COLUMN IF NOT EXISTS clinic_id INTEGER")
    op.execute(
        "UPDATE doctor_schedule SET clinic_id = (SELECT id FROM clinics WHERE slug='default') "
        "WHERE clinic_id IS NULL"
    )
    op.execute("ALTER TABLE doctor_schedule ALTER COLUMN clinic_id SET NOT NULL")
    op.execute("ALTER TABLE doctor_schedule DROP CONSTRAINT IF EXISTS doctor_schedule_day_of_week_key")
    op.execute(
        "ALTER TABLE doctor_schedule ADD CONSTRAINT uq_schedule_clinic_day "
        "UNIQUE (clinic_id, day_of_week)"
    )
    op.execute(
        "ALTER TABLE doctor_schedule ADD CONSTRAINT fk_schedule_clinic "
        "FOREIGN KEY (clinic_id) REFERENCES clinics(id) ON DELETE CASCADE"
    )

    # --- appointments: add clinic_id, backfill, re-key uniqueness/index ---
    op.execute("ALTER TABLE appointments ADD COLUMN IF NOT EXISTS clinic_id INTEGER")
    op.execute(
        "UPDATE appointments SET clinic_id = (SELECT id FROM clinics WHERE slug='default') "
        "WHERE clinic_id IS NULL"
    )
    op.execute("ALTER TABLE appointments ALTER COLUMN clinic_id SET NOT NULL")
    op.execute(
        "ALTER TABLE appointments ADD CONSTRAINT fk_appt_clinic "
        "FOREIGN KEY (clinic_id) REFERENCES clinics(id) ON DELETE CASCADE"
    )
    op.execute("DROP INDEX IF EXISTS uq_confirmed_slot")
    op.execute(
        "CREATE UNIQUE INDEX uq_confirmed_slot ON appointments (clinic_id, scheduled_at) "
        "WHERE status = 'confirmed'"
    )
    op.execute("DROP INDEX IF EXISTS idx_appointments_scheduled")
    op.execute(
        "CREATE INDEX idx_appointments_scheduled ON appointments (clinic_id, scheduled_at) "
        "WHERE status = 'confirmed'"
    )

    # Register a default web channel for the seeded clinic.
    op.execute(
        "INSERT INTO channels (clinic_id, kind, identifier) "
        "SELECT id, 'web', 'default' FROM clinics WHERE slug='default' "
        "ON CONFLICT (kind, identifier) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_confirmed_slot")
    op.execute("DROP INDEX IF EXISTS idx_appointments_scheduled")
    op.execute("ALTER TABLE appointments DROP CONSTRAINT IF EXISTS fk_appt_clinic")
    op.execute("ALTER TABLE appointments DROP COLUMN IF EXISTS clinic_id")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_confirmed_slot "
        "ON appointments (scheduled_at) WHERE status = 'confirmed'"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_appointments_scheduled "
        "ON appointments (scheduled_at) WHERE status = 'confirmed'"
    )
    op.execute("ALTER TABLE doctor_schedule DROP CONSTRAINT IF EXISTS fk_schedule_clinic")
    op.execute("ALTER TABLE doctor_schedule DROP CONSTRAINT IF EXISTS uq_schedule_clinic_day")
    op.execute("ALTER TABLE doctor_schedule DROP COLUMN IF EXISTS clinic_id")
    op.execute("DROP TABLE IF EXISTS channels")
    op.execute("DROP TABLE IF EXISTS users")
    op.execute("DROP TABLE IF EXISTS clinics")
