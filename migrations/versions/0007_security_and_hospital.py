"""Hospital data model + RBAC audit log + appointment extensions.

Merges the previous two 0007 files (security_and_scalability + hospital_model)
that shared the same revision ID and created a fork.

Includes:
  - hospitals table (top-level tenant for multi-department mode)
  - patients table with MRN (hospital-registered patient identity)
  - hospital_id / specialty_code / floor / phone_ext on clinics
  - audit_log table (immutable admin action trail)
  - channels CHECK constraint fix
  - patient_id / doctor_id / appointment_type / consent_at on appointments

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-19
"""
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- hospitals ---
    op.execute("""
        CREATE TABLE IF NOT EXISTS hospitals (
            id              SERIAL PRIMARY KEY,
            slug            TEXT UNIQUE NOT NULL,
            name            TEXT NOT NULL,
            address         TEXT NOT NULL DEFAULT '',
            license_number  TEXT NOT NULL DEFAULT '',
            timezone        TEXT NOT NULL DEFAULT 'Asia/Dhaka',
            status          TEXT NOT NULL DEFAULT 'active',
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute(
        "ALTER TABLE clinics ADD COLUMN IF NOT EXISTS "
        "hospital_id INTEGER REFERENCES hospitals(id) ON DELETE SET NULL"
    )
    op.execute(
        "ALTER TABLE clinics ADD COLUMN IF NOT EXISTS "
        "specialty_code TEXT NOT NULL DEFAULT ''"
    )
    op.execute(
        "ALTER TABLE clinics ADD COLUMN IF NOT EXISTS floor TEXT NOT NULL DEFAULT ''"
    )
    op.execute(
        "ALTER TABLE clinics ADD COLUMN IF NOT EXISTS phone_ext TEXT NOT NULL DEFAULT ''"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_clinics_hospital_id ON clinics (hospital_id)")

    # --- patients ---
    op.execute("""
        CREATE TABLE IF NOT EXISTS patients (
            id          SERIAL PRIMARY KEY,
            hospital_id INTEGER NOT NULL REFERENCES hospitals(id) ON DELETE CASCADE,
            mrn         TEXT NOT NULL,
            name        TEXT NOT NULL DEFAULT '',
            phone       TEXT NOT NULL DEFAULT '',
            age         INTEGER,
            gender      TEXT CHECK (gender IN ('male', 'female', 'other')) DEFAULT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (hospital_id, mrn)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_patients_hospital_phone "
        "ON patients (hospital_id, phone)"
    )

    # --- audit_log ---
    op.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id          BIGSERIAL PRIMARY KEY,
            hospital_id INTEGER,
            clinic_id   INTEGER REFERENCES clinics(id) ON DELETE SET NULL,
            user_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
            actor_role  TEXT NOT NULL DEFAULT '',
            action      TEXT NOT NULL,
            entity_type TEXT NOT NULL DEFAULT '',
            entity_id   TEXT,
            old_value   TEXT,
            new_value   TEXT,
            ip_address  TEXT,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_audit_clinic_time "
        "ON audit_log (clinic_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_audit_hospital_time "
        "ON audit_log (hospital_id, created_at DESC)"
    )

    # --- channels: expand kind CHECK ---
    op.execute("ALTER TABLE channels DROP CONSTRAINT IF EXISTS valid_channel_kind")
    op.execute(
        "ALTER TABLE channels ADD CONSTRAINT valid_channel_kind "
        "CHECK (kind IN ('whatsapp','sms','voice_sip','web','voice','phone'))"
    )

    # --- appointments: hospital-mode columns ---
    op.execute(
        "ALTER TABLE appointments ADD COLUMN IF NOT EXISTS patient_id INTEGER "
        "REFERENCES patients(id) ON DELETE SET NULL"
    )
    op.execute(
        "ALTER TABLE appointments ADD COLUMN IF NOT EXISTS doctor_id INTEGER "
        "REFERENCES doctors(id) ON DELETE SET NULL"
    )
    op.execute(
        "ALTER TABLE appointments ADD COLUMN IF NOT EXISTS appointment_type TEXT "
        "NOT NULL DEFAULT 'opd' "
        "CHECK (appointment_type IN ('opd', 'follow_up', 'diagnostic', 'emergency'))"
    )
    op.execute(
        "ALTER TABLE appointments ADD COLUMN IF NOT EXISTS consent_at TIMESTAMPTZ DEFAULT NULL"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE appointments DROP COLUMN IF EXISTS consent_at")
    op.execute("ALTER TABLE appointments DROP COLUMN IF EXISTS appointment_type")
    op.execute("ALTER TABLE appointments DROP COLUMN IF EXISTS doctor_id")
    op.execute("ALTER TABLE appointments DROP COLUMN IF EXISTS patient_id")
    op.execute(
        "ALTER TABLE channels DROP CONSTRAINT IF EXISTS valid_channel_kind"
    )
    op.execute(
        "ALTER TABLE channels ADD CONSTRAINT valid_channel_kind "
        "CHECK (kind IN ('whatsapp','sms','voice_sip','web'))"
    )
    op.execute("DROP INDEX IF EXISTS ix_audit_hospital_time")
    op.execute("DROP INDEX IF EXISTS ix_audit_clinic_time")
    op.execute("DROP TABLE IF EXISTS audit_log")
    op.execute("DROP INDEX IF EXISTS ix_patients_hospital_phone")
    op.execute("DROP TABLE IF EXISTS patients")
    op.execute("DROP INDEX IF EXISTS ix_clinics_hospital_id")
    op.execute("ALTER TABLE clinics DROP COLUMN IF EXISTS phone_ext")
    op.execute("ALTER TABLE clinics DROP COLUMN IF EXISTS floor")
    op.execute("ALTER TABLE clinics DROP COLUMN IF EXISTS specialty_code")
    op.execute("ALTER TABLE clinics DROP COLUMN IF EXISTS hospital_id")
    op.execute("DROP TABLE IF EXISTS hospitals")
