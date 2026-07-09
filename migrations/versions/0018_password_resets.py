"""Patient password reset via SMS OTP.

Stores short-lived, single-use one-time codes (hashed) keyed to a patient
account. Reset codes are delivered over SMS (there is no email infrastructure),
matching the product's local-first design.

Revision ID: 0018
Revises: 0017
Create Date: 2026-06-27
"""
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS password_resets (
            id          BIGSERIAL PRIMARY KEY,
            account_id  INTEGER NOT NULL REFERENCES patient_accounts(id) ON DELETE CASCADE,
            code_hash   TEXT NOT NULL,
            expires_at  TIMESTAMPTZ NOT NULL,
            used        BOOLEAN NOT NULL DEFAULT FALSE,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_password_resets_account "
        "ON password_resets (account_id, used, expires_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS password_resets")
