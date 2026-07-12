"""
Чат поддержки — сторона администратора (admin backend).
GET  /api/admin/support/dialogs
GET  /api/admin/support/dialogs/{user_id}
POST /api/admin/support/dialogs/{user_id}/reply
"""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.models import SupportMessage, MobileUser, MobileBooking

router = APIRouter(prefix="/api/admin/support", tags=["admin-support"])


def _require_admin(request: Request) -> None:
    if not request.session.get("admin_username"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется авторизация администратора",
        )


@router.get("/dialogs")
async def list_dialogs(request: Request, db: AsyncSession = Depends(get_db)):
    _require_admin(request)

    result = await db.execute(
        select(SupportMessage.user_id, func.max(SupportMessage.created_at).label("last_msg_at"))
        .group_by(SupportMessage.user_id)
        .order_by(func.max(SupportMessage.created_at).desc())
    )
    rows = result.all()

    dialogs = []
    for row in rows:
        uid = row.user_id
        user = await db.get(MobileUser, uid)
        if not user:
            continue

        last_msg_result = await db.execute(
            select(SupportMessage)
            .where(SupportMessage.user_id == uid)
            .order_by(SupportMessage.created_at.desc())
            .limit(1)
        )
        last_msg = last_msg_result.scalar_one_or_none()

        unread_result = await db.execute(
            select(func.count()).select_from(SupportMessage).where(
                SupportMessage.user_id == uid,
                SupportMessage.sender == "user",
                SupportMessage.is_read == False,
            )
        )
        unread = unread_result.scalar() or 0

        dialogs.append({
            "user_id": uid,
            "user_name": user.name,
            "user_phone": user.phone,
            "user_email": user.email,
            "last_message": last_msg.text[:80] if last_msg else "",
            "last_message_at": last_msg.created_at.isoformat() if last_msg else None,
            "unread_from_user": unread,
            "has_new": unread > 0,
        })
    return dialogs


@router.get("/dialogs/{user_id}")
async def get_dialog(user_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    _require_admin(request)

    user = await db.get(MobileUser, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    msgs_result = await db.execute(
        select(SupportMessage)
        .where(SupportMessage.user_id == user_id)
        .order_by(SupportMessage.created_at.asc())
    )
    messages = msgs_result.scalars().all()

    for msg in messages:
        if msg.sender == "user" and not msg.is_read:
            msg.is_read = True
    await db.commit()

    bookings_result = await db.execute(
        select(MobileBooking)
        .where(MobileBooking.user_id == user_id)
        .order_by(MobileBooking.booking_date.desc())
        .limit(10)
    )
    bookings = bookings_result.scalars().all()

    return {
        "user": {
            "id": user.id,
            "name": user.name,
            "phone": user.phone,
            "email": user.email,
            "created_at": user.created_at.isoformat(),
        },
        "messages": [
            {
                "id": msg.id,
                "sender": msg.sender,
                "text": msg.text,
                "is_read": msg.is_read,
                "created_at": msg.created_at.isoformat(),
            }
            for msg in messages
        ],
        "recent_bookings": [
            {
                "id": b.id,
                "booking_date": b.booking_date.isoformat(),
                "start_time": b.start_time.strftime("%H:%M"),
                "service_type": b.service_type.value,
                "status": b.status.value,
                "price": b.price,
            }
            for b in bookings
        ],
    }


class ReplyRequest(BaseModel):
    text: str


@router.post("/dialogs/{user_id}/reply", status_code=status.HTTP_201_CREATED)
async def reply_to_user(
    user_id: int,
    body: ReplyRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _require_admin(request)

    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Сообщение не может быть пустым")

    user = await db.get(MobileUser, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    msg = SupportMessage(
        user_id=user_id,
        sender="admin",
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
        "created_at": msg.created_at.isoformat(),
    }
