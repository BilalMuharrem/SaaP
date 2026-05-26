"""Faz 9: /healthz ve /healthz/deep sağlık kontrolü."""


def test_liveness_returns_200(client):
    """/healthz — sade liveness, auth gerektirmez."""
    r = client.get('/healthz')
    assert r.status_code == 200
    assert r.data == b'ok'


def test_liveness_no_auth_needed(client):
    """Anonim erişim — login'e redirect olmamalı."""
    r = client.get('/healthz', follow_redirects=False)
    assert r.status_code == 200
    assert 'Location' not in r.headers


def test_readiness_returns_status_and_checks(client):
    """/healthz/deep — DB + Redis durumu döner."""
    r = client.get('/healthz/deep')
    # Status 200 (ok) veya 503 (degraded) olabilir — test'te Redis kapalı olabilir
    assert r.status_code in (200, 503)

    data = r.get_json()
    assert 'status' in data
    assert 'checks' in data
    assert 'db' in data['checks']
    assert 'redis' in data['checks']
    # SQLite in-memory test DB her zaman OK olmalı
    assert data['checks']['db'] is True
