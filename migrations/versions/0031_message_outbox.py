"""Reliable messaging: outbound outbox + inbound webhook de-duplication.

- `outbound_messages`: every SMS / WhatsApp send goes through this table. A
  send is attempted inline first (so OTPs arrive immediately); on a provider
  failure the row stays `queued` with exponential backoff and the outbox loop
  retries it until it succeeds or its `expires_at` passes (an agent reply or
  OTP is worthless after minutes; a reminder is good for hours). `to_number`
  and `body` are Fernet-encrypted patient PII, like `sms_log`.
  `provider_msg_id` is kept so delivery reports (DLRs / WhatsApp statuses) can
  update the row later.
- `inbound_events`: (provider, event_id) of every inbound webhook message
  already processed. Meta and SMS gateways retry deliveries; without this a
  retry would run a second agent turn and send a duplicate reply.

Revision ID: 0031
Revises: 0030
Create Date: 2026-10-20
"""
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS outbound_messages (
            id               BIGSERIAL PRIMARY KEY,
            channel          TEXT NOT NULL CHECK (channel IN ('sms','whatsapp')),
            kind             TEXT NOT NULL,
            to_number        TEXT NOT NULL,
            body             TEXT NOT NULL,
            sender_id        TEXT,
            clinic_id        INTEGER REFERENCES clinics(id) ON DELETE SET NULL,
            status           TEXT NOT NULL DEFAULT 'queued'
                             CHECK (status IN ('queued','sending','sent','delivered','failed','expired')),
            provider         TEXT,
            provider_msg_id  TEXT,
            attempts         INTEGER NOT NULL DEFAULT 0,
            last_error       TEXT,
            next_attempt_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at       TIMESTAMPTZ NOT NULL,
            idempotency_key  TEXT UNIQUE,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    # The drain query only ever looks at due, queued rows.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_outbound_due ON outbound_messages (next_attempt_at) "
        "WHERE status = 'queued'"
    )
    # Delivery reports look rows up by the provider's message id.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_outbound_provider_msg ON outbound_messages "
        "(provider, provider_msg_id) WHERE provider_msg_id IS NOT NULL"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS inbound_events (
            provider     TEXT NOT NULL,
            event_id     TEXT NOT NULL,
            received_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (provider, event_id)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS inbound_events")
    op.execute("DROP TABLE IF EXISTS outbound_messages")
