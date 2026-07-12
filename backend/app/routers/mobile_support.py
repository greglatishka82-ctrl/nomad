"""
Чат поддержки — сторона клиента.
GET  /api/mobile/support/messages
POST /api/mobile/support/messages
GET  /api/mobile/support/unread-count
"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.models import SupportMessage, MobileUser
from app.services.mobile_auth import get_current_user_id

router = APIRouter(prefix="/api/mobile/support", tags=["mobile-support"])


class SendMessageRequest(BaseModel):
    text: str

    def validate_text(self) -> "SendMessageRequest":
        if not self.text.strip():
            raise ValueError("Сообщение не может быть пустым")
        return self


@router.get("/messages")
async def get_messages(
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SupportMessage)
        .where(SupportMessage.user_id == user_id)
        .order_by(SupportMessage.created_at.asc())
    )
    messages = result.scalars().all()

    # Помечаем все сообщения от admin как прочитанные
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


@router.post("/messages", status_code=status.HTTP_201_CREATED)
async def send_message(
    body: SendMessageRequest,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Сообщение не может быть пустым")

    msg = SupportMessage(
        user_id=user_id,
        sender="user",
        text=text,
        is_read=False,
    )
    db.add(msg)
    await db.commit()
    await db.refresh(msg)
    return {
        "id": msg.id,
        "sender": msg.sender,
        "text": msg.text,
        "is_read": msg.is_read,
        "created_at": msg.created_at.isoformat(),
    }


@router.get("/unread-count")
async def unread_count(
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(func.count()).select_from(SupportMessage).where(
            SupportMessage.user_id == user_id,
            SupportMessage.sender == "admin",
            SupportMessage.is_read == False,
        )
    )
    count = result.scalar() or 0
    return {"unread_count": count}
