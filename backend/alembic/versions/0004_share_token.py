"""note share token

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-11

"""
from alembic import op
import sqlalchemy as sa


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("notes", sa.Column("share_token", sa.String(64), nullable=True))
    op.create_index("ix_notes_share_token", "notes", ["share_token"], unique=True)


def downgrade():
    op.drop_index("ix_notes_share_token", "notes")
    op.drop_column("notes", "share_token")
