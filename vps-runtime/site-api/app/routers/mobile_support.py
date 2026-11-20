"""
Мобильное API - Поддержка
GET  /api/mobile/support/messages
POST /api/mobile/support/messages
GET  /api/mobile/support/unread-count
"""
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.models import Client, Event, SupportMessage, now_kz
from app.routers.mobile_auth import get_current_user

router = APIRouter(prefix="/api/mobile/support", tags=["mobile-support"])


class SendMessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


@router.get("/messages")
async def get_messages(
    user: Client = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SupportMessage)
        .where(SupportMessage.client_id == user.id)
        .order_by(SupportMessage.created_at.asc())
    )
    messages = result.scalars().all()

    # Отмечаем сообщения от админа как прочитанные
    for msg in messages:
        if msg.sender == "admin" and not msg.is_read:
            msg.is_read = True
    await db.commit()

    return [
        {
            "id": msg.id,
            "sender": msg.sender,
            "text": msg.text,
            "is_read": msg.is_read,
            "created_at": msg.created_at.isoformat(),
        }
        for msg in messages
    ]


@router.post("/messages", status_code=201)
async def send_message(
    body: SendMessageRequest,
    user: Client = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not body.text.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    recent = (await db.execute(
        select(func.count()).select_from(SupportMessage).where(
            SupportMessage.client_id == user.id,
            SupportMessage.sender == "user",
            SupportMessage.created_at >= now_kz() - timedelta(minutes=1),
        )
    )).scalar() or 0
    if recent >= 5:
        raise HTTPException(status_code=429, detail="Слишком много сообщений. Подождите минуту.")

    msg = SupportMessage(
        client_id=user.id,
        sender="user",
        text=body.text.strip(),
        is_read=False,
    )
    db.add(msg)
    db.add(Event(
        event_type="client_support_message",
        source="mobile",
        client_id=user.id,
        message=f"Клиент «{user.name}» написал в поддержку.",
    ))
    await db.commit()
    await db.refresh(msg)

    return {
        "id": msg.id,
        "sender": msg.sender,
        "text": msg.text,
        "created_at": msg.created_at.isoformat(),
    }


@router.get("/unread-count")
async def get_unread_count(
    user: Client = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SupportMessage).where(
            SupportMessage.client_id == user.id,
            SupportMessage.sender == "admin",
            SupportMessage.is_read == False,
        )
    )
    count = len(result.scalars().all())
    return {"unread_count": count}
