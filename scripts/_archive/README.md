# Arşiv — Geçmiş Tek Seferlik Script'ler

Bu klasör, proje kökünde birikmiş tek kullanımlık migration/patch script'lerini barındırır.
**Bu script'ler tarihsel referans için saklanır.** Üretimde veya geliştirmede çalıştırılmamalıdır.

## İçindekiler

### Template HTML patch'leri (uygulandı, gerek yok)
- `update_badge.py`, `update_badge_2.py`, `update_badge_dynamic.py`, `update_badge_cache.py` — sidebar status badge'i ekleme/değiştirme
- `remove_sidebar_badge.py`, `remove_sys_badge.py` — eski badge'leri kaldırma
- `patch_sidebar.py` — sidebar HTML yaması
- `update_icons.py`, `update_prices.py` — toplu template güncellemeleri
- `theme_fixer_worker.py`, `theme_fixer_cost.py` — dark/light mode yamaları

### Migration script'leri (uygulandı)
- `convert_to_pw.py` — Selenium → Playwright geçişi
- `rewrite_worker.py` — worker.py yeniden yazımı
- `db_migration.py` — Job.result_html stil yaması
- `trigger.py` — mevcut ürünleri tek seferlik kuyruğa atma

### Kullanılmayan modüller
- `database_manager.py` — bağımsız PostgreSQL helper. Kod tabanında hiçbir yerden import edilmiyor; muhtemelen erken prototip kalıntısı.

## Silinmeyi neden bekliyorlar?

Faz 1 (Blueprint refactor) ve Faz 4 (Test suite) tamamlandıktan sonra bu klasör tamamen silinebilir.
O zamana kadar, geçmiş bir davranışı anlamak gerekirse referans olarak kalır.
