"""Add market_segments, segment_snapshots; add segment_id to listing_entities

Revision ID: 002
Revises: 001
Create Date: 2026-05-29

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "market_segments",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("portal", sa.Text(), nullable=False),
        sa.Column("operation_key", sa.Text(), nullable=False),
        sa.Column("operation_value", sa.Integer(), nullable=False),
        sa.Column("province_key", sa.Text(), nullable=False),
        sa.Column("province_value", sa.Integer(), nullable=False),
        sa.Column("price_min", sa.Numeric(), nullable=False),
        sa.Column("price_max", sa.Numeric(), nullable=False),
        sa.Column("surface_min", sa.Numeric(), nullable=False),
        sa.Column("surface_max", sa.Numeric(), nullable=False),
        sa.Column("total_count", sa.Integer(), nullable=True),
        sa.Column("depth", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("parent_id", sa.BigInteger(), sa.ForeignKey("market_segments.id"), nullable=True),
        sa.Column("is_leaf", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_oversized", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("last_checked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_market_segments_portal_op_prov", "market_segments", ["portal", "operation_key", "province_key"])
    op.create_index("idx_market_segments_leaf", "market_segments", ["is_leaf", "portal"])

    op.create_table(
        "segment_snapshots",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("segment_id", sa.BigInteger(), sa.ForeignKey("market_segments.id"), nullable=False),
        sa.Column("captured_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("total_count", sa.Integer(), nullable=False),
        sa.Column("price_min", sa.Numeric(), nullable=False),
        sa.Column("price_max", sa.Numeric(), nullable=False),
        sa.Column("surface_min", sa.Numeric(), nullable=False),
        sa.Column("surface_max", sa.Numeric(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_segment_snapshots_segment_captured", "segment_snapshots", ["segment_id", "captured_at"])

    op.add_column(
        "listing_entities",
        sa.Column("segment_id", sa.BigInteger(), sa.ForeignKey("market_segments.id"), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("listing_entities", "segment_id")
    op.drop_index("idx_segment_snapshots_segment_captured", "segment_snapshots")
    op.drop_table("segment_snapshots")
    op.drop_index("idx_market_segments_leaf", "market_segments")
    op.drop_index("idx_market_segments_portal_op_prov", "market_segments")
    op.drop_table("market_segments")
