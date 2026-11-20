import secrets
from fastapi import APIRouter,Depends,HTTPException,Request
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models.models import Client
from app.progress_common import ProfileAccess,profile_data,SITE
from app.routers.admin import _get_admin_username
router=APIRouter(prefix='/api/admin/progress',tags=['test-progress'])
async def get_client(db,client_id):
 c=await db.get(Client,client_id)
 if not c or c.is_deleted:raise HTTPException(404,'Клиент не найден')
 return c
@router.get('/{client_id}')
async def details(client_id:int,request:Request,db:AsyncSession=Depends(get_db)):
 _get_admin_username(request);client=await get_client(db,client_id);access=await db.get(ProfileAccess,client_id)
 return {'code':access.code if access else None,'site':SITE,'profile':await profile_data(db,client)}
@router.post('/{client_id}/reset-code')
async def reset(client_id:int,request:Request,db:AsyncSession=Depends(get_db)):
 _get_admin_username(request);await get_client(db,client_id);access=await db.get(ProfileAccess,client_id)
 if access is None: raise HTTPException(409,'Код появится после новой записи клиента')
 access.code='GT-'+secrets.token_hex(8).upper();await db.commit();return {'code':access.code,'site':SITE}