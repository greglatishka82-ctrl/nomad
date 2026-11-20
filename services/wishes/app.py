import hashlib, hmac, os, re, sqlite3, time
from contextlib import contextmanager
import httpx
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

KEY = os.environ['WISHES_HASH_KEY'].encode()
DB = '/data/wishes.sqlite3'
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

@contextmanager
def db():
    with sqlite3.connect(DB, timeout=10) as con:
        yield con

with db() as con:
    con.execute('CREATE TABLE IF NOT EXISTS wishes (id INTEGER PRIMARY KEY, text TEXT NOT NULL, created_at INTEGER NOT NULL, seen INTEGER NOT NULL DEFAULT 0)')
    con.execute('CREATE TABLE IF NOT EXISTS limits (ip_hash TEXT NOT NULL, text_hash TEXT NOT NULL, created_at INTEGER NOT NULL)')

class Wish(BaseModel):
    text: str = Field(min_length=10, max_length=3000)
    website: str = Field(default='', max_length=200)
    elapsed: int = 0

def origin(request, allowed):
    if request.headers.get('origin') not in allowed: raise HTTPException(403, 'Недопустимый источник запроса')

async def admin(request):
    cookie=request.headers.get('cookie')
    if not cookie: raise HTTPException(401, 'Требуется вход')
    try:
        async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
            res=await client.get('http://admin-api:8001/api/admin/check-session',headers={'cookie':cookie})
        if res.status_code!=200 or res.json().get('ok') is not True: raise HTTPException(401, 'Требуется вход')
    except (httpx.HTTPError, ValueError):
        raise HTTPException(503, 'Не удалось проверить сессию')

@app.post('/api/wishes')
def submit(body: Wish, request: Request):
    origin(request, {'https://greentime.luxe','https://www.greentime.luxe'})
    value=body.text.strip()
    if len(value)<10 or body.website or body.elapsed<2500: raise HTTPException(400,'Расскажите немного подробнее и попробуйте ещё раз.')
    now=int(time.time())
    ip=request.headers.get('x-greentime-client-ip', request.client.host)
    fingerprint=hmac.new(KEY,ip.encode(),hashlib.sha256).hexdigest()
    digest=hashlib.sha256(re.sub(r'\s+',' ',value.casefold()).encode()).hexdigest()
    with db() as con:
        con.execute('BEGIN IMMEDIATE')
        con.execute('DELETE FROM limits WHERE created_at<?',(now-86400,))
        if con.execute('SELECT COUNT(*) FROM limits WHERE ip_hash=? AND created_at>?',(fingerprint,now-600)).fetchone()[0]>=3: raise HTTPException(429,'Слишком много отправок. Попробуйте позже.')
        if con.execute('SELECT 1 FROM limits WHERE ip_hash=? AND text_hash=?',(fingerprint,digest)).fetchone(): raise HTTPException(429,'Такой ответ уже отправлен. Спасибо!')
        con.execute('INSERT INTO wishes(text,created_at) VALUES(?,?)',(value,now))
        con.execute('INSERT INTO limits VALUES(?,?,?)',(fingerprint,digest,now))
    return {'ok':True}

@app.get('/api/admin/wishes')
async def listing(request: Request):
    await admin(request)
    with db() as con: rows=con.execute('SELECT id,text,created_at,seen FROM wishes ORDER BY id DESC').fetchall()
    return [{'id':r[0],'text':r[1],'created_at':r[2],'seen':bool(r[3])} for r in rows]

@app.post('/api/admin/wishes/{item_id}/seen')
async def seen(item_id:int, request:Request):
    origin(request, {'https://admin.greentime.luxe'}); await admin(request)
    with db() as con: con.execute('UPDATE wishes SET seen=1 WHERE id=?',(item_id,))
    return {'ok':True}
