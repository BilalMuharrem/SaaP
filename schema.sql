-- Trendyol scraper veritabanı şeması
-- Çalıştırmak için: psql postgresql://mac@localhost:5432/saas_db -f schema.sql

BEGIN;

-- ── products: ürünlerin sabit (yavaş değişen) bilgileri ──────────────────
CREATE TABLE IF NOT EXISTS products (
    id              SERIAL          PRIMARY KEY,
    product_name    TEXT            NOT NULL,
    brand           TEXT,
    category        TEXT,
    url             TEXT            NOT NULL UNIQUE,   -- doğal anahtar; upsert bu sütun üzerinden
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

-- ── price_history: her tarama bir yeni satır (zaman serisi) ─────────────
CREATE TABLE IF NOT EXISTS price_history (
    id              BIGSERIAL       PRIMARY KEY,
    product_id      INTEGER         NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    price           NUMERIC(12, 2)  NOT NULL,
    discount_rate   NUMERIC(5, 2),
    seo_rank        INTEGER,
    buybox_owner    TEXT,
    timestamp       TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

-- Grafik / trend sorguları için bileşik indeks
CREATE INDEX IF NOT EXISTS idx_price_history_product_time
    ON price_history (product_id, timestamp DESC);

COMMIT;
