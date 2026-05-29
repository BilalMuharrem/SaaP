"""
services/demo_data.py — Yeni kullanıcılar için örnek ürün seed'i.

Amaç: kayıt sonrası kullanıcı boş bir paneli değil, çalışan bir sistemi görür.
Demo ürünleri gerçek pazaryeri URL'leridir; worker bunları diğer ürünler gibi tarar.
Kullanıcı istediği zaman silebilir (UI'da "ÖRNEK" badge'i ile işaretli).

KONFİGÜRASYON
─────────────
Aşağıdaki DEMO_PRODUCTS listesini düzenleyerek kullanılan ürünleri değiştirebilirsin.
Tercihen:
  • Popüler ve stabil ürünler (URL ID'si kolayca değişmiyor)
  • Pazaryeri çeşitliliği (1× Trendyol + 1× Hepsiburada)
  • Stoğu uzun süre bitmeyecek kategoriler

URL'ler değiştirildikten sonra YENİ kayıtlara uygulanır — eski kullanıcılar etkilenmez.
"""
import logging
import uuid

from extensions import db
from models import TrackedProduct, attach_tracked_product_to_global

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# DEMO ÜRÜNLER — istediğin zaman değiştirebilirsin (en az 1, en fazla 5 ürün)
# ─────────────────────────────────────────────────────────────────────────────
DEMO_PRODUCTS = [
    {
        'url': 'https://www.trendyol.com/apple/iphone-15-128-gb-aksiyon-kamerasi-titan-mavi-p-758889063',
        'platform_hint': 'Trendyol',
        # Açıklama (UI'da gösterilmez, kod içi referans için):
        # iPhone 15 — yüksek popülarite + stabil ürün ID
    },
    {
        'url': 'https://www.hepsiburada.com/arzum-okka-minio-otomatik-turk-kahvesi-makinesi-elektrikli-cezve-p-HBV00000FOWFV',
        'platform_hint': 'Hepsiburada',
        # Arzum Okka — uzun yıllar piyasada, çok satan + popüler
    },
]

DEMO_GROUP_LABEL = 'Örnek Ürünler'


def _broker_alive(timeout_seconds=2):
    """Celery broker'ı (Redis) ping et. Erişilemezse False döner.

    Demo seed sırasında broker kapalıysa, sessizce `.delay()` çağırmak yerine
    önce probe edip kullanıcıyı/log'u bilgilendirmek için.
    """
    try:
        from extensions import celery
        conn = celery.broker_connection()
        conn.ensure_connection(max_retries=1, timeout=timeout_seconds)
        conn.close()
        return True
    except Exception as e:
        log.info("[Demo seed] Broker probe başarısız: %s", e)
        return False


def seed_demo_products(user_id):
    """Verilen kullanıcı için DEMO_PRODUCTS'ı takibe ekler.

    Davranış:
      • Tek bir grup altında (DEMO_GROUP_LABEL etiketiyle) eklenir.
      • İlk ürün is_base_product=True, diğerleri False.
      • Her ürün is_demo=True ile işaretlenir.
      • Worker'a fiyat taraması async tetiklenir.

    Hata durumunda sessizce başarısız olur (kayıt akışını bozmamak için).

    Returns:
        int — kaç ürün eklendi
    """
    if not DEMO_PRODUCTS:
        return 0

    try:
        group_id = str(uuid.uuid4())
        added_ids = []

        for idx, item in enumerate(DEMO_PRODUCTS):
            url = (item.get('url') or '').strip()
            if not url.startswith('http'):
                continue

            # Aynı URL bu kullanıcıda zaten varsa atla
            existing = TrackedProduct.query.filter_by(user_id=user_id, url=url).first()
            if existing:
                continue

            tp = TrackedProduct(
                user_id=user_id,
                url=url,
                group_id=group_id,
                is_base_product=(idx == 0),
                tracking_type='price',
                is_price_tracked=True,
                is_radar_tracked=False,
                is_demo=True,
                group_label=(DEMO_GROUP_LABEL if idx == 0 else None),
            )
            db.session.add(tp)
            db.session.flush()

            try:
                attach_tracked_product_to_global(tp)
            except Exception:
                log.exception("[Demo seed] attach_to_global başarısız")

            added_ids.append(tp.id)

        db.session.commit()

        # Worker'a fiyat taramasını async tetikle — ilk veri birkaç dakikada gelir.
        # FAZ 10C: Broker (Redis) erişimini ÖNCEDEN probe et — kapalıysa daha açıklayıcı
        # log + sessiz devam (kullanıcı kaydı bozulmasın). Bir sonraki periyodik beat
        # taraması (03:15/09:15/15:15/21:15) bu ürünleri yine de yakalar.
        if added_ids:
            broker_ok = _broker_alive()
            if broker_ok:
                try:
                    from worker import check_single_product_task
                    for pid in added_ids:
                        check_single_product_task.delay(pid)
                    log.info("[Demo seed] %d ürün için Celery scan tetiklendi", len(added_ids))
                except Exception:
                    log.exception("[Demo seed] Celery tetikleme başarısız (broker UP ama task patladı)")
            else:
                log.warning(
                    "[Demo seed] Broker (Redis) erişilemez — demo ürünler eklendi "
                    "ama anlık scan tetiklenemedi. Bir sonraki periyodik beat "
                    "(en geç 4 saat) bunları tarayacak. user_id=%s, eklenen=%d",
                    user_id, len(added_ids),
                )

        log.info("[Demo seed] user_id=%s için %d demo ürün eklendi", user_id, len(added_ids))
        return len(added_ids)

    except Exception:
        log.exception("[Demo seed] beklenmeyen hata, rollback")
        db.session.rollback()
        return 0
