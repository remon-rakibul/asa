"""Consultation fees + patient reviews (marketplace round)

Doctors get two nullable consultation fees (৳): fee_new for a first visit and
fee_followup for a return visit — NULL means "not set", shown as such in the
portal and omitted from agent answers. doctor_reviews holds 1-5 star ratings
with optional text from platform patient accounts; one review per
(doctor, account), editable, and hospital admins can hide individual reviews
(status published|hidden). The lower(specialty) index backs the marketplace
specialty search.

Revision ID: 0024
Revises: 0023
"""

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE doctors "
        "ADD COLUMN IF NOT EXISTS fee_new INTEGER, "
        "ADD COLUMN IF NOT EXISTS fee_followup INTEGER"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS doctor_reviews (
            id          SERIAL PRIMARY KEY,
            doctor_id   INTEGER NOT NULL REFERENCES doctors(id) ON DELETE CASCADE,
            account_id  INTEGER NOT NULL REFERENCES patient_accounts(id) ON DELETE CASCADE,
            rating      SMALLINT NOT NULL CHECK (rating BETWEEN 1 AND 5),
            text        TEXT NOT NULL DEFAULT '',
            status      TEXT NOT NULL DEFAULT 'published'
                        CHECK (status IN ('published', 'hidden')),
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (doctor_id, account_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_doctor_reviews_doctor "
        "ON doctor_reviews (doctor_id, status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_doctors_specialty "
        "ON doctors (lower(specialty))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_doctors_specialty")
    op.execute("DROP TABLE IF EXISTS doctor_reviews")
    op.execute(
        "ALTER TABLE doctors "
        "DROP COLUMN IF EXISTS fee_new, "
        "DROP COLUMN IF EXISTS fee_followup"
    )
