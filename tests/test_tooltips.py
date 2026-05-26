"""Faz 7E: Tooltip help sistemi entegrasyon testleri."""


def test_tooltip_macro_renders(app):
    """Macro doğrudan render edildiğinde help-tip class'ı ve data-tip attr'ı çıkar."""
    with app.app_context():
        from flask import render_template_string
        html = render_template_string(
            "{% from '_macros/tooltips.html' import tooltip %}{{ tooltip('Test açıklama') }}"
        )
        assert 'help-tip' in html
        assert 'data-tip="Test açıklama"' in html
        assert 'tabindex="0"' in html
        assert 'aria-label="Bilgi: Test açıklama"' in html


def test_tooltip_right_position(app):
    """pos='right' modifier'ı tip-right class'ı eklemeli."""
    with app.app_context():
        from flask import render_template_string
        html = render_template_string(
            "{% from '_macros/tooltips.html' import tooltip %}{{ tooltip('X', pos='right') }}"
        )
        assert 'tip-right' in html


def test_new_request_has_tooltips(auth_client):
    """/new-request job_type kartlarında tooltip var."""
    r = auth_client.get('/new-request')
    assert r.status_code == 200
    body = r.data.decode('utf-8')
    assert 'help-tip' in body
    # En az 5 tooltip beklenir (Analiz Türü başlık + 4 alt tür)
    assert body.count('help-tip') >= 5


def test_seo_tracker_has_keyword_tooltip(auth_client):
    """/seo-tracker'da arama kelimesi tooltip'i var."""
    r = auth_client.get('/seo-tracker')
    assert r.status_code == 200
    body = r.data.decode('utf-8')
    assert 'help-tip' in body
    assert 'uzun kuyruklu' in body.lower() or 'long-tail' in body.lower() or 'hedefli' in body.lower()
