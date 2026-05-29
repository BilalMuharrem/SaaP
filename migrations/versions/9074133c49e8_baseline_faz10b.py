"""baseline_faz10b — Alembic baseline

Faz 10B: init_db'deki manuel ALTER TABLE blokları yerine Alembic devraldı.

Bu baseline ÜRETİM DB'sinden autogenerate ile üretildi. Mevcut şema ile
models.py arasında küçük drift'leri düzeltir:

  • Index rename: ix_kt_pool_id → ix_keyword_trackers_pool_id (SQLAlchemy default'una hizalama)
  • Index rename: ix_tp_global_pid → ix_tracked_products_global_product_id
  • FK constraint eklendi: keyword_trackers.pool_id → keyword_pools.id
  • FK constraint eklendi: tracked_products.global_product_id → global_products.id
  • NOT NULL eklendi: tracked_products.is_demo (zaten backfill edilmiş)
  • NOT NULL eklendi: users.onboarding_completed (zaten backfill edilmiş)

ÜRETİME UYGULAMA:
   1. Önce DB backup al: ./.venv/bin/python scripts/backup_db.py
   2. ./.venv/bin/alembic upgrade head
   3. Hata olursa: psql ile backup'tan restore et.

YENİ KURULUM (boş DB) için:
   ./.venv/bin/alembic upgrade head
   tüm tabloları yaratacak — ama TABLO YARATMA AŞAMASI init_db'de hâlâ var.
   Geçiş tamamlanınca init_db'deki create_all() ve seed kısmı kalır;
   ALTER TABLE blokları artık burada yönetilir.

ESKİ KURULUM (init_db çalışmış) için:
   ./.venv/bin/alembic stamp head
   "Bu DB zaten baseline durumda, hiçbir şey yapma" demek için.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9074133c49e8'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Üretim ile model arasındaki küçük drift'leri uygula."""
    # ── keyword_trackers index + FK ──
    op.drop_index(op.f('ix_kt_pool_id'), table_name='keyword_trackers')
    op.create_index(
        op.f('ix_keyword_trackers_pool_id'),
        'keyword_trackers', ['pool_id'], unique=False,
    )
    op.create_foreign_key(
        'fk_keyword_trackers_pool_id',
        'keyword_trackers', 'keyword_pools',
        ['pool_id'], ['id'],
    )

    # ── tracked_products.is_demo NOT NULL ──
    op.alter_column(
        'tracked_products', 'is_demo',
        existing_type=sa.BOOLEAN(),
        server_default=None,
        nullable=False,
    )

    # ── tracked_products index + FK (global_products) ──
    op.drop_index(op.f('ix_tp_global_pid'), table_name='tracked_products')
    op.create_index(
        op.f('ix_tracked_products_global_product_id'),
        'tracked_products', ['global_product_id'], unique=False,
    )
    op.create_foreign_key(
        'fk_tracked_products_global_product_id',
        'tracked_products', 'global_products',
        ['global_product_id'], ['id'],
    )

    # ── users.onboarding_completed NOT NULL ──
    op.alter_column(
        'users', 'onboarding_completed',
        existing_type=sa.BOOLEAN(),
        server_default=None,
        nullable=False,
    )


def downgrade() -> None:
    """Geri al — production'da kullanmayın, sadece geliştirme test'i için."""
    op.alter_column(
        'users', 'onboarding_completed',
        existing_type=sa.BOOLEAN(),
        server_default=sa.text('false'),
        nullable=True,
    )
    op.drop_constraint('fk_tracked_products_global_product_id',
                       'tracked_products', type_='foreignkey')
    op.drop_index(op.f('ix_tracked_products_global_product_id'),
                  table_name='tracked_products')
    op.create_index(op.f('ix_tp_global_pid'),
                    'tracked_products', ['global_product_id'], unique=False)
    op.alter_column(
        'tracked_products', 'is_demo',
        existing_type=sa.BOOLEAN(),
        server_default=sa.text('false'),
        nullable=True,
    )
    op.drop_constraint('fk_keyword_trackers_pool_id',
                       'keyword_trackers', type_='foreignkey')
    op.drop_index(op.f('ix_keyword_trackers_pool_id'),
                  table_name='keyword_trackers')
    op.create_index(op.f('ix_kt_pool_id'),
                    'keyword_trackers', ['pool_id'], unique=False)
