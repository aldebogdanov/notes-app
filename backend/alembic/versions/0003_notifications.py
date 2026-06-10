"""notification settings + note_notifications

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-10

"""
from alembic import op
import sqlalchemy as sa


revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users",
        sa.Column(
            "notification_settings",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.create_table(
        "note_notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "note_id",
            sa.Integer(),
            sa.ForeignKey("notes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('pending','sent','skipped','failed')",
            name="ck_note_notifications_status",
        ),
        sa.UniqueConstraint("note_id", "channel", name="uq_note_notifications_note_channel"),
    )
    op.create_index("ix_note_notifications_note_id", "note_notifications", ["note_id"])
    op.create_index("ix_note_notifications_status", "note_notifications", ["status"])

    # Notes already past their date never get a reminder: mark them skipped so
    # the scheduler (M3) has nothing to pick up. Server-side CURRENT_DATE (UTC)
    # is the only boundary available before per-user timezones exist.
    op.execute(
        """
        INSERT INTO note_notifications (note_id, channel, status)
        SELECT id, 'telegram', 'skipped' FROM notes
        WHERE note_date IS NOT NULL AND note_date < CURRENT_DATE
        """
    )


def downgrade():
    op.drop_index("ix_note_notifications_status", "note_notifications")
    op.drop_index("ix_note_notifications_note_id", "note_notifications")
    op.drop_table("note_notifications")
    op.drop_column("users", "notification_settings")
