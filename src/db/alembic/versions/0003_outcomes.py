"""Backtest outcome labels: the ``outcomes`` table.

One row per brand, horizon (months after filing) and labeller version, saying
whether the brand had launched (``launched`` / ``not_launched`` / ``unknown``)
with the criteria and evidence behind it. See docs/BACKTEST.md.

Downgrading drops ``outcomes`` only; every other table keeps its rows.

Revision ID: 0003_outcomes
Revises: 0002_brands
Create Date: 2026-10-09 16:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_outcomes"
down_revision: str | Sequence[str] | None = "0002_brands"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "outcomes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("brand_id", sa.Integer(), nullable=False),
        sa.Column("horizon_months", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=16), nullable=False),
        sa.Column("criteria_met", sa.JSON(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("labelled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("labeller_version", sa.String(length=16), nullable=False),
        sa.ForeignKeyConstraint(
            ["brand_id"], ["brands.id"], name="fk_outcomes_brand_id_brands", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "brand_id", "horizon_months", "labeller_version", name="uq_outcome_brand_horizon"
        ),
    )
    with op.batch_alter_table("outcomes", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_outcomes_brand_id"), ["brand_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("outcomes", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_outcomes_brand_id"))

    op.drop_table("outcomes")
