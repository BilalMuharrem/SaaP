"""
tests/test_hotfix_11_2.py — Trendyol yorum çıkarımı (gömülü JSON state).

Kök neden (canlı debug, 2026-10): TY yorum API host'ları public-mdc / public
DNS-dead, eski socialgw-service 556 dönüyor. /yorumlar sayfası ise 200 + yorumlar
"comment":"..." olarak sayfa-içi JSON state'inde gömülü. _extract_ty_comments_from_html
bu alanları çıkarır. Testler saf fonksiyonu izole doğrular (ağ yok).
"""
import worker


def test_extracts_embedded_comments():
    html = ('x {"comment":"Ürün çok güzel geldi hızlı kargo için teşekkürler"} '
            'y {"comment":"İkinci yorum da yeterince uzun bir metin içeriyor"}')
    out = worker._extract_ty_comments_from_html(html)
    assert len(out) == 2
    assert out[0].startswith('Ürün çok güzel')


def test_filters_too_short():
    """10 karakterden kısa yorumlar elenmeli (gürültü)."""
    html = '{"comment":"kısa"} {"comment":"bu yorum yeterince uzun geçerli bir yorum"}'
    out = worker._extract_ty_comments_from_html(html)
    assert out == ['bu yorum yeterince uzun geçerli bir yorum']


def test_decodes_json_escapes():
    """JSON \\uXXXX ve \\" escape'leri düzgün çözülmeli (bozuk karakter yok)."""
    # "Fiyat/performans" içinde kaçışlı tırnak + unicode
    html = r'{"comment":"Harika ürün, \"favorim\" oldu kesinlikle alın"}'
    out = worker._extract_ty_comments_from_html(html)
    assert len(out) == 1
    assert 'ürün' in out[0]
    assert '"favorim"' in out[0]


def test_empty_and_none_safe():
    assert worker._extract_ty_comments_from_html('') == []
    assert worker._extract_ty_comments_from_html(None) == []
    assert worker._extract_ty_comments_from_html('hiç json yok burada') == []


def test_preserves_order():
    html = ('{"comment":"birinci sıradaki uzun yorum metni burada"} '
            '{"comment":"ikinci sıradaki uzun yorum metni burada"} '
            '{"comment":"üçüncü sıradaki uzun yorum metni burada"}')
    out = worker._extract_ty_comments_from_html(html)
    assert out[0].startswith('birinci')
    assert out[2].startswith('üçüncü')
