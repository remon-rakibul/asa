"""Appointment lifecycle statuses + change-history events.

Staff need to record what actually happens to a booking, not just confirmed/
cancelled. This migration:

- Widens the appointments status CHECK to add `checked_in`, `completed`, and
  `no_show` (keeping `confirmed` default and `cancelled`).
- Adds lifecycle timestamps + actor attribution columns: checked_in_at,
  completed_at, cancelled_at, cancel_reason, updated_at, updated_by.
- Adds `appointment_events`: an append-only timeline of every transition
  (created / checked_in / completed / no_show / rescheduled / cancelled) with
  the actor (user) and, for reschedules, the old->new time. This is what powers
  the activity timeline in the appointment drawer and the no-show analytics.

Revision ID: 0019
Revises: 0018
Create Date: 2026-06-28
"""
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # Widen the status CHECK constraint to the full lifecycle.             #
    # ------------------------------------------------------------------ #
    op.execute("ALTER TABLE appointments DROP CONSTRAINT IF EXISTS valid_status")
    op.execute(
        "ALTER TABLE appointments ADD CONSTRAINT valid_status CHECK ("
        "status IN ('confirmed','checked_in','completed','no_show','cancelled')"
        ")"
    )

    # ------------------------------------------------------------------ #
    # Lifecycle timestamps + actor attribution.                           #
    # ------------------------------------------------------------------ #
    for col, ddl in (
        ("checked_in_at", "TIMESTAMPTZ"),
        ("completed_at", "TIMESTAMPTZ"),
        ("cancelled_at", "TIMESTAMPTZ"),
        ("cancel_reason", "TEXT"),
        ("updated_at", "TIMESTAMPTZ"),
        ("updated_by", "INTEGER REFERENCES users(id) ON DELETE SET NULL"),
    ):
        op.execute(
            f"ALTER TABLE appointments ADD COLUMN IF NOT EXISTS {col} {ddl}"
        )

    # ------------------------------------------------------------------ #
    # appointment_events — append-only change history.                    #
    # ------------------------------------------------------------------ #
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS appointment_events (
            id             BIGSERIAL PRIMARY KEY,
            appointment_id UUID NOT NULL REFERENCES appointments(id) ON DELETE CASCADE,
            clinic_id      INTEGER REFERENCES clinics(id) ON DELETE SET NULL,
            event_type     TEXT NOT NULL,
            from_status    TEXT,
            to_status      TEXT,
            from_time      TIMESTAMPTZ,
            to_time        TIMESTAMPTZ,
            actor_user_id  INTEGER REFERENCES users(id) ON DELETE SET NULL,
            actor_role     TEXT NOT NULL DEFAULT '',
            note           TEXT,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_appt_events_appt_created "
        "ON appointment_events (appointment_id, created_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS appointment_events")
    for col in (
        "updated_by", "updated_at", "cancel_reason", "cancelled_at",
        "completed_at", "checked_in_at",
    ):
        op.execute(f"ALTER TABLE appointments DROP COLUMN IF EXISTS {col}")
    # Restore the original binary status constraint. Any rows in the new
    # statuses are normalised so the tighter constraint can be re-applied.
    op.execute(
        "UPDATE appointments SET status = 'confirmed' "
        "WHERE status IN ('checked_in','completed','no_show')"
    )
    op.execute("ALTER TABLE appointments DROP CONSTRAINT IF EXISTS valid_status")
    op.execute(
        "ALTER TABLE appointments ADD CONSTRAINT valid_status CHECK ("
        "status IN ('confirmed','cancelled')"
        ")"
    )
