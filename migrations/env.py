"""
migrations/env.py — Alembic ortam betiği (Faz 10B).

İki şeyi BMK'ya bağlar:
  1) DB URL'si: Config.SQLALCHEMY_DATABASE_URI'den okunur (alembic.ini'de boş).
     Böylece .env'deki DATABASE_URL hem uygulama hem migration için tek kaynak.
  2) target_metadata: models.py'deki tüm SQLAlchemy modellerin metadata'sı.
     `alembic revision --autogenerate` modelleri DB ile karşılaştırıp diff üretir.
"""
import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

# Proje kök dizinini sys.path'e ekle (models, config import edilebilsin)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from config import Config  # noqa: E402
from extensions import db  # noqa: E402
import models  # noqa: F401, E402  — modelleri import et ki metadata dolsun

# Alembic Config nesnesi
config = context.config

# Logging — alembic.ini'deki [loggers] vb. bloklarına göre
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# DB URL'sini Config'den enjekte et (alembic.ini'deki sqlalchemy.url boş)
config.set_main_option('sqlalchemy.url', Config.SQLALCHEMY_DATABASE_URI)

# Autogenerate için metadata
target_metadata = db.metadata


def run_migrations_offline() -> None:
    """Engine olmadan SQL üret — 'alembic upgrade --sql' için."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,         # kolon tip değişimini de fark et
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Engine ile bağlan, migration'ı uygula."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
