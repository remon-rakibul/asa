"""RBAC roles + audit log.

Adds:
  - hospital_id / department_id columns on users
  - users.role migrated from open text to a constrained enum
    (existing 'admin' rows become 'hospital_admin')
  - audit_log table (append-only; no DELETE privilege in app code)

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-19
"""
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # users — add hospital / department scoping and enforce role enum      #
    # ------------------------------------------------------------------ #
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
        "hospital_id INTEGER REFERENCES hospitals(id) ON DELETE CASCADE"
    )
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
        "department_id INTEGER REFERENCES clinics(id) ON DELETE SET NULL"
    )
    # Migrate legacy 'admin' role to hospital_admin before adding the constraint.
    op.execute("UPDATE users SET role = 'hospital_admin' WHERE role = 'admin'")
    op.execute(
        "ALTER TABLE users ADD CONSTRAINT valid_role CHECK ("
        "role IN ('platform_admin','hospital_admin','dept_head','receptionist','doctor')"
        ")"
    )

    # ------------------------------------------------------------------ #
    # audit_log — immutable record of every admin write action             #
    # ------------------------------------------------------------------ #
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            id           BIGSERIAL PRIMARY KEY,
            hospital_id  INTEGER REFERENCES hospitals(id) ON DELETE SET NULL,
            clinic_id    INTEGER REFERENCES clinics(id) ON DELETE SET NULL,
            user_id      INTEGER REFERENCES users(id) ON DELETE SET NULL,
            actor_role   TEXT NOT NULL DEFAULT '',
            action       TEXT NOT NULL,
            entity_type  TEXT NOT NULL,
            entity_id    TEXT,
            old_value    JSONB,
            new_value    JSONB,
            ip_address   TEXT,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_audit_hospital_created "
        "ON audit_log (hospital_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_audit_clinic_created "
        "ON audit_log (clinic_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_audit_entity "
        "ON audit_log (entity_type, entity_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS audit_log")
    op.execute(
        "ALTER TABLE users DROP CONSTRAINT IF EXISTS valid_role"
    )
    op.execute("UPDATE users SET role = 'admin' WHERE role = 'hospital_admin'")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS department_id")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS hospital_id")
