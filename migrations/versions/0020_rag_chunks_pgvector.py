"""RAG chunk vectors in Postgres (pgvector).

Creates the `rag_chunks` table holding embedded document chunks so RAG search
is shared across all API workers (replacing per-process Chroma files).
Dimension 768 matches nomic-embed-text (settings.embedding_dims).

Skips gracefully (like 0016) when the pgvector extension binary isn't
installed; install postgresql-<major>-pgvector and re-run `alembic upgrade`.

Revision ID: 0020
Revises: 0019
Create Date: 2026-07-02
"""
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
            CREATE EXTENSION IF NOT EXISTS vector;
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'pgvector not available — install postgresql-<major>-pgvector to enable RAG';
        END $$
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') THEN
                CREATE TABLE IF NOT EXISTS rag_chunks (
                    id           TEXT PRIMARY KEY,   -- doc-{document_id}-chunk-{i}
                    hospital_id  INTEGER NOT NULL REFERENCES hospitals(id) ON DELETE CASCADE,
                    document_id  INTEGER NOT NULL,
                    content      TEXT NOT NULL,
                    embedding    vector(768) NOT NULL,
                    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
                );
                CREATE INDEX IF NOT EXISTS ix_rag_chunks_hospital
                    ON rag_chunks (hospital_id);
                CREATE INDEX IF NOT EXISTS ix_rag_chunks_document
                    ON rag_chunks (document_id);
                -- HNSW: good recall with no training step (unlike ivfflat).
                CREATE INDEX IF NOT EXISTS ix_rag_chunks_embedding
                    ON rag_chunks USING hnsw (embedding vector_cosine_ops);
            ELSE
                RAISE NOTICE 'pgvector missing — rag_chunks not created; RAG_BACKEND=pgvector will not work';
            END IF;
        END $$
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS rag_chunks")
