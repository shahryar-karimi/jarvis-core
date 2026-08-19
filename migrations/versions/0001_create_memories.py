"""Create the memories table.

Revision ID: 0001_create_memories
Revises:
Create Date: 2026-08-18

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_create_memories"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the initial long-term memory schema."""
    op.create_table(
        "memories",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "category",
            "key",
            name="uq_memory_category_key",
        ),
    )
    op.create_index(
        "ix_memories_category",
        "memories",
        ["category"],
        unique=False,
    )


def downgrade() -> None:
    """Remove the initial long-term memory schema."""
    op.drop_index("ix_memories_category", table_name="memories")
    op.drop_table("memories")
