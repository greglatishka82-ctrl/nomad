import secrets, hashlib, hmac, json, os
from pathlib import Path
from sqlalchemy import Column, Integer, String, Text, JSON, ForeignKey, DateTime, Boolean, select
from app.database import Base
from app.models.models import Client, Booking, Instructor, now_kz
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

SECRET=os.environ['SECRET_KEY']
SITE='https://greentime.luxe'
CATEGORIES=json.loads(Path(__file__).with_name('profile_catalog.json').read_text(encoding='utf8'))
CATALOG={c['id']:c for c in CATEGORIES}
serializer=URLSafeTimedSerializer(SECRET)
class ProfileAccess(Base):
 __tablename__='lab_profile_access'
 client_id=Column(Integer,ForeignKey('clients.id',ondelete='CASCADE'),primary_key=True)
 code=Column(String(32),unique=True,nullable=False,index=True)
 delivery_pending=Column(Boolean,nullable=False,server_default='false')
 created_at=Column(DateTime,default=now_kz,nullable=False)
class LessonReview(Base):
 __tablename__='lab_lesson_review'
 booking_id=Column(Integer,ForeignKey('bookings.id',ondelete='CASCADE'),primary_key=True)
 client_id=Column(Integer,ForeignKey('clients.id',ondelete='CASCADE'),nullable=False,index=True)
 instructor_id=Column(Integer,ForeignKey('instructors.id',ondelete='SET NULL'),nullable=True)
 ratings=Column(JSON,nullable=False,default=dict)
 comment=Column(Text,nullable=False,default='')
 updated_at=Column(DateTime,default=now_kz,nullable=False)
async def ensure_code(db,client_id):
 access=await db.get(ProfileAccess,client_id)
 if not access:
  access=ProfileAccess(client_id=client_id,code='GT-'+secrets.token_hex(8).upper());db.add(access);await db.flush()
 return access

def lesson_token(bid): return serializer.dumps({'b':bid},salt='lab-lesson')
def parse_lesson(token): return int(serializer.loads(token,salt='lab-lesson',max_age=60*60*24*30)['b'])
def make_session(access):
 digest=hmac.new(SECRET.encode(),access.code.encode(),hashlib.sha256).hexdigest()
 return serializer.dumps({'c':access.client_id,'v':digest},salt='lab-client')
async def session_client(db,token):
 try:
  data=serializer.loads(token,salt='lab-client',max_age=60*60*24*30); access=await db.get(ProfileAccess,int(data['c']))
  if not access:return None
  digest=hmac.new(SECRET.encode(),access.code.encode(),hashlib.sha256).hexdigest()
  if not hmac.compare_digest(digest,data['v']):return None
  return await db.get(Client,access.client_id)
 except (BadSignature,SignatureExpired,KeyError,ValueError,TypeError):return None

def normalize_ratings(input_value):
 if not isinstance(input_value,dict):raise ValueError('Некорректные оценки')
 cleaned={}
 for cat,values in input_value.items():
  if cat not in CATALOG or not isinstance(values,dict):raise ValueError('Неизвестное направление')
  clean={}
  for index,value in values.items():
   if not str(index).isdigit() or int(index)>=len(CATALOG[cat]['skills']) or type(value) is not int or not 1<=value<=5:raise ValueError('Некорректная оценка')
   clean[str(int(index))]=value
  if clean:cleaned[cat]=clean
 if not cleaned:raise ValueError('Выберите хотя бы один навык')
 return cleaned

async def profile_data(db,client):
 bookings=(await db.execute(select(Booking).where(Booking.client_id==client.id,Booking.service_type=='training',Booking.status=='completed').order_by(Booking.booking_date,Booking.start_time,Booking.id))).scalars().all()
 reviews=(await db.execute(select(LessonReview).where(LessonReview.client_id==client.id))).scalars().all()
 reviewmap={r.booking_id:r for r in reviews}
 instructor_ids={b.instructor_id for b in bookings if b.instructor_id}
 instructors=(await db.execute(select(Instructor).where(Instructor.id.in_(instructor_ids)))).scalars().all() if instructor_ids else []
 instructor_names={i.id:i.name for i in instructors}
 latest={};history=[];series=[]
 def calculate():
  values={}
  for cat in CATEGORIES:
   points=[latest.get((cat['id'],str(i))) for i in range(len(cat['skills']))]
   assessed=[x for x in points if x is not None]
   values[cat['id']]=round(sum(assessed)/(len(assessed)*5)*100) if assessed else None
  measured=[c for c in CATEGORIES if values[c['id']] is not None]
  score=round(sum(values[c['id']]*c['weight'] for c in measured)/sum(c['weight'] for c in measured)) if measured else None
  return values,score
 for b in bookings:
  rev=reviewmap.get(b.id)
  if rev:
   for cat,ratings in (rev.ratings or {}).items():
    if cat in CATALOG:
     for idx,val in ratings.items():latest[(cat,str(idx))]=val
   values,score=calculate();series.append({'date':b.booking_date.isoformat(),'score':score,'categories':values})
  history.append({'booking_id':b.id,'date':b.booking_date.isoformat(),'time':str(b.start_time)[:5],'instructor':instructor_names.get(b.instructor_id,'Инструктор'),'comment':rev.comment if rev else '', 'ratings':rev.ratings if rev else {}})
 values,score=calculate()
 categories=[]
 for cat in CATEGORIES:
  categories.append({'id':cat['id'],'title':cat['title'],'shortTitle':cat['shortTitle'],'score':values[cat['id']],'skills':[{'name':skill[0],'kind':skill[2] if len(skill)>2 else 'additional','rating':latest.get((cat['id'],str(i)))} for i,skill in enumerate(cat['skills'])]})
 return {'name':client.name,'completed_visits':len(bookings),'score':score,'assessed_skills':len(latest),'total_skills':sum(len(c['skills']) for c in CATEGORIES),'categories':categories,'series':series,'history':list(reversed(history))}