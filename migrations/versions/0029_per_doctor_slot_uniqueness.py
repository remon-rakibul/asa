"""Make the slot-uniqueness index per-doctor, not per-clinic.

The old `uq_confirmed_slot` was `(clinic_id, scheduled_at)` — one appointment
per clinic per instant. That was correct for the single-doctor pilot, but the
product is now a multi-doctor marketplace: availability is computed PER DOCTOR
(`get_available_slots(doctor_id=...)` only subtracts that doctor's bookings),
so two different doctors at the same hospital legitimately have open 10:00 AM
slots. Under the old index the second booking collided on `(clinic, time)` and
was wrongly reported as a race-loss / "slot taken", making concurrent doctors
unbookable.

Fix: key the index on `(clinic_id, COALESCE(doctor_id, 0), scheduled_at)`.
  * Real doctors (id >= 1, SERIAL) each get their own one-per-time guarantee.
  * Clinic-level slots (doctor_id IS NULL) collapse to the `0` bucket, so the
    old single-appointment-per-clinic-time behavior is preserved for them.

This new index is strictly LOOSER than the old one (any rows unique on
`(clinic, time)` are automatically unique on `(clinic, doctor, time)`), so the
CREATE can never fail on data that satisfied the previous constraint.

Revision ID: 0029
Revises: 0028
Create Date: 2026-07-23
"""
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_confirmed_slot")
    op.execute(
        "CREATE UNIQUE INDEX uq_confirmed_slot "
        "ON appointments (clinic_id, COALESCE(doctor_id, 0), scheduled_at) "
        "WHERE status IN ('confirmed', 'pending_payment')"
    )
    # Keep the read index aligned with the new key so availability lookups
    # (which filter by clinic_id + doctor_id) stay index-covered.
    op.execute("DROP INDEX IF EXISTS idx_appointments_scheduled")
    op.execute(
        "CREATE INDEX idx_appointments_scheduled "
        "ON appointments (clinic_id, COALESCE(doctor_id, 0), scheduled_at) "
        "WHERE status IN ('confirmed', 'pending_payment')"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_confirmed_slot")
    op.execute(
        "CREATE UNIQUE INDEX uq_confirmed_slot ON appointments (clinic_id, scheduled_at) "
        "WHERE status IN ('confirmed', 'pending_payment')"
    )
    op.execute("DROP INDEX IF EXISTS idx_appointments_scheduled")
    op.execute(
        "CREATE INDEX idx_appointments_scheduled ON appointments (clinic_id, scheduled_at) "
        "WHERE status IN ('confirmed', 'pending_payment')"
    )
