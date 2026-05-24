# Arşivlenmiş Manuel Test Script'leri

Bu klasördekiler `pytest` test'leri **değil**. Hepsi geliştirici tarafından elle
çalıştırılan, bir fonksiyonu denemek için yazılmış ad hoc script'lerdir.

## Neden saklanıyor?

Faz 4'te (`tests/` altında gerçek pytest suite yazılırken) bu dosyalardaki
URL'ler, edge case'ler ve davranış beklentileri referans olarak kullanılabilir.

## İçindekiler

- `test_scraper.py`, `test_selenium.py`, `test_uc.py`, `test_pw.py`, `test_headless.py`,
  `test_minimize.py`, `test_hb.py`, `test_error.py`, `test_run.py`, `test_fix.py`
  — scraper davranış denemeleri (driver kurulumu, sayfa açma, fiyat çekme)
- `test_quota_fix.py` — kullanıcı quota mantığını manuel doğrulama
