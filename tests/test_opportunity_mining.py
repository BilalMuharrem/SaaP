"""
tests/test_opportunity_mining.py — Fırsat Avcısı (Yorum Madenciliği → Fırsat).

Özgünlük özelliği #2: rakip yorumlarından karşılanmamış ihtiyaç + ürün fırsatı.
AI sentezi (generate_opportunity_report) mock'lu; rota auth + validasyon.
"""
import json


def test_opportunity_synthesis_parses_output(monkeypatch):
    from services.ai import opportunity_mining as om

    fake = json.dumps({
        "karsilanmayan_ihtiyaclar": ["müşteriler sensör istiyor ama yok"],
        "sik_sikayetler": ["kurulum zor"],
        "firsatlar": ["sensörlü zamanlayıcı ekle — çünkü en çok istenen"],
        "oncelik": "Önce sensörlü zamanlayıcı — en çok tekrar eden talep.",
        "ozet": "Sensör ve kolay kurulum pazar boşluğu.",
    })

    class _Msg: content = fake
    class _Choice: message = _Msg()
    class _Resp: choices = [_Choice()]
    class _FakeClient:
        def __init__(self, *a, **k): pass
        class chat:
            class completions:
                @staticmethod
                def create(*a, **k): return _Resp()

    monkeypatch.setattr(om, "resolve_groq_key", lambda *a, **k: "test-key-xxxxxxxxxxxxxxxx")
    monkeypatch.setattr("groq.Groq", _FakeClient)

    data = {"platform": "Trendyol", "name": "Test", "reviews": ["sensör olsa keşke", "kurulum zordu"]}
    res = om.generate_opportunity_report(data)
    assert "error" not in res
    assert res["firsatlar"] == ["sensörlü zamanlayıcı ekle — çünkü en çok istenen"]
    assert res["oncelik"].startswith("Önce")


def test_opportunity_needs_reviews():
    from services.ai import opportunity_mining as om
    res = om.generate_opportunity_report({"name": "X", "reviews": []},
                                         api_key="test-key-xxxxxxxxxxxxxxxx")
    assert "error" in res


def test_opportunity_page_requires_login(client):
    r = client.get('/firsat-avcisi', follow_redirects=False)
    assert r.status_code == 302


def test_opportunity_page_renders(auth_client):
    r = auth_client.get('/firsat-avcisi')
    assert r.status_code == 200
    assert 'Fırsat Avcısı' in r.data.decode('utf-8')


def test_opportunity_rejects_invalid_url(auth_client, monkeypatch):
    import worker
    monkeypatch.setattr(worker.process_job_task, 'delay', lambda *a, **k: None)
    r = auth_client.post('/firsat-avcisi', data={'url': 'https://amazon.com/x'},
                         follow_redirects=True)
    body = r.data.decode('utf-8')
    assert 'Trendyol' in body or 'geçerli' in body.lower()


def test_opportunity_creates_job(auth_client, monkeypatch):
    import worker
    monkeypatch.setattr(worker.process_job_task, 'delay', lambda *a, **k: None)
    r = auth_client.post('/firsat-avcisi',
                         data={'url': 'https://www.trendyol.com/x-p-927230515'},
                         follow_redirects=False)
    assert r.status_code == 302
    from models import Job
    assert Job.query.filter_by(job_type='opportunity').first() is not None
