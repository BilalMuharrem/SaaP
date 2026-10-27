"""
services/ai/competitor_xray.py — "Rakip Röntgeni" stratejik AI sentezi.

BMK'nın özgünlük çekirdeği: rakip ürün verisini (fiyat + satıcı + rating +
yorumlar) alıp PASİF VERİ yerine SALDIRI İSTİHBARATI üretir:
  • neden_kazaniyor  — rakibin güçlü yanları (yorumlardan + fiyat/rating)
  • zayif_noktalar    — saldırı yüzeyi (müşteri şikayetleri = senin fırsatın)
  • fiyat_stratejisi  — rakibin fiyat konumu yorumu
  • karsi_hamleler    — KULLANICIYA ÖZEL somut aksiyonlar (asıl değer)
  • ozet              — tek paragraf stratejik özet

Not: Çıktı JSON. gpt-oss modelleri için reasoning_effort=low + metinden JSON
parse (katı json_object modu gpt-oss'ta 400 veriyor — bkz. HOTFIX ai).
"""
import json
import re

from .groq import resolve_groq_key, resolve_groq_model, groq_extra_params


def _parse_json_block(raw):
    """Ham AI metninden ilk {...} JSON bloğunu güvenle çıkarır."""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        pass
    m = re.search(r'\{[\s\S]*\}', raw)
    if not m:
        return None
    candidate = re.sub(r',\s*([}\]])', r'\1', m.group(0))  # trailing comma onar
    try:
        return json.loads(candidate)
    except Exception:
        return None


def _as_list(v, cap=6):
    if isinstance(v, list):
        out = [str(x).strip() for x in v if str(x).strip()]
        return out[:cap]
    if isinstance(v, str) and v.strip():
        return [v.strip()]
    return []


def generate_competitor_xray(data, api_key=None, max_reviews=25):
    """Rakip ürün verisinden stratejik röntgen üretir.

    Args:
        data: {platform, name, price, seller, rating, review_count, reviews[list]}
        api_key: opsiyonel kullanıcı anahtarı (yoksa sistem anahtarına düşer)
        max_reviews: AI'ya gönderilecek yorum üst sınırı (token kontrolü)

    Returns: dict — {neden_kazaniyor, zayif_noktalar, fiyat_stratejisi,
             karsi_hamleler, ozet} veya {"error": "..."}
    """
    key = resolve_groq_key(api_key)
    if not key:
        return {"error": "API Anahtarı eksik. Ayarlar'dan Groq anahtarı girin veya admin sistem anahtarı tanımlasın."}

    name = (data.get("name") or "Rakip ürün").strip()
    platform = data.get("platform") or "?"
    price = data.get("price")
    seller = data.get("seller") or "bilinmiyor"
    rating = data.get("rating")
    review_count = data.get("review_count")
    reviews = [r for r in (data.get("reviews") or []) if isinstance(r, str) and r.strip()]
    sample = reviews[:max_reviews]

    if not sample:
        return {"error": "Yorum verisi alınamadığı için röntgen çıkarılamadı."}

    reviews_blob = "\n".join(f"- {r[:300]}" for r in sample)

    system_prompt = (
        "Sen bir e-ticaret rekabet istihbaratı analistisin. Türk pazaryeri "
        "(Trendyol/Hepsiburada) satıcılarına, RAKİPLERİNİN ürünlerini analiz edip "
        "'neden kazandıklarını, zayıf noktalarını ve bu rakibe karşı NE YAPMALARI "
        "gerektiğini' söylersin. Pasif veri değil, uygulanabilir SALDIRI planı üretirsin. "
        "Sadece verilen gerçek yorumlara ve verilere dayan; uydurma yapma. "
        "Yanıtı YALNIZCA tek bir JSON objesi olarak ver, başka metin yazma."
    )

    user_prompt = f"""RAKİP ÜRÜN VERİSİ
Platform: {platform}
Ürün: {name}
Fiyat: {price}
Buybox satıcısı: {seller}
Puan: {rating} / Yorum sayısı: {review_count}

MÜŞTERİ YORUMLARI (örneklem {len(sample)} adet):
{reviews_blob}

GÖREV — şu JSON şemasını doldur (Türkçe, somut, bu ürüne özel):
{{
  "neden_kazaniyor": ["rakibin GÜÇLÜ yanı (yorumlarda beğenilen + fiyat/puan avantajı), en fazla 5 madde"],
  "zayif_noktalar": ["müşterilerin ŞİKAYET ettiği, senin fırsatın olan açıklar; her madde 'müşteriler X diyor' formatında, en fazla 5"],
  "fiyat_stratejisi": "rakibin fiyat konumu ve bunun ne anlama geldiği — 1-2 cümle",
  "karsi_hamleler": ["SANA özel, uygulanabilir somut aksiyon (ör. ürün açıklamanda şunu vurgula, şu özelliği ekle, şu şikayeti çöz), en fazla 5 madde"],
  "ozet": "tek paragraf stratejik özet: bu rakiple nasıl yarışırsın"
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
            temperature=0.3,
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
        "neden_kazaniyor": _as_list(parsed.get("neden_kazaniyor")),
        "zayif_noktalar": _as_list(parsed.get("zayif_noktalar")),
        "fiyat_stratejisi": str(parsed.get("fiyat_stratejisi") or "").strip(),
        "karsi_hamleler": _as_list(parsed.get("karsi_hamleler")),
        "ozet": str(parsed.get("ozet") or "").strip(),
    }
