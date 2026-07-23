"""conversation_log + escalations follow the unified patient thread

Patient portal threads are now ONE conversation per account across ALL
hospitals (pt-acc{N}-platform) — a single thread can carry turns logged
under different clinic_id values as the patient moves between hospitals,
and turns before any department is chosen have no clinic_id at all.

- `conversation_log.clinic_id` becomes nullable (a platform-mode turn with
  no department yet has no clinic) and gains `hospital_id` so a hospital
  admin can see their own slice of a cross-hospital thread even when no
  clinic value applies yet.
- `escalations.hospital_id` lets `request_human_help` route a
  platform-wide question (no clinic/hospital chosen) or a hospital-level
  one straight to that hospital's queue, instead of guessing a department.

Revision ID: 0025
Revises: 0024
Create Date: 2026-07-12
"""
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE conversation_log ALTER COLUMN clinic_id DROP NOT NULL")
    op.execute(
        "ALTER TABLE conversation_log ADD COLUMN IF NOT EXISTS hospital_id "
        "INTEGER REFERENCES hospitals(id) ON DELETE SET NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_convlog_session "
        "ON conversation_log (session_id, created_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_convlog_hospital "
        "ON conversation_log (hospital_id, session_id, created_at)"
    )

    op.execute(
        "ALTER TABLE escalations ADD COLUMN IF NOT EXISTS hospital_id "
        "INTEGER REFERENCES hospitals(id) ON DELETE CASCADE"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_escalations_hospital_status "
        "ON escalations (hospital_id, status)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_escalations_hospital_status")
    op.execute("ALTER TABLE escalations DROP COLUMN IF EXISTS hospital_id")
    op.execute("DROP INDEX IF EXISTS ix_convlog_hospital")
    op.execute("DROP INDEX IF EXISTS ix_convlog_session")
    op.execute("ALTER TABLE conversation_log DROP COLUMN IF EXISTS hospital_id")
    # Nullability is not restored on downgrade (existing NULL rows would violate
    # the NOT NULL constraint); acceptable for this dev-stage migration chain.
