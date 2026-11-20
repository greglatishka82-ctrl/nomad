from collections import defaultdict,deque
import time
from fastapi import APIRouter,Depends,HTTPException,Request,Response
from pydantic import BaseModel
from sqlalchemy import select, Column, Integer, String, Text, ForeignKey, DateTime
from app.database import Base
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models.models import Event,Client,Booking,Instructor,now_kz
from app.progress_common import ProfileAccess,LessonReview,CATEGORIES,parse_lesson,make_session,session_client,profile_data,normalize_ratings

router=APIRouter(prefix='/api/profile',tags=['test-profile'])
attempts=defaultdict(deque)
class LoginBody(BaseModel): code:str
class ReviewBody(BaseModel): ratings:dict; comment:str=''; goal:str=''
from app.security_guard import client_ip, lock_attempt, failed, succeeded, check_profile_ip
@router.post('/login')
async def login(body:LoginBody,request:Request,response:Response,db:AsyncSession=Depends(get_db)):
 ip=client_ip(request);attempt=await lock_attempt(db,'profile',ip)
 code=body.code.strip().upper()
 access=(await db.execute(select(ProfileAccess).where(ProfileAccess.code==code))).scalar_one_or_none()
 client=await db.get(Client,access.client_id) if access else None
 if not access or not client or client.is_deleted:
  count=await failed(db,'profile',ip,attempt)
  raise HTTPException(401,f'Код не найден. Осталось попыток: {5-count}.')
 await succeeded(db,'profile',ip);await db.commit()
 response.set_cookie('gt_profile',make_session(access),secure=True,httponly=True,samesite='lax',max_age=2592000,path='/');return {'ok':True}
@router.post('/logout')
async def logout(response:Response):response.delete_cookie('gt_profile',path='/');return {'ok':True}
@router.get('/me')
async def me(request:Request,db:AsyncSession=Depends(get_db)):
 await check_profile_ip(db,request)
 client=await session_client(db,request.cookies.get('gt_profile',''))
 if not client or client.is_deleted:raise HTTPException(401,'Требуется вход')
 return await profile_data(db,client)
async def lesson_from_token(db,token):
 try:bid=parse_lesson(token)
 except Exception:raise HTTPException(404,'Ссылка недействительна или устарела')
 booking=(await db.execute(select(Booking).where(Booking.id==bid).with_for_update())).scalar_one_or_none()
 if booking and booking.status=='completed':raise HTTPException(403,'Занятие завершено. Доступ к оценке закрыт.')
 if not booking or booking.service_type!='training' or booking.status not in ('planned','confirmed','in_progress'):raise HTTPException(403,'Карточка для этой записи недоступна')
 return booking
@router.get('/lesson/{token}/status')
async def lesson_status(token:str,db:AsyncSession=Depends(get_db)):
 b=await lesson_from_token(db,token)
 return {'status':b.status}
@router.get('/lesson/{token}')
async def get_lesson(token:str,db:AsyncSession=Depends(get_db)):
 b=await lesson_from_token(db,token);c=await db.get(Client,b.client_id);r=await db.get(LessonReview,b.id)
 note=await db.get(JourneyNote,b.id)
 prior=await profile_data(db,c)
 prior['history']=[h for h in prior['history'] if (h['date'],h['time'],h['booking_id']) < (b.booking_date.isoformat(),str(b.start_time)[:5],b.id)]
 previous_ids=[h['booking_id'] for h in prior['history']]
 previous_notes=(await db.execute(select(JourneyNote).where(JourneyNote.booking_id.in_(previous_ids)))).scalars().all() if previous_ids else []
 note_map={n.booking_id:n for n in previous_notes}
 previous_replies=(await db.execute(select(JourneyReply).where(JourneyReply.booking_id.in_(previous_ids)).order_by(JourneyReply.created_at,JourneyReply.id))).scalars().all() if previous_ids else []
 replies_by_booking={}
 for rr in previous_replies:replies_by_booking.setdefault(rr.booking_id,[]).append({'instructor':rr.instructor_name,'question':rr.question,'body':rr.body})
 for h in prior['history']:
  n=note_map.get(h['booking_id']);h['goal']=n.goal if n else '';h['reflection']=n.reflection if n else '';h['feeling']=n.feeling if n else '';h['replies']=replies_by_booking.get(h['booking_id'],[])
 return {'goal':note.goal if note else '', 'client_name':c.name,'date':b.booking_date.isoformat(),'status':b.status,'categories':CATEGORIES,'ratings':r.ratings if r else {},'comment':r.comment if r else '', 'prior':prior}
@router.post('/lesson/{token}')
async def save_lesson(token:str,body:ReviewBody,db:AsyncSession=Depends(get_db)):
 b=await lesson_from_token(db,token)
 if b.status!='in_progress':raise HTTPException(409,'Занятие завершено, изменение оценок закрыто')
 try:ratings=normalize_ratings(body.ratings)
 except ValueError as exc:raise HTTPException(422,str(exc))
 if len(body.goal)>300:raise HTTPException(422,'Цель: не более 300 символов')
 if len(body.comment)>2000:raise HTTPException(422,'Комментарий слишком длинный')
 r=await db.get(LessonReview,b.id)
 if not r:r=LessonReview(booking_id=b.id,client_id=b.client_id,instructor_id=b.instructor_id);db.add(r)
 n=await db.get(JourneyNote,b.id)
 if not n:n=JourneyNote(booking_id=b.id);db.add(n)
 n.goal=body.goal.strip();n.updated_at=now_kz()
 r.ratings=ratings;r.comment=body.comment.strip();r.updated_at=now_kz()
 db.add(Event(event_type='lesson_assessment_saved',source='instructor_web',client_id=b.client_id,instructor_id=b.instructor_id,booking_id=b.id,message=f'Сохранена оценка упражнений: запись #{b.booking_number or b.id}, заполнено {len(ratings)} элементов',created_at=now_kz()))
 await db.commit();return {'ok':True}
class JourneyNote(Base):
 __tablename__='lab_journey_notes'
 booking_id=Column(Integer,ForeignKey('bookings.id',ondelete='CASCADE'),primary_key=True)
 goal=Column(Text,nullable=False,default='')
 feeling=Column(String(24),nullable=False,default='')
 reflection=Column(Text,nullable=False,default='')
 updated_at=Column(DateTime,default=now_kz,nullable=False)

class JourneyReply(Base):
 __tablename__='lab_journey_replies'
 id=Column(Integer,primary_key=True,autoincrement=True)
 booking_id=Column(Integer,ForeignKey('bookings.id',ondelete='CASCADE'),nullable=False,index=True)
 context_booking_id=Column(Integer,ForeignKey('bookings.id',ondelete='SET NULL'),nullable=True)
 instructor_id=Column(Integer,ForeignKey('instructors.id',ondelete='SET NULL'),nullable=True)
 instructor_name=Column(String(200),nullable=False)
 question=Column(Text,nullable=False,default='')
 body=Column(Text,nullable=False)
 created_at=Column(DateTime,default=now_kz,nullable=False)

class ReplyBody(BaseModel):
 booking_id:int
 body:str

@router.post('/lesson/{token}/reply')
async def reply(token:str,body:ReplyBody,db:AsyncSession=Depends(get_db)):
 current=await lesson_from_token(db,token)
 if current.status not in ('planned','confirmed','in_progress') or not current.instructor_id:raise HTTPException(403,'Ответ доступен по действующей записи инструктора')
 target=(await db.execute(select(Booking).where(Booking.id==body.booking_id).with_for_update())).scalar_one_or_none()
 if not target or target.client_id!=current.client_id or target.status!='completed' or target.service_type!='training' or (target.booking_date,target.start_time,target.id)>=(current.booking_date,current.start_time,current.id):
  raise HTTPException(404,'Предыдущее занятие не найдено')
 note=await db.get(JourneyNote,target.id)
 if not note or not note.reflection.strip():raise HTTPException(409,'Клиент пока не оставил вопрос')
 text=body.body.strip()
 if not text or len(text)>1000:raise HTTPException(422,'Ответ должен содержать от 1 до 1000 символов')
 instructor=await db.get(Instructor,current.instructor_id)
 r=JourneyReply(booking_id=target.id,context_booking_id=current.id,instructor_id=current.instructor_id,instructor_name=instructor.name if instructor else 'Инструктор',question=note.reflection,body=text)
 db.add(r)
 db.add(Event(event_type='instructor_reply_saved',source='instructor_web',client_id=target.client_id,instructor_id=current.instructor_id,booking_id=target.id,message=f'Инструктор ответил на вопрос по занятию #{target.booking_number or target.id}',created_at=now_kz()))
 await db.commit()
 return {'ok':True}

class ReflectionBody(BaseModel):
 feeling:str
 reflection:str=''

async def require_profile(request,db):
 await check_profile_ip(db,request)
 client=await session_client(db,request.cookies.get('gt_profile',''))
 if not client or client.is_deleted:raise HTTPException(401,'Требуется вход')
 return client

@router.get('/journey')
async def journey(request:Request,db:AsyncSession=Depends(get_db)):
 client=await require_profile(request,db)
 completed=(await db.execute(select(Booking).where(Booking.client_id==client.id,Booking.service_type=='training',Booking.status=='completed'))).scalars().all()
 ids=[b.id for b in completed]
 notes=(await db.execute(select(JourneyNote).where(JourneyNote.booking_id.in_(ids)))).scalars().all() if ids else []
 upcoming=(await db.execute(select(Booking).where(Booking.client_id==client.id,Booking.service_type=='training',Booking.status.in_(['planned','confirmed','pending','in_progress']),Booking.booking_date>=now_kz().date()).order_by(Booking.booking_date,Booking.start_time))).scalars().all()
 upcoming=[b for b in upcoming if b.booking_date>now_kz().date() or b.end_time>now_kz().time()]
 next_lesson=None
 if upcoming:
  b=upcoming[0]; instructor=await db.get(Instructor,b.instructor_id) if b.instructor_id else None
  next_lesson={'date':b.booking_date.isoformat(),'time':str(b.start_time)[:5],'instructor':instructor.name if instructor else '', 'status':b.status,'transmission':b.transmission}
 replies=(await db.execute(select(JourneyReply).where(JourneyReply.booking_id.in_(ids)).order_by(JourneyReply.created_at,JourneyReply.id))).scalars().all() if ids else []
 response_map={}
 for reply in replies:response_map.setdefault(str(reply.booking_id),[]).append({'instructor':reply.instructor_name,'question':reply.question,'body':reply.body,'date':reply.created_at.isoformat()})
 return {'notes':{str(n.booking_id):{'goal':n.goal,'feeling':n.feeling,'reflection':n.reflection,'replies':response_map.get(str(n.booking_id),[])} for n in notes},'upcoming':next_lesson}

@router.post('/reflection/{booking_id}')
async def reflection(booking_id:int,body:ReflectionBody,request:Request,db:AsyncSession=Depends(get_db)):
 client=await require_profile(request,db)
 b=(await db.execute(select(Booking).where(Booking.id==booking_id).with_for_update())).scalar_one_or_none()
 if not b or b.client_id!=client.id or b.status!='completed' or b.service_type!='training':raise HTTPException(404,'Занятие не найдено')
 if body.feeling not in ('hard','clear','confident'):raise HTTPException(422,'Выберите впечатление о занятии')
 if len(body.reflection)>1000:raise HTTPException(422,'Не более 1000 символов')
 n=await db.get(JourneyNote,b.id)
 if not n:n=JourneyNote(booking_id=b.id);db.add(n)
 n.feeling=body.feeling;n.reflection=body.reflection.strip();n.updated_at=now_kz()
 db.add(Event(event_type='lesson_reflection_saved',source='client_web',client_id=client.id,booking_id=b.id,message=f'Клиент сохранил впечатление о занятии #{b.booking_number or b.id}',created_at=now_kz()))
 await db.commit()
 return {'ok':True}
