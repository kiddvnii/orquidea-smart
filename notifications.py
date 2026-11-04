"""Procesamiento persistente de recordatorios de riego por correo."""
from __future__ import annotations

import json
import os
import re
import smtplib
import ssl
from datetime import date, datetime, time, timedelta, timezone
from email.message import EmailMessage
import httpx
from zoneinfo import ZoneInfo

from sqlalchemy import (
    Column, DateTime, Integer, MetaData, String, Table, Text,
    UniqueConstraint, and_, delete, or_, select, update,
)
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

notification_metadata = MetaData()
notification_events = Table(
    'email_notification_events', notification_metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('watering_id', Integer, nullable=False),
    Column('scheduled_for', DateTime(timezone=True), nullable=False),
    Column('status', String(15), nullable=False, default='pending'),
    Column('attempts', Integer, nullable=False, default=0),
    Column('lease_until', DateTime(timezone=True), nullable=True),
    Column('sent_at', DateTime(timezone=True), nullable=True),
    Column('last_error', Text, nullable=True),
    UniqueConstraint('watering_id', 'scheduled_for', name='uq_email_notification_occurrence'),
)

DAY_NAMES = ('lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo')

def recipient_email() -> str:
    return os.getenv('NOTIFICATION_EMAIL', 'orquidea.smart@gmail.com').strip()

def email_provider() -> str:
    setting = os.getenv('EMAIL_PROVIDER', '').lower().strip()
    if setting:
        return setting
    return 'resend' if os.getenv('RESEND_API_KEY') else 'smtp'

def email_is_configured() -> bool:
    if not recipient_email():
        return False
    provider = email_provider()
    if provider == 'resend':
        return bool(os.getenv('RESEND_API_KEY') and os.getenv('EMAIL_FROM', 'Orquídea Smart <onboarding@resend.dev>'))
    if provider == 'smtp':
        return bool(os.getenv('SMTP_USER') and os.getenv('SMTP_APP_PASSWORD'))
    return False

def send_email(subject: str, body: str, recipient: str) -> None:
    """Envía mediante HTTPS/Resend o SMTP; las claves nunca se exponen al cliente."""
    if not email_is_configured():
        raise RuntimeError('No está configurado el proveedor de correo.')
    if email_provider() == 'resend':
        # Se usa un cliente HTTP estándar con User-Agent explícito.
        # El identificador Python-urllib puede ser bloqueado por filtros externos.
        payload = {
            'from': os.getenv('EMAIL_FROM', 'Orquídea Smart <onboarding@resend.dev>'),
            'to': [recipient], 'subject': subject, 'text': body,
        }
        headers = {
            'Authorization': 'Bearer ' + os.environ['RESEND_API_KEY'],
            'User-Agent': 'OrquideaSmart/1.0 (+https://orquidea-smart.onrender.com)',
            'Accept': 'application/json',
        }
        try:
            response = httpx.post(
                'https://api.resend.com/emails', json=payload,
                headers=headers, timeout=15.0,
            )
        except httpx.RequestError as exc:
            raise RuntimeError('No fue posible conectar con Resend.') from exc

        if response.status_code not in (200, 201, 202):
            detail = ''
            try:
                data = response.json()
                if isinstance(data, dict):
                    detail = str(data.get('message') or data.get('error') or '')
            except ValueError:
                pass
            if detail:
                detail = re.sub(r're_[A-Za-z0-9_-]{8,}', '[clave oculta]', detail)
                detail = re.sub(r'(?i)bearer\\s+\\S+', 'Bearer [oculto]', detail)
                detail = detail.replace('\\n', ' ').replace('\\r', ' ')[:200]
            elif 'html' in response.headers.get('content-type', '').lower():
                detail = 'respuesta HTML (posible bloqueo de seguridad)'
            suffix = ': ' + detail if detail else ''
            raise RuntimeError(f'Resend rechazó el correo (HTTP {response.status_code}{suffix}).')
        return

    host = os.getenv('SMTP_HOST', 'smtp.gmail.com')
    port = int(os.getenv('SMTP_PORT', '465'))
    user = os.environ['SMTP_USER']
    password = os.environ['SMTP_APP_PASSWORD'].replace(' ', '')
    message = EmailMessage()
    message['From'] = os.getenv('EMAIL_FROM', user)
    message['To'] = recipient
    message['Subject'] = subject
    message.set_content(body)
    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=15) as server:
            server.login(user, password)
            server.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=15) as server:
            server.starttls(context=context)
            server.login(user, password)
            server.send_message(message)

def ensure_utc(dt: datetime) -> datetime:
    return (dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc))

def make_occurrences(watering_rows, now: datetime, grace_minutes: int, tz: ZoneInfo):
    """Lunes=0, domingo=6; días y horas se interpretan en Santiago."""
    local_now = now.astimezone(tz)
    local_start = (now - timedelta(minutes=grace_minutes)).astimezone(tz)
    dates: list[date] = [local_start.date()]
    if local_now.date() != local_start.date():
        dates.append(local_now.date())
    for item in watering_rows:
        if not item['enabled']:
            continue
        days = json.loads(item['weekdays'])
        hour, minute = (int(value) for value in item['time'].split(':'))
        for on_date in dates:
            if on_date.weekday() not in days:
                continue
            scheduled_local = datetime.combine(on_date, time(hour, minute), tzinfo=tz)
            scheduled = scheduled_local.astimezone(timezone.utc)
            if scheduled.astimezone(tz).replace(tzinfo=None) != scheduled_local.replace(tzinfo=None):
                continue
            if now - timedelta(minutes=grace_minutes) <= scheduled <= now:
                yield item, scheduled

def _insert_if_missing(conn, occurrence):
    values = dict(watering_id=occurrence[0], scheduled_for=occurrence[1],
                  status='pending', attempts=0)
    if conn.dialect.name == 'postgresql':
        stmt = postgres_insert(notification_events).values(**values).on_conflict_do_nothing(
            index_elements=['watering_id', 'scheduled_for'])
    elif conn.dialect.name == 'sqlite':
        stmt = sqlite_insert(notification_events).values(**values).on_conflict_do_nothing(
            index_elements=['watering_id', 'scheduled_for'])
    else:
        raise RuntimeError('Las notificaciones necesitan PostgreSQL o SQLite.')
    conn.execute(stmt)

def dispatch_due(engine, watering_table, sender=send_email, *, now=None,
                 grace_minutes=30, timezone_name='America/Santiago') -> dict:
    """Genera y reclama ocurrencias atómicamente, con reintentos y deduplicación."""
    if not 1 <= grace_minutes <= 1440:
        raise ValueError('grace_minutes fuera de rango')
    tz = ZoneInfo(timezone_name)
    current = ensure_utc(now or datetime.now(timezone.utc))
    if not email_is_configured() and sender is send_email:
        return {'ok': False, 'reason': 'Correo sin configurar', 'sent': 0, 'failed': 0, 'pending': 0}

    with engine.begin() as conn:
        items = conn.execute(select(watering_table)).mappings().all()
        for item, scheduled in make_occurrences(items, current, grace_minutes, tz):
            _insert_if_missing(conn, (item['id'], scheduled))
        conn.execute(delete(notification_events).where(and_(
            notification_events.c.status != 'sent',
            ~notification_events.c.watering_id.in_(select(watering_table.c.id)),
        )))

    due_before = current - timedelta(minutes=grace_minutes)
    available = or_(notification_events.c.lease_until.is_(None),
                    notification_events.c.lease_until <= current)
    eligible = and_(notification_events.c.status != 'sent', available,
                    notification_events.c.scheduled_for >= due_before,
                    notification_events.c.scheduled_for <= current)
    claimed = []
    with engine.begin() as conn:
        rows = conn.execute(
            select(notification_events.c.id, notification_events.c.watering_id,
                   notification_events.c.scheduled_for, watering_table.c.name, watering_table.c.time)
            .join(watering_table, notification_events.c.watering_id == watering_table.c.id)
            .where(eligible, watering_table.c.enabled.is_(True))
            .order_by(notification_events.c.scheduled_for, notification_events.c.id)
            .limit(100)
        ).mappings().all()
        for row in rows:
            result = conn.execute(
                update(notification_events).where(notification_events.c.id == row['id'], eligible)
                .values(status='processing', attempts=notification_events.c.attempts + 1,
                        lease_until=current + timedelta(minutes=2))
            )
            if result.rowcount:
                claimed.append(dict(row))

    sent, failed = 0, 0
    for record in claimed:
        local_slot = ensure_utc(record['scheduled_for']).astimezone(tz)
        subject = '[Orquídea Smart] Recordatorio de riego'
        body = (f'Hola,\n\nEs momento de regar tus orquídeas.\n\n'
                f'Tarea: {record["name"]}\n'
                f'Día: {DAY_NAMES[local_slot.weekday()].capitalize()} {local_slot:%d-%m-%Y}\n'
                f'Hora programada: {record["time"]} ({timezone_name})\n\n'
                f'Entra a Orquídea Smart para registrar el riego realizado.\n\n'
                f'Este correo es un recordatorio automático de tu calendario.\n')
        try:
            sender(subject, body, recipient_email())
        except Exception as exc:
            with engine.begin() as conn:
                conn.execute(update(notification_events).where(notification_events.c.id == record['id'])
                             .values(status='failed', last_error=type(exc).__name__,
                                     lease_until=current + timedelta(minutes=2)))
            failed += 1
        else:
            with engine.begin() as conn:
                conn.execute(update(notification_events).where(notification_events.c.id == record['id'])
                             .values(status='sent', sent_at=datetime.now(timezone.utc),
                                     lease_until=None, last_error=None))
            sent += 1
    return {'ok': failed == 0, 'sent': sent, 'failed': failed, 'pending': len(claimed) - sent - failed}

def recent_delivery_events(engine, limit=6):
    with engine.connect() as conn:
        rows = conn.execute(select(notification_events).order_by(
            notification_events.c.id.desc()).limit(limit)).mappings().all()
    return [{
        'watering_id': row['watering_id'],
        'scheduled_for': ensure_utc(row['scheduled_for']).isoformat(),
        'sent_at': ensure_utc(row['sent_at']).isoformat() if row['sent_at'] else None,
        'status': row['status'], 'attempts': row['attempts'],
    } for row in rows]
