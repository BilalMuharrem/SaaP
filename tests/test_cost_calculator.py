"""
tests/test_cost_calculator.py — Maliyet/Başabaş Hesaplayıcı (Mod 2).

Satıcı alış fiyatı + kategori + kanal + kargo + KDV girer → komisyon ve KDV dahil
"başabaş satış fiyatı" hesaplanır ve unit_cost olarak saklanır (kâr/zarar buna göre).
"""
import json

from extensions import db
from models import TrackedProduct
from services.pricing.commission import compute_breakeven, resolve_commission, CATEGORY_RATES


# ── Hesap birimi ──────────────────────────────────────────────────────────────

def test_breakeven_basic():
    """(alış+kargo)/(1-kesinti). petshop hb=16, kdv=20 → kesinti 36%."""
    r = compute_breakeven(100, 'petshop', channel='hepsiburada', shipping=0, vat_included=True)
    # komisyon 16 + kdv 20 = 36 → 100/0.64 = 156.25
    assert r['commission_pct'] == 16
    assert abs(r['breakeven'] - 156.25) < 0.01


def test_breakeven_vat_off_lower():
    """KDV kapalıyken başabaş düşer."""
    on = compute_breakeven(100, 'petshop', channel='hepsiburada', vat_included=True)['breakeven']
    off = compute_breakeven(100, 'petshop', channel='hepsiburada', vat_included=False)['breakeven']
    assert off < on


def test_channel_unknown_uses_higher():
    """Kanal belirsiz → TY/HB'den YÜKSEK komisyon."""
    comm, kdv, note = resolve_commission('petshop', '')
    cat = next(c for c in CATEGORY_RATES if c['key'] == 'petshop')
    assert comm == max(cat['ty'], cat['hb'])


def test_commission_override():
    """Kullanıcı komisyon girerse o kullanılır."""
    r = compute_breakeven(100, 'petshop', channel='trendyol', vat_included=False,
                          commission_override=5)
    assert r['commission_pct'] == 5
    assert abs(r['breakeven'] - (100 / 0.95)) < 0.01


def test_breakeven_invalid_purchase():
    assert 'error' in compute_breakeven(0, 'petshop')
    assert 'error' in compute_breakeven(-5, 'petshop')
    assert 'error' in compute_breakeven('abc', 'petshop')


def test_shipping_added():
    base = compute_breakeven(100, 'petshop', channel='trendyol', vat_included=False)['breakeven']
    with_ship = compute_breakeven(100, 'petshop', channel='trendyol', shipping=20, vat_included=False)['breakeven']
    assert with_ship > base


# ── Rota (Mod 2) ──────────────────────────────────────────────────────────────

def _make_group(user, gid='cost-grp'):
    tp = TrackedProduct(user_id=user.id, url='https://www.trendyol.com/x-p-9',
                        is_price_tracked=True, group_id=gid, is_base_product=True,
                        current_price=5000.0)
    db.session.add(tp)
    db.session.commit()
    return tp.id


def test_mode_calc_stores_breakeven(enterprise_client, enterprise_user, app):
    """Mod 2 POST → unit_cost = başabaş + cost_meta JSON saklanır."""
    with app.app_context():
        tp_id = _make_group(enterprise_user, 'cg1')

    r = enterprise_client.post('/tracked-products/group/cg1/cost', data={
        'cost_mode': 'calc', 'purchase_price': '150', 'category': 'petshop',
        'channel': 'hepsiburada', 'shipping_cost': '0', 'vat_included': '1',
    }, follow_redirects=False)
    assert r.status_code == 302

    with app.app_context():
        tp = db.session.get(TrackedProduct, tp_id)
        # 150 / (1-0.36) = 234.375
        assert abs(tp.unit_cost - 234.38) < 0.1
        meta = json.loads(tp.cost_meta)
        assert meta['mode'] == 'calc'
        assert meta['category'] == 'petshop'


def test_mode_direct_clears_meta(enterprise_client, enterprise_user, app):
    """Mod 1 (doğrudan) → unit_cost set, cost_meta temizlenir."""
    with app.app_context():
        tp_id = _make_group(enterprise_user, 'cg2')
        db.session.get(TrackedProduct, tp_id).cost_meta = '{"mode":"calc"}'
        db.session.commit()

    enterprise_client.post('/tracked-products/group/cg2/cost', data={
        'cost_mode': 'direct', 'unit_cost': '99.90',
    }, follow_redirects=False)
    with app.app_context():
        tp = db.session.get(TrackedProduct, tp_id)
        assert abs(tp.unit_cost - 99.90) < 0.01
        assert tp.cost_meta is None
