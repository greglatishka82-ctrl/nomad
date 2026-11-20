import math
from datetime import datetime, timedelta, timezone
from sqlalchemy import text
from fastapi import HTTPException

PROFILE_MESSAGE = 'Ваш доступ ограничен, напишите администратору GREENTIME.'

def client_ip(request):
    # Заголовок перезаписывается внешним Caddy; API не публикуются наружу.
    return request.headers.get('x-greentime-client-ip') or (request.client.host if request.client else 'unknown')

def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)

async def ensure_schema(db):
    await db.execute(text("""CREATE TABLE IF NOT EXISTS security_auth_limits (
        scope VARCHAR(24) NOT NULL, ip VARCHAR(64) NOT NULL, failures INTEGER NOT NULL DEFAULT 0,
        blocked BOOLEAN NOT NULL DEFAULT FALSE, blocked_until TIMESTAMP NULL, updated_at TIMESTAMP NOT NULL,
        PRIMARY KEY(scope, ip))"""))
    await db.execute(text("""CREATE TABLE IF NOT EXISTS security_alert_owner (
        id INTEGER PRIMARY KEY CHECK(id=1), telegram_id BIGINT NOT NULL, phone VARCHAR(24) NOT NULL)"""))
    await db.execute(text("""CREATE TABLE IF NOT EXISTS security_alert_outbox (
        id BIGSERIAL PRIMARY KEY, message TEXT NOT NULL, created_at TIMESTAMP NOT NULL,
        delivered_at TIMESTAMP NULL, last_error VARCHAR(80) NULL)"""))
    await db.commit()

async def lock_attempt(db, scope, ip):
    await db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'), {'key': 'auth:'+scope+':'+ip})
    row=(await db.execute(text('SELECT * FROM security_auth_limits WHERE scope=:scope AND ip=:ip FOR UPDATE'),{'scope':scope,'ip':ip})).mappings().first()
    now=utcnow()
    if row and row['blocked']:
        until=row['blocked_until']
        if until is None or until>now:
            if scope=='profile': raise HTTPException(403,PROFILE_MESSAGE)
            seconds=max(1,math.ceil((until-now).total_seconds()))
            raise HTTPException(429,'Вход заблокирован на 1 час после трёх неверных паролей. Повторите позже.',headers={'Retry-After':str(seconds)})
        await db.execute(text('DELETE FROM security_auth_limits WHERE scope=:scope AND ip=:ip'),{'scope':scope,'ip':ip})
        row=None
    return row

async def failed(db,scope,ip,row):
    now=utcnow();count=(row['failures'] if row else 0)+1;limit=3 if scope=='admin' else 5;blocked=count>=limit
    until=now+timedelta(hours=1) if blocked and scope=='admin' else None
    await db.execute(text("""INSERT INTO security_auth_limits(scope,ip,failures,blocked,blocked_until,updated_at)
        VALUES(:scope,:ip,:count,:blocked,:until,:now) ON CONFLICT(scope,ip) DO UPDATE SET
        failures=EXCLUDED.failures,blocked=EXCLUDED.blocked,blocked_until=EXCLUDED.blocked_until,updated_at=EXCLUDED.updated_at"""),
        dict(scope=scope,ip=ip,count=count,blocked=blocked,until=until,now=now))
    if blocked:
        from app.models.models import Event,now_kz
        message=(f'Вход в админку заблокирован на 1 час: 3 неверных пароля. IP: {ip}' if scope=='admin' else f'Доступ в личный кабинет ограничен: 5 неверных кодов. IP: {ip}')
        db.add(Event(event_type='auth_access_blocked',source='security',message=message,created_at=now_kz()))
        await db.execute(text('INSERT INTO security_alert_outbox(message,created_at) VALUES(:message,:now)'),{'message':message,'now':now})
    await db.commit()
    if blocked:
        if scope=='profile':raise HTTPException(403,PROFILE_MESSAGE)
        raise HTTPException(429,'Вход заблокирован на 1 час после трёх неверных паролей.',headers={'Retry-After':'3600'})
    return count

async def succeeded(db,scope,ip):
    await db.execute(text('DELETE FROM security_auth_limits WHERE scope=:scope AND ip=:ip'),{'scope':scope,'ip':ip})

async def check_profile_ip(db,request):
    row=(await db.execute(text("SELECT blocked FROM security_auth_limits WHERE scope='profile' AND ip=:ip"),{'ip':client_ip(request)})).scalar()
    if row:raise HTTPException(403,PROFILE_MESSAGE)
