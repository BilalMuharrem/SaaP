"""
database_manager.py
-------------------
Trendyol scraper için PostgreSQL yönetim modülü.

- products tablosu: URL bazlı upsert (varsa güncelle, yoksa ekle)
- price_history tablosu: her taramada yeni satır (zaman serisi)
- Tüm yazma işlemleri tek bir transaction içinde gerçekleşir
"""

import logging
import os
from contextlib import contextmanager
from typing import Iterable

import psycopg2
from psycopg2.extras import execute_batch
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)


# ── Bağlantı yapılandırması ─────────────────────────────────────────────
# .env / config'den okunur; doğrudan dict olarak da geçilebilir.
DB_CONFIG = {
    "dbname":   os.getenv("DB_NAME",     "saas_db"),
    "user":     os.getenv("DB_USER",     "mac"),
    "password": os.getenv("DB_PASSWORD", ""),
    "host":     os.getenv("DB_HOST",     "localhost"),
    "port":     os.getenv("DB_PORT",     "5432"),
}


@contextmanager
def get_connection(config: dict | None = None):
    """psycopg2 bağlantısını context manager ile yönetir.

    Hata durumunda otomatik rollback, başarıda commit, her durumda close.
    """
    cfg = config or DB_CONFIG
    conn = None
    try:
        conn = psycopg2.connect(**cfg)
        yield conn
        conn.commit()
    except psycopg2.Error as exc:
        if conn is not None:
            conn.rollback()
        log.error("Veritabanı işlemi başarısız, rollback uygulandı: %s", exc)
        raise
    finally:
        if conn is not None:
            conn.close()


# ── SQL şablonları ──────────────────────────────────────────────────────
UPSERT_PRODUCT_SQL = """
    INSERT INTO products (product_name, brand, category, url)
    VALUES (%(product_name)s, %(brand)s, %(category)s, %(url)s)
    ON CONFLICT (url) DO UPDATE SET
        product_name = EXCLUDED.product_name,
        brand        = EXCLUDED.brand,
        category     = EXCLUDED.category,
        updated_at   = NOW()
    RETURNING id;
"""

INSERT_PRICE_HISTORY_SQL = """
    INSERT INTO price_history
        (product_id, price, discount_rate, seo_rank, buybox_owner)
    VALUES
        (%(product_id)s, %(price)s, %(discount_rate)s, %(seo_rank)s, %(buybox_owner)s);
"""

REQUIRED_KEYS = {"product_name", "url", "price"}


def _validate(row: dict) -> None:
    missing = REQUIRED_KEYS - row.keys()
    if missing:
        raise ValueError(f"Eksik alanlar: {missing} → {row!r}")


def save_scraped_data(
    data_list: Iterable[dict],
    config: dict | None = None,
) -> dict:
    """Scraper'dan gelen sözlük listesini tek transaction'da kaydeder.

    Her sözlükte bulunması gereken anahtarlar:
        product_name, url, price                          (zorunlu)
        brand, category, discount_rate, seo_rank, buybox_owner   (opsiyonel)

    Returns: {'products_upserted': N, 'price_rows_inserted': M}
    """
    rows = list(data_list)
    if not rows:
        log.warning("save_scraped_data boş listeyle çağrıldı, atlandı.")
        return {"products_upserted": 0, "price_rows_inserted": 0}

    for r in rows:
        _validate(r)

    price_payload: list[dict] = []

    with get_connection(config) as conn:
        with conn.cursor() as cur:
            # 1) products tablosuna upsert (URL'ye göre) ve dönen id'yi al
            for row in rows:
                cur.execute(UPSERT_PRODUCT_SQL, {
                    "product_name": row["product_name"],
                    "brand":        row.get("brand"),
                    "category":     row.get("category"),
                    "url":          row["url"],
                })
                product_id = cur.fetchone()[0]

                price_payload.append({
                    "product_id":    product_id,
                    "price":         row["price"],
                    "discount_rate": row.get("discount_rate"),
                    "seo_rank":      row.get("seo_rank"),
                    "buybox_owner":  row.get("buybox_owner"),
                })

            # 2) price_history'ye toplu insert (her tarama = yeni satır)
            execute_batch(cur, INSERT_PRICE_HISTORY_SQL, price_payload, page_size=200)

    log.info("Kayıt tamam: %d ürün upsert, %d fiyat satırı eklendi.",
             len(rows), len(price_payload))
    return {
        "products_upserted":   len(rows),
        "price_rows_inserted": len(price_payload),
    }


# ── Hızlı manuel test ──────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    sample = [
        {
            "product_name":  "Test Ürün A",
            "brand":         "MarkaX",
            "category":      "Elektronik",
            "url":           "https://www.trendyol.com/p/test-urun-a-1",
            "price":         499.90,
            "discount_rate": 15.0,
            "seo_rank":      3,
            "buybox_owner":  "SatıcıY",
        },
    ]
    print(save_scraped_data(sample))
