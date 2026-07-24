"""Hospital prepaid credit wallet (pass-through usage metering).

Hospitals keep their monthly base subscription (0026) and additionally load a
prepaid credit wallet that is drawn down per billable event — a confirmed
booking plus the variable channel costs (SMS, voice minutes, WhatsApp). Credits
are priced per-hospital (a negotiated ৳/credit rate) and never expire. A wallet
may go negative (bookings are never blocked); a hospital whose debt crosses a
threshold is hidden from the marketplace via the new `hospitals.wallet_status`,
kept separate from `billing_status` so the subscription sweep and the wallet
sweep never fight over one column.

- `hospital_wallets`: one row per hospital — cached balance + per-hospital rate.
- `wallet_ledger`: append-only source of truth; one row per debit/credit, with
  an idempotency key so a booking (or a replayed payment IPN) charges once.
- `payments`: `kind` gains `'credit_topup'`; a nullable `credits` column records
  how many credits a top-up buys (read at confirm time).
- `hospitals`: gains `wallet_status` ('ok' | 'suspended').

Revision ID: 0030
Revises: 0029
Create Date: 2026-07-24
"""
from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # hospitals: wallet visibility flag (separate from billing_status).    #
    # ------------------------------------------------------------------ #
    op.execute(
        "ALTER TABLE hospitals ADD COLUMN IF NOT EXISTS wallet_status TEXT "
        "NOT NULL DEFAULT 'ok' CHECK (wallet_status IN ('ok','suspended'))"
    )

    # ------------------------------------------------------------------ #
    # payments: credit top-up kind + credits purchased.                    #
    # ------------------------------------------------------------------ #
    op.execute("ALTER TABLE payments DROP CONSTRAINT IF EXISTS payments_kind_check")
    op.execute(
        "ALTER TABLE payments ADD CONSTRAINT payments_kind_check "
        "CHECK (kind IN ('booking_fee','patient_subscription','credit_topup'))"
    )
    op.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS credits INTEGER")

    # ------------------------------------------------------------------ #
    # hospital_wallets — cached balance + per-hospital ৳/credit rate.      #
    # ------------------------------------------------------------------ #
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS hospital_wallets (
            hospital_id      INTEGER PRIMARY KEY REFERENCES hospitals(id) ON DELETE CASCADE,
            balance          INTEGER NOT NULL DEFAULT 0,
            credit_rate_bdt  NUMERIC(10,2) NOT NULL DEFAULT 20.00,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    # ------------------------------------------------------------------ #
    # wallet_ledger — append-only audit + source of truth.                 #
    # ------------------------------------------------------------------ #
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS wallet_ledger (
            id               BIGSERIAL PRIMARY KEY,
            hospital_id      INTEGER NOT NULL REFERENCES hospitals(id) ON DELETE CASCADE,
            delta            INTEGER NOT NULL,
            balance_after    INTEGER NOT NULL,
            reason           TEXT NOT NULL CHECK (reason IN
                             ('booking','sms','voice','whatsapp','topup','grant','adjustment','refund')),
            quantity         INTEGER NOT NULL DEFAULT 1,
            clinic_id        INTEGER,
            appointment_id   UUID REFERENCES appointments(id) ON DELETE SET NULL,
            payment_id       UUID REFERENCES payments(id) ON DELETE SET NULL,
            idempotency_key  TEXT,
            note             TEXT,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_wallet_ledger_idem "
        "ON wallet_ledger (idempotency_key) WHERE idempotency_key IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_wallet_ledger_hospital "
        "ON wallet_ledger (hospital_id, created_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_wallet_ledger_hospital")
    op.execute("DROP INDEX IF EXISTS ux_wallet_ledger_idem")
    op.execute("DROP TABLE IF EXISTS wallet_ledger")
    op.execute("DROP TABLE IF EXISTS hospital_wallets")

    op.execute("ALTER TABLE payments DROP COLUMN IF EXISTS credits")
    op.execute("ALTER TABLE payments DROP CONSTRAINT IF EXISTS payments_kind_check")
    # Normalise any credit_topup rows before re-tightening the CHECK.
    op.execute("UPDATE payments SET kind = 'booking_fee' WHERE kind = 'credit_topup'")
    op.execute(
        "ALTER TABLE payments ADD CONSTRAINT payments_kind_check "
        "CHECK (kind IN ('booking_fee','patient_subscription'))"
    )

    op.execute("ALTER TABLE hospitals DROP COLUMN IF EXISTS wallet_status")
