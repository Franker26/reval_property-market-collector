"""Drop listing_targets table (reemplazada por market_segments)

Revision ID: 003
Revises: 002
Create Date: 2026-05-29

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("listing_targets")


def downgrade() -> None:
    op.create_table(
        "listing_targets",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("source_id", sa.BigInteger(), sa.ForeignKey("market_sources.id"), nullable=False),
        sa.Column("search_url", sa.Text(), nullable=False),
        sa.Column("operation_type", sa.Text(), nullable=True),
        sa.Column("property_type", sa.Text(), nullable=True),
        sa.Column("location_text", sa.Text(), nullable=True),
        sa.Column("state", sa.Text(), nullable=False, server_default="active"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("last_processed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("next_process_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("cooldown_until", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_page_processed", sa.Integer(), nullable=True),
        sa.Column("last_offset_processed", sa.Integer(), nullable=True),
        sa.Column("last_success_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_error_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
    )
