"""Payments (booking fees + patient subscriptions) + hospital billing.

- `appointments` gains a `pending_payment` status (a slot HELD while the
  patient completes payment, before it counts as `confirmed`) and
  `payment_expires_at` (the hold's TTL). The confirmed-slot uniqueness index
  and the "open" scheduled-slots index both widen to include unexpired
  pending holds, so two patients can't be sold the same time.
- `payments`: one row per payment attempt (a booking fee or a patient
  subscription period), provider-agnostic (manual or a real gateway like
  SSLCommerz), with an idempotency-safe status machine.
- `hospitals` gains `booking_fee` (NULL = platform default, 0 = free) and
  `billing_status` — the single choke point that hides a lapsed hospital's
  doctors from patient search/booking ("block hospitals only").
- `hospital_subscriptions` / `subscription_invoices`: the monthly billing
  relationship with each hospital, invoiced and marked paid by platform
  admins (a real payment gateway is optional here — see tools/payments.py).

Revision ID: 0026
Revises: 0025
Create Date: 2026-07-12
"""
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # appointments: pending_payment status + hold expiry.                  #
    # ------------------------------------------------------------------ #
    op.execute("ALTER TABLE appointments DROP CONSTRAINT IF EXISTS valid_status")
    op.execute(
        "ALTER TABLE appointments ADD CONSTRAINT valid_status CHECK ("
        "status IN ('pending_payment','confirmed','checked_in','completed','no_show','cancelled')"
        ")"
    )
    op.execute(
        "ALTER TABLE appointments ADD COLUMN IF NOT EXISTS payment_expires_at TIMESTAMPTZ"
    )

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
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_appt_pending_expiry ON appointments (payment_expires_at) "
        "WHERE status = 'pending_payment'"
    )

    # ------------------------------------------------------------------ #
    # payments — booking fees + patient subscription periods.             #
    # ------------------------------------------------------------------ #
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS payments (
            id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            kind           TEXT NOT NULL CHECK (kind IN ('booking_fee','patient_subscription')),
            appointment_id UUID REFERENCES appointments(id) ON DELETE SET NULL,
            account_id     INTEGER REFERENCES patient_accounts(id) ON DELETE SET NULL,
            hospital_id    INTEGER REFERENCES hospitals(id) ON DELETE SET NULL,
            amount         INTEGER NOT NULL,
            currency       TEXT NOT NULL DEFAULT 'BDT',
            provider       TEXT NOT NULL,
            provider_ref   TEXT,
            val_id         TEXT,
            status         TEXT NOT NULL DEFAULT 'initiated'
                           CHECK (status IN ('initiated','paid','failed','refunded','expired')),
            raw            JSONB,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            paid_at        TIMESTAMPTZ
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_payments_appt ON payments (appointment_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_payments_account ON payments (account_id)")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_payments_ref ON payments (provider, provider_ref) "
        "WHERE provider_ref IS NOT NULL"
    )

    # ------------------------------------------------------------------ #
    # hospital billing.                                                    #
    # ------------------------------------------------------------------ #
    op.execute("ALTER TABLE hospitals ADD COLUMN IF NOT EXISTS booking_fee INTEGER")
    op.execute(
        "ALTER TABLE hospitals ADD COLUMN IF NOT EXISTS billing_status TEXT "
        "NOT NULL DEFAULT 'active' "
        "CHECK (billing_status IN ('active','past_due','suspended'))"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS hospital_subscriptions (
            hospital_id          INTEGER PRIMARY KEY REFERENCES hospitals(id) ON DELETE CASCADE,
            monthly_fee          INTEGER NOT NULL DEFAULT 0,
            status                TEXT NOT NULL DEFAULT 'active'
                                  CHECK (status IN ('active','past_due','suspended')),
            current_period_start  TIMESTAMPTZ,
            current_period_end    TIMESTAMPTZ,
            grace_days            INTEGER NOT NULL DEFAULT 7,
            created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS subscription_invoices (
            id            SERIAL PRIMARY KEY,
            hospital_id   INTEGER NOT NULL REFERENCES hospitals(id) ON DELETE CASCADE,
            period_start  DATE NOT NULL,
            period_end    DATE NOT NULL,
            amount        INTEGER NOT NULL,
            status        TEXT NOT NULL DEFAULT 'due' CHECK (status IN ('due','paid','void')),
            method        TEXT,
            note          TEXT,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            paid_at       TIMESTAMPTZ
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_sub_invoices_hospital "
        "ON subscription_invoices (hospital_id, status)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_sub_invoices_hospital")
    op.execute("DROP TABLE IF EXISTS subscription_invoices")
    op.execute("DROP TABLE IF EXISTS hospital_subscriptions")
    op.execute("ALTER TABLE hospitals DROP COLUMN IF EXISTS billing_status")
    op.execute("ALTER TABLE hospitals DROP COLUMN IF EXISTS booking_fee")

    op.execute("DROP INDEX IF EXISTS ux_payments_ref")
    op.execute("DROP TABLE IF EXISTS payments")

    op.execute("DROP INDEX IF EXISTS ix_appt_pending_expiry")
    op.execute("DROP INDEX IF EXISTS idx_appointments_scheduled")
    op.execute("DROP INDEX IF EXISTS uq_confirmed_slot")
    # Normalise pending_payment rows before re-tightening the CHECK.
    op.execute("UPDATE appointments SET status = 'cancelled' WHERE status = 'pending_payment'")
    op.execute("ALTER TABLE appointments DROP CONSTRAINT IF EXISTS valid_status")
    op.execute(
        "ALTER TABLE appointments ADD CONSTRAINT valid_status CHECK ("
        "status IN ('confirmed','checked_in','completed','no_show','cancelled')"
        ")"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_confirmed_slot ON appointments (clinic_id, scheduled_at) "
        "WHERE status = 'confirmed'"
    )
    op.execute(
        "CREATE INDEX idx_appointments_scheduled ON appointments (clinic_id, scheduled_at) "
        "WHERE status = 'confirmed'"
    )
    op.execute("ALTER TABLE appointments DROP COLUMN IF EXISTS payment_expires_at")
