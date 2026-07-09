"""Add hospital-level IVR channels and voice_ivr channel kind.

Allows a phone number to be mapped to an entire hospital (not just one clinic),
enabling the agent to act as an IVR that routes callers to departments.

Revision ID: 0013
Revises: 0012
Create Date: 2026-06-24
"""
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Allow clinic_id to be NULL so a channel can belong to a hospital instead
    op.execute("ALTER TABLE channels ALTER COLUMN clinic_id DROP NOT NULL")

    # Hospital FK for IVR-type channels
    op.execute(
        "ALTER TABLE channels ADD COLUMN IF NOT EXISTS hospital_id INTEGER "
        "REFERENCES hospitals(id) ON DELETE CASCADE"
    )

    # Exactly one of clinic_id or hospital_id must be set
    op.execute(
        "ALTER TABLE channels ADD CONSTRAINT chk_channel_scope CHECK ("
        "  (clinic_id IS NOT NULL AND hospital_id IS NULL) OR "
        "  (clinic_id IS NULL AND hospital_id IS NOT NULL)"
        ")"
    )

    # Extend the kind CHECK constraint to include voice_ivr
    op.execute("ALTER TABLE channels DROP CONSTRAINT IF EXISTS channels_kind_check")
    op.execute(
        "ALTER TABLE channels ADD CONSTRAINT channels_kind_check CHECK ("
        "  kind IN ('web','whatsapp','sms','voice','voice_sip','phone','voice_ivr')"
        ")"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE channels DROP CONSTRAINT IF EXISTS channels_kind_check")
    op.execute(
        "ALTER TABLE channels ADD CONSTRAINT channels_kind_check CHECK ("
        "  kind IN ('web','whatsapp','sms','voice','voice_sip','phone')"
        ")"
    )
    op.execute("ALTER TABLE channels DROP CONSTRAINT IF EXISTS chk_channel_scope")
    op.execute("ALTER TABLE channels DROP COLUMN IF EXISTS hospital_id")
    op.execute(
        "UPDATE channels SET clinic_id = (SELECT id FROM clinics ORDER BY id LIMIT 1) "
        "WHERE clinic_id IS NULL"
    )
    op.execute("ALTER TABLE channels ALTER COLUMN clinic_id SET NOT NULL")
