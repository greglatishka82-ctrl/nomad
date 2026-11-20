from fastapi import APIRouter,Request,HTTPException,Query,Depends,Response
from sqlalchemy import text
from app.database import async_session, get_db
from app.routers.admin import check_session
router=APIRouter(prefix='/api/admin/site-traffic',tags=['site-traffic'])
@router.get('/overview')
async def overview(request:Request,days:int=Query(30,ge=1,le=365),db=Depends(get_db)):
 await check_session(request,Response(),db)
 async with async_session() as db:
  where="created_at >= now() - (:days * interval '1 day')"
  async def rows(sql): return [dict(r._mapping) for r in (await db.execute(text(sql),{'days':days})).all()]
  totals=(await rows(f"""SELECT count(*) FILTER (WHERE kind='pageview') AS views,count(DISTINCT visitor) FILTER (WHERE kind='pageview') AS visitors,count(DISTINCT session_id) FILTER (WHERE kind='pageview') AS sessions,count(*) FILTER (WHERE kind='click' AND target='whatsapp') AS whatsapp,count(*) FILTER (WHERE kind='click' AND target='telegram') AS telegram,coalesce(sum(value) FILTER (WHERE kind='heartbeat'),0) AS active_seconds FROM site_traffic_events WHERE {where} AND path != '/direct' AND path != '/profile.html'"""))[0]
  async def groups(column,cond="kind='pageview'"):
   return await rows(f"SELECT coalesce(nullif({column},''),'direct') AS name,count(*) AS count,count(DISTINCT session_id) AS sessions FROM site_traffic_events WHERE {where} AND {cond} GROUP BY 1 ORDER BY count DESC LIMIT 20")
  sources=await rows(f"""WITH events AS (SELECT *,lower(coalesce(nullif(source,''),nullif(referrer,''),'direct')) AS raw_source FROM site_traffic_events WHERE {where} AND path != '/direct'), normalized AS (SELECT *, CASE WHEN raw_source LIKE '%2gis%' THEN '2gis' WHEN raw_source LIKE '%instagram%' OR raw_source='ig' THEN 'instagram' WHEN raw_source LIKE '%yandex%' OR raw_source LIKE '%ya.ru%' THEN 'yandex' WHEN raw_source LIKE '%tiktok%' THEN 'tiktok' WHEN raw_source LIKE '%google%' THEN 'google' WHEN raw_source LIKE '%telegram%' OR raw_source='t.me' THEN 'telegram' WHEN raw_source LIKE '%whatsapp%' OR raw_source='wa.me' THEN 'whatsapp' ELSE raw_source END AS normalized_source FROM events) SELECT normalized_source AS name,count(DISTINCT session_id) FILTER (WHERE kind='pageview') AS sessions,count(*) FILTER (WHERE kind='click' AND target='whatsapp') AS whatsapp,count(*) FILTER (WHERE kind='click' AND target='telegram') AS telegram FROM normalized GROUP BY 1 ORDER BY sessions DESC LIMIT 30""")
  daily=await rows(f"""SELECT to_char(created_at AT TIME ZONE 'Asia/Almaty','YYYY-MM-DD') AS day,count(DISTINCT visitor) AS visitors,count(DISTINCT session_id) AS sessions,count(*) AS views FROM site_traffic_events WHERE {where} AND kind='pageview' GROUP BY 1 ORDER BY 1""")
  clicks=await rows(f"""SELECT label,target,count(*) AS count FROM site_traffic_events WHERE {where} AND kind='click' AND path NOT IN ('/direct','/profile.html') GROUP BY 1,2 ORDER BY count DESC LIMIT 30""")
  cabinet=(await rows(f"""SELECT count(*) FILTER (WHERE kind='click') AS clicks,count(DISTINCT session_id) FILTER (WHERE kind='click') AS sessions,count(*) FILTER (WHERE kind='click' AND target='whatsapp') AS whatsapp,count(*) FILTER (WHERE kind='click' AND target='telegram') AS telegram,count(*) FILTER (WHERE kind='click' AND (label='Карточка: Telegram' OR label='Карточка: WhatsApp' OR label='Карточка: Instagram' OR label LIKE 'Карточка: Поделиться%')) AS share FROM site_traffic_events WHERE {where} AND path='/profile.html'"""))[0]
  clicks_cabinet=await rows(f"""SELECT label,target,count(*) AS count FROM site_traffic_events WHERE {where} AND kind='click' AND path='/profile.html' GROUP BY 1,2 ORDER BY count DESC LIMIT 50""")
  scroll=await rows(f"""SELECT value,count(DISTINCT session_id) AS sessions FROM site_traffic_events WHERE {where} AND kind='scroll' GROUP BY 1 ORDER BY 1""")
  return {'totals':totals,'daily':daily,'sources':sources,'device':await groups('device'),'browser':await groups('browser'),'os':await groups('os'),'screen':await groups('screen'),'language':await groups('language'),'pages':await groups('path'),'clicks':clicks,'clicks_cabinet':clicks_cabinet,'cabinet':cabinet,'scroll':scroll}
