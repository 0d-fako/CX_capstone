"""widen chat_sessions.channel to hold a Slack thread key

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-11
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("chat_sessions", "channel", type_=sa.String(120), existing_type=sa.String(32))


def downgrade() -> None:
    op.alter_column("chat_sessions", "channel", type_=sa.String(32), existing_type=sa.String(120))
