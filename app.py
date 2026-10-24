"""Orquídea Smart — backend y panel web.

Inicio local: python app.py
Producción: uvicorn app:app --host 0.0.0.0 --port $PORT
"""
from __future__ import annotations

import json
import math
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import (
    Boolean, Column, DateTime, Float, Integer, MetaData, String, Table,
    Text, create_engine, delete, func, insert, select, update,
)

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / '.env')

metadata = MetaData()
readings = Table(
    'readings', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('humidity', Float, nullable=False),
    Column('temperature', Float, nullable=False),
    Column('source', String(50), nullable=False),
    Column('created_at', DateTime(timezone=True), nullable=False),
)
latest = Table(
    'latest_reading', metadata,
    Column('id', Integer, primary_key=True),
    Column('humidity', Float, nullable=False),
    Column('temperature', Float, nullable=False),
    Column('source', String(50), nullable=False),
    Column('created_at', DateTime(timezone=True), nullable=False),
)
settings_table = Table(
    'settings', metadata,
    Column('key', String(80), primary_key=True),
    Column('value', String(100), nullable=False),
)
watering = Table(
    'watering', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('name', String(90), nullable=False),
    Column('weekdays', String(40), nullable=False),
    Column('time', String(5), nullable=False),
    Column('enabled', Boolean, nullable=False, default=True),
)
water_logs = Table(
    'water_logs', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('watering_id', Integer, nullable=False),
    Column('created_at', DateTime(timezone=True), nullable=False),
)

VALID_INTERVALS = {0, 1, 5, 15, 30, 60}
TIME_RE = re.compile(r'^(?:[01]\d|2[0-3]):[0-5]\d$')


def utc_string(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def row_reading(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        'humidity': row.humidity,
        'temperature': row.temperature,
        'source': row.source,
        'created_at': utc_string(row.created_at),
    }


def error(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({'detail': message}, status_code=status)


def make_app(database_url: str | None = None, sensor_key: str | None = None,
             admin_password: str | None = None) -> FastAPI:
    url = database_url or os.getenv('DATABASE_URL') or f'sqlite:///{BASE / "orquideas.db"}'
    if url.startswith('postgres://'):
        url = 'postgresql+psycopg://' + url[len('postgres://'):]
    elif url.startswith('postgresql://'):
        url = 'postgresql+psycopg://' + url[len('postgresql://'):]

    engine_params: dict[str, Any] = {'pool_pre_ping': True}
    if url.startswith('sqlite:'):
        engine_params['connect_args'] = {'check_same_thread': False, 'timeout': 30}
    engine = create_engine(url, **engine_params)
    metadata.create_all(engine)
    with engine.begin() as conn:
        if conn.execute(select(settings_table.c.key).where(settings_table.c.key == 'history_interval')).first() is None:
            conn.execute(insert(settings_table).values(key='history_interval', value='1'))

    app = FastAPI(title='Orquídea Smart', docs_url=None, redoc_url=None)
    app.state.db = engine
    app.state.sensor_key = sensor_key if sensor_key is not None else os.getenv('SENSOR_API_KEY', 'cambia-esta-clave')
    app.state.admin_password = admin_password if admin_password is not None else os.getenv('ADMIN_PASSWORD', '')
    app.state.signer = URLSafeTimedSerializer(os.getenv('SECRET_KEY', 'solo-desarrollo-local-cambiar'))
    app.state.secure_cookie = os.getenv('SECURE_COOKIE', '0') == '1'

    origins_raw = os.getenv('ALLOWED_ORIGINS', '*')
    origins = [part.strip().rstrip('/') for part in origins_raw.split(',') if part.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=['GET', 'POST', 'DELETE', 'PUT', 'OPTIONS'],
        allow_headers=['Content-Type', 'X-API-Key'],
        allow_credentials=False,
    )

    def authorized(request: Request) -> bool:
        if not app.state.admin_password:
            return True
        cookie = request.cookies.get('orchid_session', '')
        try:
            return app.state.signer.loads(cookie, max_age=7 * 24 * 3600) == 'admin'
        except (BadSignature, SignatureExpired):
            return False

    def require_admin(request: Request) -> JSONResponse | None:
        if not authorized(request):
            return error('Inicia sesión en la página principal para administrar.', 401)
        return None

    def get_setting(conn, key: str, default: str = '') -> str:
        result = conn.execute(select(settings_table.c.value).where(settings_table.c.key == key)).scalar_one_or_none()
        return result if result is not None else default

    def list_watering(conn) -> list[dict[str, Any]]:
        result = conn.execute(select(watering).order_by(watering.c.time, watering.c.id)).mappings().all()
        items = []
        for entry in result:
            last = conn.execute(
                select(func.max(water_logs.c.created_at)).where(water_logs.c.watering_id == entry['id'])
            ).scalar_one_or_none()
            items.append({
                'id': entry['id'], 'name': entry['name'], 'weekdays': json.loads(entry['weekdays']),
                'time': entry['time'], 'enabled': bool(entry['enabled']), 'last_watered': utc_string(last),
            })
        return items

    @app.get('/')
    def home(request: Request):
        if not authorized(request):
            return RedirectResponse('/login', status_code=303)
        return FileResponse(BASE / 'templates' / 'index.html')

    @app.get('/login')
    def login_page(request: Request):
        if authorized(request):
            return RedirectResponse('/', status_code=303)
        return FileResponse(BASE / 'templates' / 'login.html')

    @app.post('/api/login')
    async def login(request: Request):
        data = await request.json()
        if not app.state.admin_password:
            return {'ok': True}
        password = data.get('password', '') if isinstance(data, dict) else ''
        if not isinstance(password, str) or not secrets.compare_digest(password, app.state.admin_password):
            return error('Contraseña incorrecta.', 401)
        response = JSONResponse({'ok': True})
        response.set_cookie('orchid_session', app.state.signer.dumps('admin'),
                            max_age=7 * 24 * 3600, httponly=True,
                            samesite='lax', secure=app.state.secure_cookie)
        return response

    @app.post('/api/logout')
    def logout():
        response = JSONResponse({'ok': True})
        response.delete_cookie('orchid_session')
        return response

    @app.get('/simulador')
    @app.get('/simulador.html')
    def simulator():
        return FileResponse(BASE / 'simulador_publico' / 'index.html')

    @app.get('/static/{file_path:path}')
    def static(file_path: str):
        root = (BASE / 'static').resolve()
        path = (root / file_path).resolve()
        if root not in path.parents or not path.is_file():
            return error('Archivo no encontrado.', 404)
        return FileResponse(path)

    @app.get('/api/health')
    def health():
        return {'ok': True, 'service': 'Orquídea Smart API', 'simulation_supported': True}

    @app.get('/api/overview')
    def overview():
        with engine.connect() as conn:
            data = conn.execute(select(latest).where(latest.c.id == 1)).first()
            total = conn.execute(select(func.count()).select_from(readings)).scalar_one()
            interval = int(get_setting(conn, 'history_interval', '1'))
            return {
                'latest': row_reading(data), 'history_count': total,
                'history_interval': interval, 'watering': list_watering(conn),
                'humidity_target': 80, 'humidity_ok_min': 75, 'humidity_ok_max': 85,
                'temperature_min': 18, 'temperature_max': 24,
                'auth_enabled': bool(app.state.admin_password),
            }

    @app.post('/api/measurements')
    async def measurement(request: Request):
        supplied = request.headers.get('x-api-key', '')
        if not secrets.compare_digest(supplied, app.state.sensor_key):
            return error('Clave de sensor incorrecta o ausente.', 401)
        try:
            data = await request.json()
        except ValueError:
            return error('El cuerpo debe ser JSON válido.')
        if not isinstance(data, dict):
            return error('Se esperaba un objeto JSON.')
        try:
            humidity, temperature = data['humidity'], data['temperature']
            if type(humidity) not in (int, float) or type(temperature) not in (int, float):
                raise ValueError()
            humidity, temperature = float(humidity), float(temperature)
            if not (math.isfinite(humidity) and math.isfinite(temperature)):
                raise ValueError()
        except (KeyError, ValueError, TypeError):
            return error('humidity y temperature deben ser números válidos.')
        if not (0 <= humidity <= 100 and -20 <= temperature <= 60):
            return error('Humedad debe estar entre 0–100 % y temperatura entre -20–60 °C.')
        source = data.get('source', 'simulador')
        if not isinstance(source, str) or len(source) > 50 or not source.strip():
            return error('El campo source debe ser un texto de 1 a 50 caracteres.')
        now = datetime.now(timezone.utc)
        with engine.begin() as conn:
            record = {'humidity': humidity, 'temperature': temperature,
                      'source': source.strip(), 'created_at': now}
            if conn.execute(select(latest.c.id).where(latest.c.id == 1)).first():
                conn.execute(update(latest).where(latest.c.id == 1).values(**record))
            else:
                conn.execute(insert(latest).values(id=1, **record))
            minutes = int(get_setting(conn, 'history_interval', '1'))
            last = conn.execute(select(readings.c.created_at).order_by(readings.c.id.desc()).limit(1)).scalar_one_or_none()
            if last is not None and last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            stored = (minutes == 0 or last is None or (now - last).total_seconds() >= minutes * 60)
            if stored:
                conn.execute(insert(readings).values(**record))
        return {'ok': True, 'stored_in_history': stored, 'reading': row_reading(type('Row', (), record)())}

    @app.get('/api/history')
    def history(limit: int = 100):
        if not (1 <= limit <= 500):
            return error('El límite debe estar entre 1 y 500.')
        with engine.connect() as conn:
            results = conn.execute(select(readings).order_by(readings.c.id.desc()).limit(limit)).all()
            return {'items': [row_reading(row) for row in reversed(results)], 'count': len(results)}

    @app.put('/api/settings')
    async def update_settings(request: Request):
        if rejected := require_admin(request):
            return rejected
        try:
            data = await request.json()
        except ValueError:
            return error('JSON inválido.')
        value = data.get('history_interval') if isinstance(data, dict) else None
        if type(value) is not int or value not in VALID_INTERVALS:
            return error('Intervalo permitido: 0, 1, 5, 15, 30 o 60 minutos.')
        with engine.begin() as conn:
            conn.execute(update(settings_table).where(settings_table.c.key == 'history_interval').values(value=str(value)))
        return {'ok': True, 'history_interval': value}

    @app.post('/api/watering')
    async def add_watering(request: Request):
        if rejected := require_admin(request):
            return rejected
        try:
            data = await request.json()
        except ValueError:
            return error('JSON inválido.')
        if not isinstance(data, dict):
            return error('JSON inválido.')
        name, days, hour = data.get('name'), data.get('weekdays'), data.get('time')
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 90:
            return error('Escribe un nombre de 1 a 90 caracteres.')
        if (not isinstance(days, list) or not days or len(days) > 7 or
                any(type(d) is not int or d not in range(7) for d in days) or len(set(days)) != len(days)):
            return error('Selecciona uno o más días válidos (lunes=0 a domingo=6).')
        if not isinstance(hour, str) or not TIME_RE.fullmatch(hour):
            return error('La hora debe tener formato HH:MM.')
        with engine.begin() as conn:
            item_id = conn.execute(insert(watering).values(
                name=name.strip(), weekdays=json.dumps(sorted(days)), time=hour, enabled=True,
            )).inserted_primary_key[0]
        return {'ok': True, 'id': item_id}

    @app.delete('/api/watering/{item_id}')
    def delete_watering(item_id: int, request: Request):
        if rejected := require_admin(request):
            return rejected
        with engine.begin() as conn:
            result = conn.execute(delete(watering).where(watering.c.id == item_id))
            if not result.rowcount:
                return error('Riego no encontrado.', 404)
        return {'ok': True}

    @app.post('/api/watering/{item_id}/complete')
    def complete_watering(item_id: int, request: Request):
        if rejected := require_admin(request):
            return rejected
        with engine.begin() as conn:
            if conn.execute(select(watering.c.id).where(watering.c.id == item_id)).first() is None:
                return error('Riego no encontrado.', 404)
            conn.execute(insert(water_logs).values(watering_id=item_id, created_at=datetime.now(timezone.utc)))
        return {'ok': True}

    return app


app = make_app()

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=int(os.getenv('PORT', 8000)))
