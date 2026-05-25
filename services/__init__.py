"""
services/ — İş katmanı modülleri.

worker.py'nin parça parça parçalanması burada başlar. Mevcut alt modüller:
    services/scraping/browser.py       — UA havuzu, header üretimi
    services/scraping/proxy.py         — PROXY_URL env çözümleyici
    services/scraping/blocked.py       — bot algılama yardımcıları
    services/notifications/classifier.py — bildirim kategori sınıflandırma
    services/ai/groq.py                — Groq API key fallback chain

Worker.py'deki büyük scraper'lar (Trendyol, HB, N11, vb.), stok ve yorum
çıkarıcılar geçici olarak hâlâ worker.py'de. İncremental refactor için
GELECEK FAZ kapsamında ele alınacak.
"""
