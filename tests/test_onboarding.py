"""Faz 5A: Onboarding wizard testleri."""
from extensions import db
from models import User, TrackedProduct


def test_login_redirects_new_user_to_onboarding(client, starter_user):
    """Yeni (onboarding_completed=False) kullanıcı login sonrası /onboarding'e gider."""
    starter_user.onboarding_completed = False
    db.session.commit()

    r = client.post('/login', data={
        'email': starter_user.email,
        'password': 'testpass123',
    }, follow_redirects=False)
    assert r.status_code == 302
    assert '/onboarding' in r.headers['Location']


def test_login_redirects_returning_user_to_dashboard(client, starter_user):
    """Onboarding tamamlanmış kullanıcı login sonrası /dashboard'a gider."""
    starter_user.onboarding_completed = True
    db.session.commit()

    r = client.post('/login', data={
        'email': starter_user.email,
        'password': 'testpass123',
    }, follow_redirects=False)
    assert r.status_code == 302
    assert '/dashboard' in r.headers['Location']


def test_onboarding_start_renders(auth_client):
    r = auth_client.get('/onboarding')
    assert r.status_code == 200
    assert 'Hoşgeldin'.encode('utf-8') in r.data


def test_onboarding_skip_marks_completed(auth_client, starter_user):
    """/onboarding/skip → onboarding_completed=True ve dashboard redirect."""
    assert starter_user.onboarding_completed is False

    r = auth_client.get('/onboarding/skip')
    assert r.status_code == 302
    assert '/dashboard' in r.headers['Location']

    # DB'den taze çek
    db.session.refresh(starter_user)
    assert starter_user.onboarding_completed is True


def test_onboarding_redirects_when_already_completed(auth_client, starter_user):
    """Onboarding tamamlandıysa /onboarding → /dashboard."""
    starter_user.onboarding_completed = True
    db.session.commit()

    r = auth_client.get('/onboarding')
    assert r.status_code == 302
    assert '/dashboard' in r.headers['Location']


def test_onboarding_product_post_creates_tracked(auth_client, starter_user):
    """POST /onboarding/product → TrackedProduct oluşur, /onboarding/cost'a yönlenir."""
    test_url = 'https://www.trendyol.com/test-marka/test-urun-p-99999'

    r = auth_client.post('/onboarding/product', data={'url': test_url},
                         follow_redirects=False)
    assert r.status_code == 302
    assert '/onboarding/cost/' in r.headers['Location']

    tp = TrackedProduct.query.filter_by(
        user_id=starter_user.id, url=test_url
    ).first()
    assert tp is not None
    assert tp.is_base_product is True
    assert tp.is_price_tracked is True


def test_onboarding_product_rejects_bad_url(auth_client):
    """Geçersiz URL → form yeniden render edilir."""
    r = auth_client.post('/onboarding/product', data={'url': 'not-a-url'})
    assert r.status_code == 200  # form yeniden render
    assert 'geçerli bir ürün linki'.encode('utf-8') in r.data.lower() \
        or b'gecerli' in r.data.lower() \
        or b'http' in r.data


def test_onboarding_cost_post_saves_unit_cost(auth_client, starter_user):
    """POST /onboarding/cost/<id> → unit_cost kaydedilir, /done'a yönlenir."""
    tp = TrackedProduct(
        user_id=starter_user.id,
        url='https://www.trendyol.com/test-p-1',
        is_base_product=True,
        is_price_tracked=True,
        tracking_type='price',
    )
    db.session.add(tp)
    db.session.commit()

    r = auth_client.post(f'/onboarding/cost/{tp.id}', data={'unit_cost': '149.90'},
                         follow_redirects=False)
    assert r.status_code == 302
    assert '/onboarding/done' in r.headers['Location']

    db.session.refresh(tp)
    assert tp.unit_cost == 149.90


def test_onboarding_done_marks_completed(auth_client, starter_user):
    """/onboarding/done sayfasını ziyaret etmek onboarding'i tamamlar."""
    r = auth_client.get('/onboarding/done')
    assert r.status_code == 200

    db.session.refresh(starter_user)
    assert starter_user.onboarding_completed is True
