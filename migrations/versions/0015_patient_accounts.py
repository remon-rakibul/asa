"""Patient self-service accounts (platform-wide patient login).

Adds a `patient_accounts` table holding patient login credentials, kept separate
from the staff `users` table so the RBAC role enum is untouched. A global account
links to the per-hospital `patients` (MRN) rows via patients.account_id; the link
is created the first time the patient books at a given hospital.

Revision ID: 0015
Revises: 0014
Create Date: 2026-06-24
"""
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS patient_accounts (
            id            SERIAL PRIMARY KEY,
            email         TEXT UNIQUE NOT NULL,
            phone         TEXT NOT NULL DEFAULT '',
            name          TEXT NOT NULL DEFAULT '',
            password_hash TEXT NOT NULL,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_patient_accounts_phone "
        "ON patient_accounts (phone)"
    )

    # Link per-hospital patient (MRN) records back to the global account.
    op.execute(
        "ALTER TABLE patients ADD COLUMN IF NOT EXISTS account_id INTEGER "
        "REFERENCES patient_accounts(id) ON DELETE SET NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_patients_account ON patients (account_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_patients_account")
    op.execute("ALTER TABLE patients DROP COLUMN IF EXISTS account_id")
    op.execute("DROP INDEX IF EXISTS ix_patient_accounts_phone")
    op.execute("DROP TABLE IF EXISTS patient_accounts")
