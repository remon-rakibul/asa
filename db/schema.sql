-- Doctor Appointment Setter — Database Schema (REFERENCE ONLY)
--
-- Alembic migrations in migrations/versions/ are the source of truth. This file
-- mirrors the end state for fresh/docker bootstraps and documentation.
-- Apply migrations with:  alembic upgrade head

-- Needed for gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Tenants. One row per clinic/doctor. A missing channels row means that
-- channel isn't wired to any clinic.
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
);

-- Admin users (clinic staff who log into the dashboard).
CREATE TABLE IF NOT EXISTS users (
    id            SERIAL PRIMARY KEY,
    clinic_id     INTEGER NOT NULL REFERENCES clinics(id) ON DELETE CASCADE,
    email         TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'admin',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Maps a channel identity (WhatsApp number, SIP URI, web key) to a clinic.
CREATE TABLE IF NOT EXISTS channels (
    id         SERIAL PRIMARY KEY,
    clinic_id  INTEGER NOT NULL REFERENCES clinics(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,
    identifier TEXT NOT NULL,
    CONSTRAINT valid_channel_kind CHECK (kind IN ('whatsapp','sms','voice_sip','web')),
    UNIQUE (kind, identifier)
);

-- Doctor's recurring weekly schedule, per clinic. Missing row for a day = off.
CREATE TABLE IF NOT EXISTS doctor_schedule (
    id            SERIAL PRIMARY KEY,
    clinic_id     INTEGER NOT NULL REFERENCES clinics(id) ON DELETE CASCADE,
    day_of_week   SMALLINT NOT NULL,                 -- 0=Mon ... 6=Sun
    start_time    TIME NOT NULL,
    end_time      TIME NOT NULL,
    slot_duration INTEGER NOT NULL DEFAULT 30,
    CONSTRAINT day_of_week_range CHECK (day_of_week BETWEEN 0 AND 6),
    CONSTRAINT valid_time_range  CHECK (end_time > start_time),
    CONSTRAINT positive_duration CHECK (slot_duration > 0),
    CONSTRAINT uq_schedule_clinic_day UNIQUE (clinic_id, day_of_week)
);

-- Booked appointments, per clinic.
CREATE TABLE IF NOT EXISTS appointments (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    clinic_id        INTEGER NOT NULL REFERENCES clinics(id) ON DELETE CASCADE,
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
);

-- Link appointments back to the conversation session that created them.
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS session_id TEXT;

CREATE INDEX IF NOT EXISTS ix_appointments_session_id
    ON appointments (clinic_id, session_id);

-- Per-clinic availability lookup + double-booking guard.
CREATE INDEX IF NOT EXISTS idx_appointments_scheduled
    ON appointments (clinic_id, scheduled_at)
    WHERE status = 'confirmed';

CREATE UNIQUE INDEX IF NOT EXISTS uq_confirmed_slot
    ON appointments (clinic_id, scheduled_at)
    WHERE status = 'confirmed';

-- Tracks which phone number received each call turn in conversation_log.
ALTER TABLE conversation_log ADD COLUMN IF NOT EXISTS channel_identifier TEXT;

CREATE INDEX IF NOT EXISTS ix_conversation_log_channel_ident
    ON conversation_log (clinic_id, channel_identifier);

-- Seed a default clinic + Mon–Fri 09:00–17:00, 30-minute slots.
INSERT INTO clinics (slug, name) VALUES ('default', 'Clinic')
ON CONFLICT (slug) DO NOTHING;

INSERT INTO doctor_schedule (clinic_id, day_of_week, start_time, end_time, slot_duration)
SELECT c.id, d.dow, '09:00', '17:00', 30
FROM clinics c
CROSS JOIN (VALUES (0),(1),(2),(3),(4)) AS d(dow)
WHERE c.slug = 'default'
ON CONFLICT (clinic_id, day_of_week) DO NOTHING;
