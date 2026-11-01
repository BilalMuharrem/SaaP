"""add cost_meta to tracked_products

Revision ID: 32df324c0bce
Revises: 7f0f07e10728
Create Date: 2026-11-01 23:31:19.619495

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '32df324c0bce'
down_revision: Union[str, Sequence[str], None] = '7f0f07e10728'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema. (Autogenerate drift'i elle çıkarıldı — sadece cost_meta.)"""
    op.add_column('tracked_products', sa.Column('cost_meta', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('tracked_products', 'cost_meta')
