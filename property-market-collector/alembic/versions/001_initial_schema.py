"""Initial schema

Revision ID: 001
Revises:
Create Date: 2026-05-28

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "market_sources",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("code", name="uq_market_sources_code"),
    )

    op.create_table(
        "collection_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("source_id", sa.BigInteger(), sa.ForeignKey("market_sources.id"), nullable=True),
        sa.Column("run_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="running"),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Numeric(), nullable=True),
        sa.Column("params_json", postgresql.JSONB(), nullable=True),
        sa.Column("stats_json", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "listing_entities",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("source_id", sa.BigInteger(), sa.ForeignKey("market_sources.id"), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=True),
        sa.Column("operation_type", sa.Text(), nullable=True),
        sa.Column("property_type", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="unknown"),
        sa.Column("first_seen_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_error_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_changed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_snapshot_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("source_id", "external_id", name="uq_listing_source_external"),
    )

    op.create_table(
        "listing_snapshots",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("listing_id", sa.BigInteger(), sa.ForeignKey("listing_entities.id"), nullable=False),
        sa.Column("captured_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(), nullable=False),
        sa.Column("raw_payload_json", postgresql.JSONB(), nullable=True),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("price_hash", sa.Text(), nullable=True),
        sa.Column("availability_hash", sa.Text(), nullable=True),
        sa.Column("location_hash", sa.Text(), nullable=True),
        sa.Column("media_hash", sa.Text(), nullable=True),
        sa.Column("price_amount", sa.Numeric(), nullable=True),
        sa.Column("price_currency", sa.Text(), nullable=True),
        sa.Column("expenses_amount", sa.Numeric(), nullable=True),
        sa.Column("expenses_currency", sa.Text(), nullable=True),
        sa.Column("surface_total", sa.Numeric(), nullable=True),
        sa.Column("surface_covered", sa.Numeric(), nullable=True),
        sa.Column("rooms", sa.Numeric(), nullable=True),
        sa.Column("bedrooms", sa.Numeric(), nullable=True),
        sa.Column("bathrooms", sa.Numeric(), nullable=True),
        sa.Column("garages", sa.Numeric(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_listing_snapshots_listing_captured", "listing_snapshots", ["listing_id", "captured_at"])
    op.create_index("idx_listing_snapshots_content_hash", "listing_snapshots", ["content_hash"])

    op.create_table(
        "discovery_events",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("source_id", sa.BigInteger(), sa.ForeignKey("market_sources.id"), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("search_url", sa.Text(), nullable=True),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("offset_value", sa.Integer(), nullable=True),
        sa.Column("lastmod", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("discovered_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("run_id", sa.BigInteger(), sa.ForeignKey("collection_runs.id"), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "collection_errors",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("run_id", sa.BigInteger(), sa.ForeignKey("collection_runs.id"), nullable=True),
        sa.Column("source_id", sa.BigInteger(), sa.ForeignKey("market_sources.id"), nullable=True),
        sa.Column("listing_id", sa.BigInteger(), sa.ForeignKey("listing_entities.id"), nullable=True),
        sa.Column("external_id", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("error_type", sa.Text(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("failed_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
    )

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

    op.create_table(
        "listing_change_events",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("listing_id", sa.BigInteger(), sa.ForeignKey("listing_entities.id"), nullable=False),
        sa.Column("previous_snapshot_id", sa.BigInteger(), sa.ForeignKey("listing_snapshots.id"), nullable=True),
        sa.Column("new_snapshot_id", sa.BigInteger(), sa.ForeignKey("listing_snapshots.id"), nullable=True),
        sa.Column("change_type", sa.Text(), nullable=False),
        sa.Column("old_value", postgresql.JSONB(), nullable=True),
        sa.Column("new_value", postgresql.JSONB(), nullable=True),
        sa.Column("detected_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("listing_change_events")
    op.drop_table("listing_targets")
    op.drop_table("collection_errors")
    op.drop_table("discovery_events")
    op.drop_index("idx_listing_snapshots_content_hash", "listing_snapshots")
    op.drop_index("idx_listing_snapshots_listing_captured", "listing_snapshots")
    op.drop_table("listing_snapshots")
    op.drop_table("listing_entities")
    op.drop_table("collection_runs")
    op.drop_table("market_sources")
