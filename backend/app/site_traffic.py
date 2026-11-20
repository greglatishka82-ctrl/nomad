from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from app.database import async_session
from typing import Literal
router=APIRouter(prefix="/api/site-traffic",tags=["site-traffic"])
class Event(BaseModel):
 visitor: str = Field(min_length=16,max_length=64,pattern=r"^[a-zA-Z0-9-]+$")
 session: str = Field(min_length=16,max_length=64,pattern=r"^[a-zA-Z0-9-]+$")
 kind: Literal["pageview","click","scroll","heartbeat"]
 path: str = Field(max_length=180)
 source: str = Field(default="",max_length=100)
 medium: str = Field(default="",max_length=80)
 campaign: str = Field(default="",max_length=100)
 referrer: str = Field(default="",max_length=150)
 device: str = Field(default="",max_length=20)
 browser: str = Field(default="",max_length=30)
 os: str = Field(default="",max_length=30)
 screen: str = Field(default="",max_length=24)
 language: str = Field(default="",max_length=24)
 label: str = Field(default="",max_length=100)
 target: str = Field(default="",max_length=100)
 value: int = Field(default=0,ge=0,le=100)
@router.post("/event",status_code=204)
async def event(e:Event,request:Request):
 if e.path not in ("/","/index.html","/lending2.0.html"): raise HTTPException(400,"Invalid page")
 async with async_session() as db:
  await db.execute(text("""INSERT INTO site_traffic_events(visitor,session_id,kind,path,source,medium,campaign,referrer,device,browser,os,screen,language,label,target,value) VALUES (:visitor,:session,:kind,:path,:source,:medium,:campaign,:referrer,:device,:browser,:os,:screen,:language,:label,:target,:value)"""),e.model_dump())
  await db.commit()

@router.get("/go/{source}/{target}")
async def outbound(source:str,target:str,request:Request):
 from fastapi.responses import RedirectResponse
 import secrets,logging
 destinations={"whatsapp":"https://wa.me/77078810848","telegram":"https://t.me/green_time_drive_bot","site":"https://greentime.luxe/"}
 allowed={"instagram","tiktok","yandex","2gis"}
 if source not in allowed or target not in destinations: raise HTTPException(404,"Not found")
 dest=destinations[target]
 if target=="site":
  return RedirectResponse(dest+"?utm_source="+source+"&utm_medium=referral",status_code=302,headers={"Cache-Control":"no-store","Referrer-Policy":"no-referrer"})
 try:
  session=secrets.token_hex(16)
  ua=request.headers.get("user-agent","").lower()
  device="tablet" if "ipad" in ua or "tablet" in ua else "mobile" if "mobile" in ua or "android" in ua else "desktop"
  async with async_session() as db:
   await db.execute(text("INSERT INTO site_traffic_events(visitor,session_id,kind,path,source,medium,device,label,target) VALUES (:v,:s,'click','/direct',:source,'direct_link',:device,'Direct link',:target)"),{"v":session,"s":session,"source":source,"device":device,"target":target})
   await db.commit()
 except Exception:
  logging.getLogger(__name__).exception("traffic redirect logging failed")
 return RedirectResponse(dest,status_code=302,headers={"Cache-Control":"no-store","Referrer-Policy":"no-referrer"})
