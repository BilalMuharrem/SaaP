"""add is_paused to trackers (group pause)

Revision ID: 7f0f07e10728
Revises: 9074133c49e8
Create Date: 2026-10-28 00:15:16.821170

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7f0f07e10728'
down_revision: Union[str, Sequence[str], None] = '9074133c49e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    NOT: Autogenerate, baseline ile mevcut DB arasındaki eski drift'i de
    (index rename / FK / NOT NULL) yakalamıştı. Bunlar zaten DB'de mevcut veya
    riskli olduğu için ELLE ÇIKARILDI (CLAUDE.md: üretileni gözden geçir).
    Bu migration YALNIZCA is_paused kolonlarını ekler.
    """
    op.add_column('tracked_products',
                  sa.Column('is_paused', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.add_column('keyword_trackers',
                  sa.Column('is_paused', sa.Boolean(), server_default=sa.text('false'), nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('keyword_trackers', 'is_paused')
    op.drop_column('tracked_products', 'is_paused')
