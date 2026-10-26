"""
blueprints/legal.py — Yasal sayfalar (KVKK, Gizlilik, Kullanım Koşulları, Çerez).

Launch öncesi ZORUNLU yasal metinlerin barındığı public (auth'suz) rotalar.

⚠️ İÇERİK TASLAKTIR — halka açılıştan önce bir avukat/hukuk danışmanı tarafından
gözden geçirilmeli. Şablonlar hazır; metinler onaylandığında templates/legal/
altındaki dosyalar güncellenir. [KÖŞELİ PARANTEZ] içindeki yerler doldurulmalı.
"""
from flask import Blueprint, render_template

bp = Blueprint('legal', __name__)


@bp.route('/kullanim-kosullari')
def terms():
    return render_template('legal/kullanim_kosullari.html')


@bp.route('/gizlilik-politikasi')
def privacy():
    return render_template('legal/gizlilik.html')


@bp.route('/kvkk-aydinlatma')
def kvkk():
    return render_template('legal/kvkk.html')


@bp.route('/cerez-politikasi')
def cookies():
    return render_template('legal/cerez.html')
