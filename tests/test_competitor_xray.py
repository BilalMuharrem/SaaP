"""
tests/test_competitor_xray.py — Rakip Röntgeni (Competitor X-Ray) özelliği.

BMK özgünlük özelliği: tek rakip ürün için stratejik teşhis + karşı hamle.
AI sentezi (generate_competitor_xray) mock'lu; rota auth + validasyon testleri.
"""
import json


# ── AI sentezi (Groq mock'lu) ────────────────────────────────────────────────

def test_xray_synthesis_parses_structured_output(monkeypatch):
    """generate_competitor_xray geçerli AI JSON'ını 5 alanlı dict'e çevirir."""
    from services.ai import competitor_xray as cx

    fake_json = json.dumps({
        "neden_kazaniyor": ["hızlı kargo", "kaliteli plastik"],
        "zayif_noktalar": ["müşteriler kurulumun zor olduğunu söylüyor"],
        "fiyat_stratejisi": "Orta-üst segment, 4199 TL.",
        "karsi_hamleler": ["kurulum videosu ekle", "kargo garantisi ver"],
        "ozet": "Rakip fiyat-performansta güçlü ama kurulum desteğinde zayıf.",
    })

    class _Msg:
        content = fake_json
    class _Choice:
        message = _Msg()
    class _Resp:
        choices = [_Choice()]
    class _FakeClient:
        def __init__(self, *a, **k): pass
        class chat:
            class completions:
                @staticmethod
                def create(*a, **k): return _Resp()

    monkeypatch.setattr(cx, "resolve_groq_key", lambda *a, **k: "test-key-xxxxxxxxxxxxxxxx")
    monkeypatch.setattr("groq.Groq", _FakeClient)

    data = {"platform": "Trendyol", "name": "Test Ürün", "price": "4199.00",
            "seller": "TestSeller", "rating": 4.6, "review_count": 21,
            "reviews": ["çok güzel ürün hızlı kargo", "kurulumu biraz zor oldu"]}
    res = cx.generate_competitor_xray(data)

    assert "error" not in res
    assert res["neden_kazaniyor"] == ["hızlı kargo", "kaliteli plastik"]
    assert len(res["karsi_hamleler"]) == 2
    assert res["ozet"].startswith("Rakip")


def test_xray_synthesis_needs_reviews():
    """Yorum yoksa röntgen üretilemez (hata döner, AI çağrılmaz)."""
    from services.ai import competitor_xray as cx
    res = cx.generate_competitor_xray({"name": "X", "reviews": []},
                                      api_key="test-key-xxxxxxxxxxxxxxxx")
    assert "error" in res


def test_xray_synthesis_needs_api_key(monkeypatch):
    from services.ai import competitor_xray as cx
    monkeypatch.setattr(cx, "resolve_groq_key", lambda *a, **k: "")
    res = cx.generate_competitor_xray({"name": "X", "reviews": ["a review here long enough"]})
    assert "error" in res and "Anahtar" in res["error"]


# ── Rota (auth + validasyon) ─────────────────────────────────────────────────

def test_xray_page_requires_login(client):
    r = client.get('/rakip-rontgeni', follow_redirects=False)
    assert r.status_code == 302  # login'e yönlendirir


def test_xray_page_renders_for_user(auth_client):
    r = auth_client.get('/rakip-rontgeni')
    assert r.status_code == 200
    assert 'Rakip Röntgeni' in r.data.decode('utf-8')


def test_xray_rejects_invalid_url(auth_client, monkeypatch):
    """Trendyol/HB olmayan link reddedilir, job oluşmaz."""
    import worker
    monkeypatch.setattr(worker.process_job_task, 'delay', lambda *a, **k: None)
    r = auth_client.post('/rakip-rontgeni', data={'url': 'https://amazon.com/x'},
                         follow_redirects=True)
    body = r.data.decode('utf-8')
    assert 'Trendyol' in body or 'geçerli' in body.lower()


def test_xray_creates_job_for_valid_url(auth_client, monkeypatch):
    """Geçerli TY linki → xray job oluşturulur."""
    import worker
    monkeypatch.setattr(worker.process_job_task, 'delay', lambda *a, **k: None)
    r = auth_client.post('/rakip-rontgeni',
                         data={'url': 'https://www.trendyol.com/x-p-927230515'},
                         follow_redirects=False)
    assert r.status_code == 302
    from models import Job
    job = Job.query.filter_by(job_type='xray').first()
    assert job is not None
