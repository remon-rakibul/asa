"""One-time phone verification for patient accounts (premium voice calling).

Calling the platform phone number is gated to premium/trial patients matched
by caller-ID against a phone number they verified ONCE via an SMS OTP.
`patient_accounts.phone_verified_at` marks the verification; a partial UNIQUE
index guarantees one verified number maps to exactly one account — otherwise
the same SIM could verify a fresh account each month and farm new free trials
forever. `phone_verifications` holds at most one pending OTP per account
(hashed code, short expiry, attempt counter).

Revision ID: 0028
Revises: 0027
Create Date: 2026-07-13
"""
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE patient_accounts "
        "ADD COLUMN IF NOT EXISTS phone_verified_at TIMESTAMPTZ"
    )
    # One verified number = one account (trial-farming guard). Partial, so
    # unverified duplicates (family members typing the same contact number at
    # signup) stay allowed.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_patient_accounts_verified_phone "
        "ON patient_accounts (phone) WHERE phone_verified_at IS NOT NULL"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS phone_verifications (
            account_id  INTEGER PRIMARY KEY
                        REFERENCES patient_accounts(id) ON DELETE CASCADE,
            phone       TEXT NOT NULL,
            code_hash   TEXT NOT NULL,
            expires_at  TIMESTAMPTZ NOT NULL,
            attempts    INTEGER NOT NULL DEFAULT 0,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS phone_verifications")
    op.execute("DROP INDEX IF EXISTS uq_patient_accounts_verified_phone")
    op.execute("ALTER TABLE patient_accounts DROP COLUMN IF EXISTS phone_verified_at")
