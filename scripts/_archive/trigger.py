import os
import sys

# Dosya yolunu belirle (SaaS App içinde olduğumuzu varsayıyoruz)
sys.path.append(os.getcwd())

try:
    from app import app
    from worker import check_tracked_products_task
    from models import init_db

    print("🚀 Mevcut ürünlerin taraması başlatılıyor...")
    
    with app.app_context():
        # Önce veritabanı şemasının güncel olduğundan emin ol
        init_db(app)
        
        # Görevi asenkron olarak kuyruğa gönder
        check_tracked_products_task.delay()
        
    print("\n✅ BAŞARILI: Tüm eski ürünler analiz edilmek üzere kuyruğa alındı!")
    print("💡 Diğer terminaldeki (worker) siyah ekrana bakarsanız işlemleri orada görebilirsiniz.")
    
except Exception as e:
    print(f"\n❌ HATA OLUŞTU: {e}")
    print("\nLütfen terminalde 'cd \"SaaS App\"' klasöründe olduğunuzdan emin olun.")
