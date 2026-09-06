"""
Мобильное API - Профиль пользователя
GET  /api/mobile/profile
PUT  /api/mobile/profile
POST /api/mobile/profile/avatar
"""
import base64
import asyncio
import binascii
import os
import secrets
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.database import get_db
from app.models.models import Client, Event
from app.routers.mobile_auth import get_current_user
from app.services.phone_utils import normalize_phone

router = APIRouter(prefix="/api/mobile/profile", tags=["mobile-profile"])
MAX_AVATAR_BYTES = 5 * 1024 * 1024
AVATARS_DIR = Path(__file__).parent.parent / "static" / "avatars"


def _image_extension(data: bytes) -> str:
    if len(data) >= 12 and data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if len(data) >= 4 and data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9"):
        return "jpg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    raise HTTPException(status_code=400, detail="Разрешены только изображения PNG, JPEG или WebP")


def _local_avatar_path(url: Optional[str]) -> Optional[Path]:
    prefix = "/static/avatars/"
    if not url or not url.startswith(prefix):
        return None
    candidate = AVATARS_DIR / Path(url[len(prefix):]).name
    return candidate if candidate.parent == AVATARS_DIR else None


class UpdateProfileRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    phone: Optional[str] = Field(default=None, min_length=5, max_length=30)


class UploadAvatarRequest(BaseModel):
    avatar_base64: str  # base64-encoded image


@router.get("")
async def get_profile(user: Client = Depends(get_current_user)):
    return {
        "id": user.id,
        "name": user.name,
        "phone": user.phone,
        "referral_code": user.referral_code,
        "avatar_url": user.avatar_url,
        "created_at": user.created_at.isoformat(),
    }


@router.put("")
async def update_profile(
    body: UpdateProfileRequest,
    user: Client = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if body.name:
        clean_name = body.name.strip()
        if not clean_name or any(ord(char) < 32 for char in clean_name):
            raise HTTPException(status_code=400, detail="Введите корректное имя")
        user.name = clean_name
    if body.phone:
        user.phone = normalize_phone(body.phone)

    changed_fields = []
    if body.name is not None:
        changed_fields.append("имя")
    if body.phone is not None:
        changed_fields.append("телефон")
    if changed_fields:
        db.add(Event(
            event_type="client_profile_updated",
            source="mobile",
            client_id=user.id,
            message=f"Клиент «{user.name}» изменил в профиле: {', '.join(changed_fields)}.",
        ))

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Этот номер телефона уже используется")
    await db.refresh(user)

    return {
        "id": user.id,
        "name": user.name,
        "phone": user.phone,
    }


@router.post("/avatar")
async def upload_avatar(
    body: UploadAvatarRequest,
    user: Client = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Загрузка аватара пользователя.
    Принимает base64-encoded изображение, сохраняет на сервер.
    Пустая строка avatar_base64 = удаление аватара.
    """
    old_path = _local_avatar_path(user.avatar_url)
    if not body.avatar_base64 or body.avatar_base64.strip() == "":
        user.avatar_url = None
        db.add(Event(
            event_type="client_avatar_removed",
            source="mobile",
            client_id=user.id,
            message=f"Клиент «{user.name}» удалил фотографию профиля.",
        ))
        await db.commit()
        if old_path and old_path.exists():
            await asyncio.to_thread(old_path.unlink)
        return {"avatar_url": None, "message": "Аватар удалён"}

    encoded = body.avatar_base64.strip()
    if len(encoded) > (MAX_AVATAR_BYTES * 4 // 3) + 1024:
        raise HTTPException(status_code=413, detail="Изображение слишком большое. Максимум 5 МБ")
    if encoded.startswith("data:"):
        header, separator, encoded = encoded.partition(",")
        if not separator or header not in {
            "data:image/png;base64", "data:image/jpeg;base64", "data:image/jpg;base64", "data:image/webp;base64"
        }:
            raise HTTPException(status_code=400, detail="Недопустимый формат изображения")
    try:
        image_bytes = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="Повреждённое изображение")
    if not image_bytes or len(image_bytes) > MAX_AVATAR_BYTES:
        raise HTTPException(status_code=413, detail="Изображение слишком большое. Максимум 5 МБ")
    ext = _image_extension(image_bytes)

    AVATARS_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"user_{user.id}_{secrets.token_hex(12)}.{ext}"
    filepath = AVATARS_DIR / filename
    temporary = AVATARS_DIR / f".{filename}.tmp"
    await asyncio.to_thread(temporary.write_bytes, image_bytes)
    await asyncio.to_thread(os.replace, temporary, filepath)

    avatar_url = f"/static/avatars/{filename}"
    user.avatar_url = avatar_url
    db.add(Event(
        event_type="client_avatar_updated",
        source="mobile",
        client_id=user.id,
        message=f"Клиент «{user.name}» обновил фотографию профиля.",
    ))
    try:
        await db.commit()
    except Exception:
        await asyncio.to_thread(filepath.unlink, missing_ok=True)
        raise
    if old_path and old_path != filepath and old_path.exists():
        await asyncio.to_thread(old_path.unlink)
    return {"avatar_url": avatar_url, "message": "Аватар успешно загружен"}
