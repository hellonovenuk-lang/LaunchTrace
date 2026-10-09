"""Brands across weeks: brands, observations, stage_changes, and brand_id links.

``brands`` is one row per real-world brand/company across journal weeks;
``observations`` is the append-only record of facts about a brand, each with
the date it was true in the world and whether that date is point-in-time safe;
``stage_changes`` records launch-stage transitions. ``opportunities`` and
``score_events`` gain a nullable ``brand_id``. See docs/ARCHITECTURE.md.

Downgrading drops the three new tables and the two ``brand_id`` columns only;
every baseline table keeps its rows.

Revision ID: 0002_brands
Revises: 0001_baseline
Create Date: 2026-10-09 12:35:22.508554
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_brands"
down_revision: str | Sequence[str] | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "brands",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("brand_uid", sa.String(length=32), nullable=False),
        sa.Column("brand_key", sa.String(length=128), nullable=False),
        sa.Column("brand_name", sa.String(length=512), nullable=False),
        sa.Column("company_number", sa.String(length=16), nullable=True),
        sa.Column("company_name", sa.String(length=512), nullable=True),
        sa.Column("applicant_type", sa.String(length=32), nullable=False),
        sa.Column("applicant_key_hash", sa.String(length=64), nullable=True),
        sa.Column("product_category", sa.String(length=64), nullable=True),
        sa.Column("region", sa.String(length=128), nullable=True),
        sa.Column("website", sa.String(length=512), nullable=True),
        sa.Column("first_seen_journal", sa.String(length=32), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_filing_date", sa.Date(), nullable=True),
        sa.Column("last_seen_journal", sa.String(length=32), nullable=False),
        sa.Column("current_stage", sa.String(length=32), nullable=False),
        sa.Column("current_score", sa.Integer(), nullable=False),
        sa.Column("current_band", sa.String(length=16), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("launched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("brand_key"),
        sa.UniqueConstraint("brand_uid"),
    )
    with op.batch_alter_table("brands", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_brands_applicant_key_hash"), ["applicant_key_hash"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_brands_company_number"), ["company_number"], unique=False
        )

    op.create_table(
        "observations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("brand_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("signal", sa.String(length=64), nullable=False),
        sa.Column("value", sa.JSON(), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_date", sa.Date(), nullable=True),
        sa.Column("point_in_time_safe", sa.Boolean(), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("journal_number", sa.String(length=32), nullable=True),
        sa.ForeignKeyConstraint(
            ["brand_id"], ["brands.id"], name="fk_observations_brand_id_brands", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("observations", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_observations_brand_id"), ["brand_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_observations_observed_at"), ["observed_at"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_observations_signal"), ["signal"], unique=False)

    op.create_table(
        "stage_changes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("brand_id", sa.Integer(), nullable=False),
        sa.Column("from_stage", sa.String(length=32), nullable=True),
        sa.Column("to_stage", sa.String(length=32), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(
            ["brand_id"], ["brands.id"], name="fk_stage_changes_brand_id_brands", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("stage_changes", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_stage_changes_brand_id"), ["brand_id"], unique=False)

    with op.batch_alter_table("opportunities", schema=None) as batch_op:
        batch_op.add_column(sa.Column("brand_id", sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f("ix_opportunities_brand_id"), ["brand_id"], unique=False)
        batch_op.create_foreign_key(
            "fk_opportunities_brand_id_brands", "brands", ["brand_id"], ["id"], ondelete="SET NULL"
        )

    with op.batch_alter_table("score_events", schema=None) as batch_op:
        batch_op.add_column(sa.Column("brand_id", sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f("ix_score_events_brand_id"), ["brand_id"], unique=False)
        batch_op.create_foreign_key(
            "fk_score_events_brand_id_brands", "brands", ["brand_id"], ["id"], ondelete="SET NULL"
        )


def downgrade() -> None:
    with op.batch_alter_table("score_events", schema=None) as batch_op:
        batch_op.drop_constraint("fk_score_events_brand_id_brands", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_score_events_brand_id"))
        batch_op.drop_column("brand_id")

    with op.batch_alter_table("opportunities", schema=None) as batch_op:
        batch_op.drop_constraint("fk_opportunities_brand_id_brands", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_opportunities_brand_id"))
        batch_op.drop_column("brand_id")

    with op.batch_alter_table("stage_changes", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_stage_changes_brand_id"))

    op.drop_table("stage_changes")
    with op.batch_alter_table("observations", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_observations_signal"))
        batch_op.drop_index(batch_op.f("ix_observations_observed_at"))
        batch_op.drop_index(batch_op.f("ix_observations_brand_id"))

    op.drop_table("observations")
    with op.batch_alter_table("brands", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_brands_company_number"))
        batch_op.drop_index(batch_op.f("ix_brands_applicant_key_hash"))

    op.drop_table("brands")
