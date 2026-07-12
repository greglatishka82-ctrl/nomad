"""
Профиль мобильного пользователя.
GET    /api/mobile/profile
PUT    /api/mobile/profile
DELETE /api/mobile/profile
POST   /api/mobile/fcm-token
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.models import MobileUser
from app.services.auth import hash_password, verify_password
from app.services.mobile_auth import get_current_user_id

router = APIRouter(prefix="/api/mobile", tags=["mobile-profile"])


# ── Схемы ──────────────────────────────────────────────────────────────────────

class ProfileResponse(BaseModel):
    id: int
    name: str
    phone: str
    email: str
    referral_code: Optional[str]
    created_at: str

    class Config:
        from_attributes = True


class UpdateProfileRequest(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            v = v.strip()
            if not v:
                raise ValueError("Имя не может быть пустым")
        return v


class FcmTokenRequest(BaseModel):
    fcm_token: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def password_length(cls, v: str) -> str:
        if len(v) < 6:
            raise ValueError("Пароль должен содержать не менее 6 символов")
        return v


# ── Хелпер ────────────────────────────────────────────────────────────────────

async def _get_user(user_id: int, db: AsyncSession) -> MobileUser:
    result = await db.execute(select(MobileUser).where(MobileUser.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден")
    return user


# ── Эндпоинты ─────────────────────────────────────────────────────────────────

@router.get("/profile")
async def get_profile(
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    user = await _get_user(user_id, db)
    return {
        "id": user.id,
        "name": user.name,
        "phone": user.phone,
        "email": user.email,
        "referral_code": user.referral_code,
        "created_at": user.created_at.isoformat(),
    }


@router.put("/profile")
async def update_profile(
    body: UpdateProfileRequest,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    user = await _get_user(user_id, db)
    if body.name is not None:
        user.name = body.name
    if body.phone is not None:
        user.phone = body.phone.strip()
    await db.commit()
    return {"message": "Профиль обновлён"}


@router.post("/profile/change-password")
async def change_password(
    body: ChangePasswordRequest,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    user = await _get_user(user_id, db)
    if not verify_password(body.old_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Неверный текущий пароль",
        )
    user.password_hash = hash_password(body.new_password)
    await db.commit()
    return {"message": "Пароль изменён"}


@router.delete("/profile", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    user = await _get_user(user_id, db)
    user.is_active = False  # soft delete
    await db.commit()


@router.post("/fcm-token")
async def save_fcm_token(
    body: FcmTokenRequest,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    user = await _get_user(user_id, db)
    user.fcm_token = body.fcm_token
    await db.commit()
    return {"message": "FCM токен сохранён"}


class OneSignalIdRequest(BaseModel):
    onesignal_id: str


@router.post("/onesignal-id")
async def save_onesignal_id(
    body: OneSignalIdRequest,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    user = await _get_user(user_id, db)
    user.fcm_token = body.onesignal_id  # Сохраняем в то же поле
    await db.commit()
    return {"message": "OneSignal ID сохранён"}
