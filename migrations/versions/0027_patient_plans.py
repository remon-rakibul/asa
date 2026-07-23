"""Patient freemium plans (trial / free / premium) + monthly usage counter.

- `patient_accounts` gains a `plan` (free|premium), a `trial_ends_at` (a fresh
  30-day full-access trial from signup), and a `premium_until` (a prepaid
  subscription horizon — BD has no card auto-debit, so periods are prepaid and
  renewed manually). The effective tier is derived at read time:
  premium (premium_until > now) → trial (trial_ends_at > now) → free.
- Existing accounts are backfilled with a fresh trial so nobody is downgraded
  the moment this ships.
- `patient_usage` counts the metered unit — agent (chat/voice) bookings per
  calendar month — so the free tier's monthly booking cap is enforceable at a
  single code point. Direct-UI bookings (which still pay the per-booking fee)
  are not metered here.

Revision ID: 0027
Revises: 0026
Create Date: 2026-07-12
"""
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE patient_accounts ADD COLUMN IF NOT EXISTS plan TEXT "
        "NOT NULL DEFAULT 'free' CHECK (plan IN ('free','premium'))"
    )
    op.execute(
        "ALTER TABLE patient_accounts ADD COLUMN IF NOT EXISTS trial_ends_at TIMESTAMPTZ"
    )
    op.execute(
        "ALTER TABLE patient_accounts ADD COLUMN IF NOT EXISTS premium_until TIMESTAMPTZ"
    )
    # Existing patients get a fresh 30-day trial (generous — don't downgrade
    # anyone at deploy time). New signups set their own trial_ends_at.
    op.execute(
        "UPDATE patient_accounts SET trial_ends_at = now() + interval '30 days' "
        "WHERE trial_ends_at IS NULL"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS patient_usage (
            account_id     INTEGER NOT NULL REFERENCES patient_accounts(id) ON DELETE CASCADE,
            period         TEXT NOT NULL,          -- 'YYYY-MM' (calendar month)
            agent_bookings INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (account_id, period)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS patient_usage")
    op.execute("ALTER TABLE patient_accounts DROP COLUMN IF EXISTS premium_until")
    op.execute("ALTER TABLE patient_accounts DROP COLUMN IF EXISTS trial_ends_at")
    op.execute("ALTER TABLE patient_accounts DROP COLUMN IF EXISTS plan")
