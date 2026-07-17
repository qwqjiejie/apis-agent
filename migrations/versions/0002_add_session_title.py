"""Add the persisted conversation title introduced after the baseline."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agentx_session",
        sa.Column("title", sa.String(100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agentx_session", "title")
