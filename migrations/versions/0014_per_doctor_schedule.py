"""Add doctor_id to doctor_schedule for per-doctor scheduling.

Allows each doctor in a department to have their own weekly schedule.
Existing clinic-level schedule rows (doctor_id=NULL) continue to work unchanged.

Revision ID: 0014
Revises: 0013
Create Date: 2026-06-24
"""
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add nullable doctor_id FK — NULL means "clinic-level" (backward compat)
    op.execute(
        "ALTER TABLE doctor_schedule ADD COLUMN IF NOT EXISTS doctor_id INTEGER "
        "REFERENCES doctors(id) ON DELETE SET NULL"
    )

    # Replace the old (clinic_id, day_of_week) unique constraint with a
    # (clinic_id, doctor_id, day_of_week) one so multiple doctors can each have
    # their own schedule row per weekday.
    op.execute(
        "ALTER TABLE doctor_schedule DROP CONSTRAINT IF EXISTS uq_schedule_clinic_day"
    )
    op.execute(
        "ALTER TABLE doctor_schedule ADD CONSTRAINT uq_schedule_doctor_day "
        "UNIQUE (clinic_id, doctor_id, day_of_week)"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE doctor_schedule DROP CONSTRAINT IF EXISTS uq_schedule_doctor_day"
    )
    # Re-add old constraint only for NULL doctor rows (clinic-level schedule)
    op.execute(
        "ALTER TABLE doctor_schedule ADD CONSTRAINT uq_schedule_clinic_day "
        "UNIQUE (clinic_id, day_of_week)"
    )
    op.execute("ALTER TABLE doctor_schedule DROP COLUMN IF EXISTS doctor_id")
