"""Pruebas funcionales y de integración de la API de Orquídea Smart."""
import pytest
from fastapi.testclient import TestClient
from app import make_app


@pytest.fixture
def client(tmp_path):
    app = make_app(database_url=f'sqlite:///{tmp_path / "test_orquideas.db"}',
                   sensor_key='clave-de-pruebas', admin_password='administrador-secreto')
    with TestClient(app) as c:
        yield c


def sensor(client, humidity=80, temperature=21):
    return client.post('/api/measurements', json={'humidity': humidity, 'temperature': temperature},
                       headers={'X-API-Key': 'clave-de-pruebas'})


def login(client):
    assert client.post('/api/login', json={'password': 'administrador-secreto'}).status_code == 200


def test_paginas_existen_y_panel_protegido(client):
    assert client.get('/simulador').status_code == 200
    assert 'Simula. Experimenta.' in client.get('/simulador').text
    assert client.get('/').url.path == '/login'
    assert client.post('/api/login', json={'password': 'incorrecta'}).status_code == 401
    login(client)
    assert client.get('/').status_code == 200
    assert 'Calendario de riego' in client.get('/').text


def test_estado_inicial_sin_datos(client):
    payload = client.get('/api/overview').json()
    assert payload['latest'] is None
    assert payload['history_count'] == 0
    assert payload['history_interval'] == 1


def test_sensor_exige_clave(client):
    assert client.post('/api/measurements', json={'humidity': 80, 'temperature': 21}).status_code == 401
    assert client.post('/api/measurements', json={'humidity': 80, 'temperature': 21},
                       headers={'X-API-Key': 'equivocada'}).status_code == 401
    assert sensor(client).status_code == 200


@pytest.mark.parametrize('data', [
    {'humidity': -2, 'temperature': 20}, {'humidity': 101, 'temperature': 20},
    {'humidity': 80, 'temperature': 100}, {'humidity': 80, 'temperature': -50},
    {'humidity': '80', 'temperature': 20}, {'humidity': True, 'temperature': 21},
    {'humidity': 80}, {'humidity': 80, 'temperature': None},
])
def test_rechaza_mediciones_invalidas(client, data):
    response = client.post('/api/measurements', json=data, headers={'X-API-Key': 'clave-de-pruebas'})
    assert response.status_code == 400
    assert client.get('/api/overview').json()['history_count'] == 0


def test_actualizacion_tiempo_real_e_historial_por_intervalo(client):
    first = sensor(client, 80, 21).json()
    assert first['stored_in_history'] is True
    second = sensor(client, 42, 31).json()
    assert second['stored_in_history'] is False
    overview = client.get('/api/overview').json()
    assert overview['latest']['humidity'] == 42
    assert overview['latest']['temperature'] == 31
    assert overview['history_count'] == 1
    history = client.get('/api/history').json()['items']
    assert len(history) == 1 and history[0]['humidity'] == 80


def test_configuracion_permite_guardar_cada_lectura(client):
    forbidden = client.put('/api/settings', json={'history_interval': 0})
    assert forbidden.status_code == 401
    login(client)
    response = client.put('/api/settings', json={'history_interval': 0})
    assert response.status_code == 200
    sensor(client, 75, 18)
    sensor(client, 85, 24)
    assert client.get('/api/overview').json()['history_count'] == 2
    assert len(client.get('/api/history').json()['items']) == 2


def test_configuracion_intervalo_invalido(client):
    login(client)
    for value in (-1, 4, 120, '1', True):
        assert client.put('/api/settings', json={'history_interval': value}).status_code == 400


def test_calendario_crear_marcar_regado_y_eliminar(client):
    data = {'name': 'Riego lunes miércoles', 'weekdays': [0, 2], 'time': '09:30'}
    assert client.post('/api/watering', json=data).status_code == 401
    login(client)
    created = client.post('/api/watering', json=data)
    assert created.status_code == 200
    item_id = created.json()['id']
    watering = client.get('/api/overview').json()['watering']
    assert len(watering) == 1 and watering[0]['weekdays'] == [0, 2]
    assert watering[0]['last_watered'] is None
    assert client.post(f'/api/watering/{item_id}/complete').status_code == 200
    assert client.get('/api/overview').json()['watering'][0]['last_watered'] is not None
    assert client.delete(f'/api/watering/{item_id}').status_code == 200
    assert client.get('/api/overview').json()['watering'] == []


def test_calendario_valida_dias_y_hora(client):
    login(client)
    invalid = [
        {'name': 'A', 'weekdays': [], 'time': '09:00'},
        {'name': 'A', 'weekdays': [7], 'time': '09:00'},
        {'name': 'A', 'weekdays': [0, 0], 'time': '09:00'},
        {'name': 'A', 'weekdays': [0], 'time': '25:00'},
        {'name': '', 'weekdays': [0], 'time': '09:00'},
    ]
    for item in invalid:
        assert client.post('/api/watering', json=item).status_code == 400
    assert client.delete('/api/watering/9000').status_code == 404
    assert client.post('/api/watering/9000/complete').status_code == 404


def test_cors_permite_simulador_publicado(client):
    response = client.options('/api/measurements', headers={
        'Origin': 'https://ejemplo.github.io',
        'Access-Control-Request-Method': 'POST',
        'Access-Control-Request-Headers': 'content-type,x-api-key',
    })
    assert response.status_code == 200
    assert response.headers['access-control-allow-origin'] == '*'
    assert 'x-api-key' in response.headers['access-control-allow-headers'].lower()


def test_estado_salud_y_limites_consulta(client):
    assert client.get('/api/health').json()['ok'] is True
    assert client.get('/api/history?limit=0').status_code == 400
    assert client.get('/api/history?limit=501').status_code == 400
    assert client.get('/api/history?limit=10').status_code == 200


def test_sesion_cierre(client):
    login(client)
    assert client.get('/').status_code == 200
    assert client.post('/api/logout').status_code == 200
    assert client.get('/').url.path == '/login'
