"""
services/ai/opportunity_mining.py — "Fırsat Avcısı" (Yorum Madenciliği → Fırsat).

BMK özgünlük özelliği #2: rakip yorumlarından KARŞILANMAMIŞ İHTİYAÇLARI çıkarır.
Rakip Röntgeni "bu rakibi nasıl yenerim" der; bu modül "NE ÜRETMELİ / NE SUNMALI"
der — izleme aracından FIRSAT KEŞİF aracına geçiş.

Çıktı (JSON):
  • karsilanmayan_ihtiyaclar — müşterilerin istediği ama bulamadığı şeyler
  • sik_sikayetler          — tekrarlayan acı noktaları (doldurulacak boşluklar)
  • firsatlar               — somut ürün/özellik fırsatları + aksiyon
  • oncelik                 — ilk ele alınacak #1 fırsat
  • ozet                    — tek paragraf özet

Not: gpt-oss uyumlu (reasoning_effort + metinden JSON parse) — HOTFIX ai ile aynı.
Küçük JSON/liste yardımcıları competitor_xray'den yeniden kullanılır.
"""
from .groq import resolve_groq_key, resolve_groq_model, groq_extra_params
from .competitor_xray import _parse_json_block, _as_list


def generate_opportunity_report(data, api_key=None, max_reviews=30):
    """Rakip ürün yorumlarından ürün/fırsat raporu üretir.

    Args:
        data: {platform, name, price, seller, rating, review_count, reviews[list]}
        api_key: opsiyonel kullanıcı anahtarı (yoksa sistem anahtarına düşer)
        max_reviews: AI'ya gönderilecek yorum üst sınırı

    Returns: dict — {karsilanmayan_ihtiyaclar, sik_sikayetler, firsatlar,
             oncelik, ozet} veya {"error": "..."}
    """
    key = resolve_groq_key(api_key)
    if not key:
        return {"error": "API Anahtarı eksik. Ayarlar'dan Groq anahtarı girin veya admin sistem anahtarı tanımlasın."}

    name = (data.get("name") or "Ürün").strip()
    platform = data.get("platform") or "?"
    reviews = [r for r in (data.get("reviews") or []) if isinstance(r, str) and r.strip()]
    sample = reviews[:max_reviews]
    if not sample:
        return {"error": "Yorum verisi alınamadığı için fırsat analizi yapılamadı."}

    reviews_blob = "\n".join(f"- {r[:300]}" for r in sample)

    system_prompt = (
        "Sen bir ürün stratejisi ve pazar fırsatı analistisin. Türk pazaryeri "
        "satıcılarına, RAKİP ürünlerin müşteri yorumlarını okuyup 'müşterilerin "
        "istediği ama bulamadığı şeyleri (karşılanmamış ihtiyaçlar) ve bunları "
        "DOLDURARAK nasıl fırsat yaratabileceklerini' söylersin. Amacın rakibi "
        "kötülemek değil, YENİ/İYİLEŞTİRİLMİŞ ürün fırsatı keşfetmek. Sadece verilen "
        "gerçek yorumlara dayan; uydurma yapma. Yanıtı YALNIZCA tek bir JSON objesi "
        "olarak ver, başka metin yazma."
    )

    user_prompt = f"""ÜRÜN: {name} ({platform})

MÜŞTERİ YORUMLARI (örneklem {len(sample)} adet):
{reviews_blob}

GÖREV — bu yorumlardan ÜRÜN FIRSATLARINI çıkar. Şu JSON şemasını doldur
(Türkçe, somut, bu ürüne/kategoriye özel):
{{
  "karsilanmayan_ihtiyaclar": ["müşterilerin İSTEDİĞİ ama bu üründe bulamadığı şey; 'müşteriler X istiyor ama yok' formatında, en fazla 5"],
  "sik_sikayetler": ["birden fazla yorumda tekrar eden acı noktası (doldurulabilir boşluk), en fazla 5"],
  "firsatlar": ["SOMUT ürün/özellik fırsatı + ne yapılmalı; ör. 'sensörlü otomatik zamanlayıcı ekle — çünkü X', en fazla 5"],
  "oncelik": "ilk ele alınması gereken #1 fırsat ve nedeni — 1-2 cümle",
  "ozet": "tek paragraf: bu üründe hangi pazar boşluğu var, nasıl değerlendirilir"
}}
Veri yetersizse ilgili listeyi kısa tut veya boş [] bırak; ASLA uydurma."""

    try:
        from groq import Groq
        client = Groq(api_key=key)
        resp = client.chat.completions.create(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            model=resolve_groq_model(),
            temperature=0.35,
            max_tokens=2000,
            **groq_extra_params(),
        )
        raw = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        return {"error": f"Yapay Zeka Hatası: {e}"}

    parsed = _parse_json_block(raw)
    if not isinstance(parsed, dict):
        return {"error": "AI yanıtı geçerli JSON döndürmedi."}

    return {
        "karsilanmayan_ihtiyaclar": _as_list(parsed.get("karsilanmayan_ihtiyaclar")),
        "sik_sikayetler": _as_list(parsed.get("sik_sikayetler")),
        "firsatlar": _as_list(parsed.get("firsatlar")),
        "oncelik": str(parsed.get("oncelik") or "").strip(),
        "ozet": str(parsed.get("ozet") or "").strip(),
    }
