"""PGVector RAG — per-hospital document knowledge base.

Enables the `vector` PostgreSQL extension and creates the `hospital_documents`
table that tracks raw document uploads and their chunk counts for deletion.

Revision ID: 0016
Revises: 0015
Create Date: 2026-06-24
"""
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Enable pgvector — skips gracefully if the binary isn't installed yet.
    # To install: sudo apt install postgresql-18-pgvector  (then re-run upgrade)
    op.execute(
        """
        DO $$ BEGIN
            CREATE EXTENSION IF NOT EXISTS vector;
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'pgvector not available — install postgresql-18-pgvector to enable RAG embedding';
        END $$
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS hospital_documents (
            id           SERIAL PRIMARY KEY,
            hospital_id  INTEGER NOT NULL REFERENCES hospitals(id) ON DELETE CASCADE,
            filename     TEXT NOT NULL,
            content_type TEXT NOT NULL DEFAULT 'text/plain',
            chunk_count  INTEGER NOT NULL DEFAULT 0,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_hospital_documents_hospital "
        "ON hospital_documents (hospital_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_hospital_documents_hospital")
    op.execute("DROP TABLE IF EXISTS hospital_documents")
    # Do NOT drop the vector extension — other tables may depend on it.
