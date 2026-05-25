"""tests/test_seo_ai.py — SEO Tracker + AI Consultant feature gating."""


# ─────────────────────────────────────────────────────────────────────────────
# SEO TRACKER
# ─────────────────────────────────────────────────────────────────────────────

def test_seo_tracker_renders(auth_client):
    r = auth_client.get('/seo-tracker')
    assert r.status_code == 200


def test_seo_tracker_anon_redirects(client):
    r = client.get('/seo-tracker', follow_redirects=False)
    assert r.status_code == 302


def test_seo_graph_renders(auth_client):
    r = auth_client.get('/seo-graph')
    assert r.status_code == 200


def test_seo_tracker_rejects_hepsiburada(auth_client):
    """HB SEO yakında — POST reddedilir, yönlendirir."""
    r = auth_client.post('/seo-tracker', data={
        'platform': 'Hepsiburada',
        'keyword': 'test',
        'target_url': 'https://www.hepsiburada.com/test-p-HBV123',
    }, follow_redirects=False)
    assert r.status_code == 302


def test_seo_tracker_rejects_invalid_keyword(auth_client):
    r = auth_client.post('/seo-tracker', data={
        'platform': 'Trendyol',
        'keyword': 'x',  # 2 karakterden az
        'target_url': 'https://www.trendyol.com/x-p-1',
    }, follow_redirects=False)
    assert r.status_code == 302


def test_seo_tracker_rejects_non_http_url(auth_client):
    r = auth_client.post('/seo-tracker', data={
        'platform': 'Trendyol',
        'keyword': 'valid kelime',
        'target_url': 'not-a-url',
    }, follow_redirects=False)
    assert r.status_code == 302


# ─────────────────────────────────────────────────────────────────────────────
# AI CONSULTANT (Enterprise gating)
# ─────────────────────────────────────────────────────────────────────────────

def test_ai_consultant_starter_user_gets_page(auth_client):
    """Starter kullanıcı sayfayı görür ama içerik enterprise mesajı verir."""
    r = auth_client.get('/ai-consultant')
    # Sayfa açılır ama enterprise olmayan için boş portföy gösterir
    assert r.status_code == 200


def test_ai_consultant_enterprise_gets_full_page(enterprise_client):
    r = enterprise_client.get('/ai-consultant')
    assert r.status_code == 200


def test_ai_consultant_generate_blocks_starter(auth_client):
    """Starter user POST → redirect (enterprise olmadığı için)."""
    r = auth_client.post('/ai-consultant/generate', follow_redirects=False)
    assert r.status_code == 302
    # Hedef: ai_consultant sayfası (üst seviye redirect)
    assert '/ai-consultant' in r.headers['Location']


def test_ai_consultant_generate_enterprise_no_products(enterprise_client):
    """Enterprise user ama ürün yok → 'en az 1 ürün gerekli' flash."""
    r = enterprise_client.post('/ai-consultant/generate', follow_redirects=False)
    assert r.status_code == 302


def test_ai_consultant_pdf_blocks_starter(auth_client):
    r = auth_client.get('/analysis/1/download-pdf', follow_redirects=False)
    assert r.status_code == 302
    assert '/plans' in r.headers['Location']


def test_ai_consultant_standalone_blocks_starter(auth_client):
    r = auth_client.get('/ai-consultant/report/1', follow_redirects=False)
    assert r.status_code == 302
    assert '/plans' in r.headers['Location']


# ─────────────────────────────────────────────────────────────────────────────
# SEO TIPS API (Groq) — sahiplik
# ─────────────────────────────────────────────────────────────────────────────

def test_seo_tips_api_unknown_tracker_returns_404(auth_client):
    r = auth_client.get('/api/generate-seo-tips/999999')
    assert r.status_code == 404
    assert r.is_json
    data = r.get_json()
    assert data['success'] is False
