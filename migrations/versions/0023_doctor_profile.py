"""Doctor profile fields: description, degrees, photo

Admins fill in a public-facing profile per doctor (bio text, degrees like
"MBBS, FCPS", and a photo shown on the patient portal tiles). The photo is
stored in-row as BYTEA and served by GET /doctors/{id}/photo — local-first,
no external object storage. description/degrees also feed the agent's
doctor context so it can answer "which doctor should I see?" from real data.

Revision ID: 0023
Revises: 0022
"""

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE doctors "
        "ADD COLUMN IF NOT EXISTS description TEXT NOT NULL DEFAULT '', "
        "ADD COLUMN IF NOT EXISTS degrees TEXT NOT NULL DEFAULT '', "
        "ADD COLUMN IF NOT EXISTS photo BYTEA, "
        "ADD COLUMN IF NOT EXISTS photo_mime TEXT"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE doctors "
        "DROP COLUMN IF EXISTS description, "
        "DROP COLUMN IF EXISTS degrees, "
        "DROP COLUMN IF EXISTS photo, "
        "DROP COLUMN IF EXISTS photo_mime"
    )
