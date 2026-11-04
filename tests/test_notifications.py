"""Pruebas de riego por correo sin enviar mensajes reales."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from app import make_app, watering
from notifications import dispatch_due, notification_events, make_occurrences

CHILE = ZoneInfo('America/Santiago')

def chile(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=CHILE).astimezone(timezone.utc)

@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('NOTIFICATION_JOB_KEY', 'clave-segura-para-programador-1234567890')
    monkeypatch.setenv('NOTIFICATION_EMAIL', 'orquidea.smart@gmail.com')
    sent = []
    app = make_app(database_url=f'sqlite:///{tmp_path / "test.db"}', admin_password='seguro',
                   email_sender=lambda subject, body, to: sent.append((subject, body, to)))
    with TestClient(app) as client:
        assert client.post('/api/login', json={'password':'seguro'}).status_code == 200
        yield app, client, sent

def add(client, name='Riego', days=[0, 2], hour='09:30'):
    response = client.post('/api/watering', json={'name':name,'weekdays':days,'time':hour})
    assert response.status_code == 200
    return response.json()['id']

def test_horario_y_no_duplicados(setup):
    app, client, sent = setup
    item_id = add(client)
    when = chile(2026, 11, 2, 9, 30)
    assert dispatch_due(app.state.db, watering, app.state.email_sender, now=when-timedelta(minutes=1))['sent'] == 0
    assert dispatch_due(app.state.db, watering, app.state.email_sender, now=when)['sent'] == 1
    assert dispatch_due(app.state.db, watering, app.state.email_sender, now=when+timedelta(minutes=2))['sent'] == 0
    assert len(sent) == 1 and sent[0][2] == 'orquidea.smart@gmail.com'
    assert 'Lunes 02-11-2026' in sent[0][1]
    with app.state.db.connect() as conn:
        events = conn.execute(select(notification_events)).mappings().all()
    assert len(events) == 1 and events[0]['watering_id'] == item_id

def test_dias_y_horas(setup):
    app, client, sent = setup
    add(client, 'Mañana', [0, 2], '09:30')
    add(client, 'Tarde', [0], '16:00')
    for moment in [chile(2026,11,2,9,30), chile(2026,11,2,16,0), chile(2026,11,4,9,30)]:
        assert dispatch_due(app.state.db, watering, app.state.email_sender, now=moment)['sent'] == 1
    assert len(sent) == 3

def test_apis_seguras_y_correo_prueba(setup):
    _, client, sent = setup
    assert client.get('/api/notifications/status').json()['email'] == 'orquidea.smart@gmail.com'
    assert client.post('/api/notifications/dispatch').status_code == 401
    assert client.post('/api/notifications/dispatch', headers={'X-Notification-Key':'incorrecta'}).status_code == 401
    result = client.post('/api/notifications/dispatch', headers={'X-Notification-Key':'clave-segura-para-programador-1234567890'})
    assert result.status_code == 200 and result.json()['sent'] == 0
    assert client.post('/api/notifications/test').status_code == 200
    assert len(sent) == 1
    client.post('/api/logout')
    assert client.get('/api/notifications/status').status_code == 401
    assert client.post('/api/notifications/test').status_code == 401

def test_eliminar_riego_no_manda_correo(setup):
    app, client, sent = setup
    identifier = add(client)
    assert client.delete(f'/api/watering/{identifier}').status_code == 200
    assert dispatch_due(app.state.db, watering, app.state.email_sender, now=chile(2026,11,2,9,30))['sent'] == 0
    assert not sent

def test_zona_horaria_chile():
    event={'id':1,'name':'Riego','weekdays':'[0]','time':'23:59','enabled':True}
    moment=chile(2026,11,2,23,59)
    assert len(list(make_occurrences([event],moment,30,CHILE))) == 1

def test_smoke_test_autenticado(setup):
    _, client, sent = setup
    assert client.post('/api/notifications/smoke-test').status_code == 401
    valid = {'X-Notification-Key': 'clave-segura-para-programador-1234567890'}
    result = client.post('/api/notifications/smoke-test', headers=valid)
    assert result.status_code == 200
    assert result.json()['accepted_by_provider'] is True
    assert len(sent) == 1
    assert sent[0][2] == 'orquidea.smart@gmail.com'
