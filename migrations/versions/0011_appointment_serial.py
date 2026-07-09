"""Add serial_number to appointments.

serial_number is a sequential integer per clinic per day (1, 2, 3…)
assigned at booking time, determining the order patients are seen.

Revision ID: 0011
Revises: 0010
Create Date: 2026-06-20
"""
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE appointments "
        "ADD COLUMN IF NOT EXISTS serial_number INTEGER"
    )

    # Backfill serial_number for existing appointments per clinic+date,
    # ordered by booking time (created_at). Only for rows still NULL.
    op.execute("""
        WITH numbered AS (
            SELECT id, ROW_NUMBER() OVER (
                PARTITION BY clinic_id, scheduled_at::date
                ORDER BY created_at
            ) AS rn
            FROM appointments
            WHERE serial_number IS NULL
        )
        UPDATE appointments a
        SET serial_number = n.rn
        FROM numbered n
        WHERE a.id = n.id
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE appointments DROP COLUMN IF EXISTS serial_number")
