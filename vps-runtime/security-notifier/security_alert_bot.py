import asyncio,logging,os,re
from datetime import datetime,timezone
import httpx
from sqlalchemy import text
from app.database import async_session
from app.security_guard import ensure_schema
logging.getLogger('httpx').setLevel(logging.WARNING)
TOKEN=os.environ['SECURITY_BOT_TOKEN'];PHONE=os.environ['SECURITY_OWNER_PHONE']
async def api(c,method,body):
    try:
        response=await c.post('https://api.telegram.org/bot'+TOKEN+'/'+method,json=body)
        data=response.json()
        return data
    except Exception:return {'ok':False,'error_code':0}
async def tell(c,chat,message,keyboard=None):
    body={'chat_id':chat,'text':message}
    if keyboard:body['reply_markup']=keyboard
    return await api(c,'sendMessage',body)
async def handle(c,m):
    user=m.get('from',{}).get('id');chat=m.get('chat',{}).get('id')
    if not user or m.get('chat',{}).get('type')!='private':return
    async with async_session() as db:
        owner=(await db.execute(text('SELECT telegram_id FROM security_alert_owner WHERE id=1'))).scalar()
    contact=m.get('contact')
    if contact:
        number=re.sub(r'\D','',contact.get('phone_number',''))
        if contact.get('user_id')!=user or number!=PHONE:
            await tell(c,chat,'Доступ запрещён.');return
        async with async_session() as db:
            await db.execute(text('INSERT INTO security_alert_owner(id,telegram_id,phone) VALUES(1,:uid,:phone) ON CONFLICT(id) DO UPDATE SET telegram_id=EXCLUDED.telegram_id,phone=EXCLUDED.phone'),{'uid':user,'phone':PHONE})
            await db.commit()
        await tell(c,chat,'Доступ подтверждён. Здесь будут уведомления о блокировках входа в админку и личные кабинеты клиентов.\nСнять блокировку личного кабинета: /unblock IP',{'remove_keyboard':True});return
    if owner!=user:
        await tell(c,chat,'Подтвердите свой номер телефона кнопкой ниже.',{'keyboard':[[{'text':'Поделиться номером','request_contact':True}]],'resize_keyboard':True,'one_time_keyboard':True});return
    command=m.get('text','')
    if command.startswith('/unblock '):
        import ipaddress
        try:ip=str(ipaddress.ip_address(command.split(maxsplit=1)[1].strip()))
        except ValueError:await tell(c,chat,'Укажите IP: /unblock 1.2.3.4');return
        async with async_session() as db:
            result=await db.execute(text("DELETE FROM security_auth_limits WHERE scope='profile' AND ip=:ip RETURNING ip"),{'ip':ip})
            found=result.scalar()
            if found:
                from app.models.models import Event,now_kz
                db.add(Event(event_type='profile_ip_unblocked',source='security',message=f'Владелец снял блокировку личного кабинета для IP {ip}',created_at=now_kz()))
            await db.commit()
        await tell(c,chat,'Блокировка снята.' if found else 'Этот IP не заблокирован.');return
    await tell(c,chat,'Бот безопасности GREENTIME активен. Уведомления о блокировках приходят автоматически. Снять ограничение личного кабинета: /unblock IP')
async def deliver(c):
    async with async_session() as db:
        owner=(await db.execute(text('SELECT telegram_id FROM security_alert_owner WHERE id=1'))).scalar()
        if not owner:return
        rows=(await db.execute(text('SELECT id,message FROM security_alert_outbox WHERE delivered_at IS NULL ORDER BY id LIMIT 10'))).mappings().all()
    for row in rows:
        response=await tell(c,owner,row['message'])
        async with async_session() as db:
            if response.get('ok'):
                await db.execute(text('UPDATE security_alert_outbox SET delivered_at=:now,last_error=NULL WHERE id=:id'),{'now':datetime.now(timezone.utc).replace(tzinfo=None),'id':row['id']})
            else:
                await db.execute(text('UPDATE security_alert_outbox SET last_error=:err WHERE id=:id'),{'err':str(response.get('error_code',0)),'id':row['id']})
            await db.commit()
async def main():
    async with async_session() as db:await ensure_schema(db)
    offset=0
    async with httpx.AsyncClient(timeout=20) as c:
        while True:
            try:
                data=await api(c,'getUpdates',{'offset':offset,'timeout':10,'allowed_updates':['message']})
                for update in data.get('result',[]):
                    await handle(c,update.get('message',{}));offset=update['update_id']+1
                await deliver(c)
            except Exception:
                print('Security bot operation failed; retrying',flush=True)
            await asyncio.sleep(2)
asyncio.run(main())
