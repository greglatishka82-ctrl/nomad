"""
Чат поддержки — сторона администратора (admin backend).
GET  /api/admin/support/dialogs
GET  /api/admin/support/dialogs/{user_id}
POST /api/admin/support/dialogs/{user_id}/reply
"""
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.models import Booking, Client, Instructor, SupportMessage, now_kz
from app.services.activity_log import record_admin_action
from app.services.push_service import send_push_to_user

router = APIRouter(prefix="/api/admin/support", tags=["admin-support"])

CLIENT_SUPPORT_REPLY_MARKUP = {
    "keyboard": [[{"text": "❌ Завершить чат"}]],
    "resize_keyboard": True,
}
CLIENT_MAIN_REPLY_MARKUP = {
    "keyboard": [
        [{"text": "📝 Записаться"}, {"text": "📋 Мои записи"}],
        [{"text": "ℹ️ Как записаться"}, {"text": "❓ FAQ"}],
        [{"text": "📚 История обучения"}, {"text": "📞 Контакты"}],
        [{"text": "🎁 Пригласи друга"}, {"text": "💬 Поддержка"}],
        [{"text": "🎟️ Сертификат"}],
    ],
    "resize_keyboard": True,
}


def _operation_id(request: Request):
    value = request.headers.get("x-idempotency-key", "").strip()
    return value[:128] or None


def _message_payload(msg: SupportMessage):
    return {
        "id": msg.id, "sender": msg.sender, "text": msg.text,
        "created_at": msg.created_at.isoformat(),
    }


def _is_support_chat_open(client: Client) -> bool:
    return bool(client.support_chat_opened_at and not client.support_chat_closed_at)


def _open_support_chat(client: Client) -> None:
    if not _is_support_chat_open(client):
        client.support_chat_opened_at = now_kz()
        client.support_chat_closed_at = None


def _require_admin(request: Request) -> str:
    # The admin panel uses its own signed cookie session. Mobile JWTs are
    # deliberately not accepted here: they identify clients, not admins.
    username = request.session.get("admin_username")
    if username:
        return username
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Требуется авторизация администратора",
    )


@router.get("/dialogs")
async def list_dialogs(request: Request, db: AsyncSession = Depends(get_db)):
    _require_admin(request)

    result = await db.execute(
        select(SupportMessage.client_id, func.max(SupportMessage.created_at).label("last_msg_at"))
        .where(SupportMessage.client_id.isnot(None))
        .group_by(SupportMessage.client_id)
        .order_by(func.max(SupportMessage.created_at).desc())
    )
    rows = result.all()

    dialogs = []
    for row in rows:
        uid = row.client_id
        user = await db.get(Client, uid)
        if not user or user.is_deleted:
            continue

        last_msg_result = await db.execute(
            select(SupportMessage)
            .where(SupportMessage.client_id == uid)
            .order_by(SupportMessage.created_at.desc())
            .limit(1)
        )
        last_msg = last_msg_result.scalar_one_or_none()

        unread_result = await db.execute(
            select(func.count()).select_from(SupportMessage).where(
                SupportMessage.client_id == uid,
                SupportMessage.sender == "user",
                SupportMessage.is_admin_read == False,
            )
        )
        unread = unread_result.scalar() or 0

        dialogs.append({
            "user_id": uid,
            "user_name": user.name,
            "user_phone": user.phone,
            "last_message": last_msg.text[:80] if last_msg else "",
            "last_message_at": last_msg.created_at.isoformat() if last_msg else None,
            "unread_from_user": unread,
            "has_new": unread > 0,
            "channel": last_msg.channel if last_msg else "mobile",
            "support_chat_is_open": _is_support_chat_open(user),
        })
    return dialogs


@router.get("/instructors/dialogs")
async def list_instructor_dialogs(request: Request, db: AsyncSession = Depends(get_db)):
    _require_admin(request)

    instructors_result = await db.execute(select(Instructor).order_by(Instructor.name))
    instructors = instructors_result.scalars().all()
    dialogs = []
    for inst in instructors:
        last_msg_result = await db.execute(
            select(SupportMessage)
            .where(SupportMessage.instructor_id == inst.id)
            .order_by(SupportMessage.created_at.desc())
            .limit(1)
        )
        last_msg = last_msg_result.scalar_one_or_none()
        unread_result = await db.execute(
            select(func.count()).select_from(SupportMessage).where(
                SupportMessage.instructor_id == inst.id,
                SupportMessage.sender == "instructor",
                SupportMessage.is_admin_read == False,
            )
        )
        unread = unread_result.scalar() or 0
        dialogs.append({
            "user_id": inst.id,
            "user_name": inst.name,
            "user_phone": inst.phone,
            "telegram_id": inst.telegram_id,
            "telegram_username": inst.telegram_username,
            "last_message": last_msg.text[:80] if last_msg else "Можно написать инструктору",
            "last_message_at": last_msg.created_at.isoformat() if last_msg else None,
            "unread_from_user": unread,
            "has_new": unread > 0,
        })
    return dialogs


@router.get("/dialogs/{user_id}")
async def get_dialog(user_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    _require_admin(request)

    user = await db.get(Client, user_id)
    if not user or user.is_deleted:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    msgs_result = await db.execute(
        select(SupportMessage)
        .where(SupportMessage.client_id == user_id)
        .order_by(SupportMessage.id.asc())
    )
    messages = msgs_result.scalars().all()

    for msg in messages:
        if msg.sender == "user" and not msg.is_read:
            msg.is_read = True
        if msg.sender == "user" and not msg.is_admin_read:
            msg.is_admin_read = True
    await db.commit()

    bookings_result = await db.execute(
        select(Booking)
        .where(Booking.client_id == user_id)
        .order_by(Booking.booking_date.desc())
        .limit(10)
    )
    bookings = bookings_result.scalars().all()

    return {
        "user": {
            "id": user.id,
            "name": user.name,
            "phone": user.phone,
            "created_at": user.created_at.isoformat(),
            "support_chat_is_open": _is_support_chat_open(user),
        },
        "messages": [
            {
                "id": msg.id,
                "sender": msg.sender,
                "text": msg.text,
                "is_read": msg.is_read,
                "created_at": msg.created_at.isoformat(),
                "channel": msg.channel or "mobile",
            }
            for msg in messages
        ],
        "recent_bookings": [
            {
                "id": b.id,
                "booking_date": b.booking_date.isoformat(),
                "start_time": b.start_time.strftime("%H:%M"),
                "service_type": b.service_type,
                "status": b.status,
                "price": b.price,
            }
            for b in bookings
        ],
    }


@router.get("/instructors/dialogs/{instructor_id}")
async def get_instructor_dialog(instructor_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    _require_admin(request)

    instructor = await db.get(Instructor, instructor_id)
    if not instructor:
        raise HTTPException(status_code=404, detail="Инструктор не найден")

    msgs_result = await db.execute(
        select(SupportMessage)
        .where(SupportMessage.instructor_id == instructor_id)
        .order_by(SupportMessage.id.asc())
    )
    messages = msgs_result.scalars().all()

    for msg in messages:
        if msg.sender == "instructor" and not msg.is_admin_read:
            msg.is_admin_read = True
    await db.commit()

    return {
        "user": {
            "id": instructor.id,
            "name": instructor.name,
            "phone": instructor.phone,
            "telegram_id": instructor.telegram_id,
            "created_at": instructor.created_at.isoformat(),
        },
        "messages": [
            {
                "id": msg.id,
                "sender": "admin" if msg.sender == "admin" else "user",
                "text": msg.text,
                "is_read": msg.is_read,
                "created_at": msg.created_at.isoformat(),
            }
            for msg in messages
        ],
        "recent_bookings": [],
    }


class ReplyRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


@router.post("/dialogs/{user_id}/reply", status_code=status.HTTP_201_CREATED)
async def reply_to_user(
    user_id: int,
    body: ReplyRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    username = _require_admin(request)

    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Сообщение не может быть пустым")
    operation_id = _operation_id(request)
    if operation_id:
        existing = (await db.execute(select(SupportMessage).where(
            SupportMessage.offline_operation_id == operation_id
        ))).scalar_one_or_none()
        if existing:
            return _message_payload(existing)

    user = await db.get(Client, user_id)
    if not user or user.is_deleted:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    _open_support_chat(user)

    msg = SupportMessage(
        client_id=user_id,
        channel="telegram" if user.telegram_id else "client",
        sender="admin",
        text=text,
        is_read=False,
        offline_operation_id=operation_id,
    )

    db.add(msg)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = (await db.execute(select(SupportMessage).where(
            SupportMessage.offline_operation_id == operation_id
        ))).scalar_one_or_none() if operation_id else None
        if existing:
            return _message_payload(existing)
        raise
    await db.refresh(msg)
    await record_admin_action(
        db, username, "support_reply_client",
        f"Администратор ответил клиенту «{user.name}» в поддержке.",
    )

    # Отправляем push-уведомление пользователю через OneSignal
    # Даже если приложение закрыто — уведомление придёт на экран блокировки
    preview = text[:60] + "…" if len(text) > 60 else text
    await send_push_to_user(
        user_id=user_id,
        title="Ответ поддержки 💬",
        body=preview,
        data={"type": "support_reply"},
    )

    if user.telegram_id and settings.BOT_TOKEN:
        try:
            async with httpx.AsyncClient(timeout=10) as tg_client:
                await tg_client.post(
                    f"https://api.telegram.org/bot{settings.BOT_TOKEN}/sendMessage",
                    json={
                        "chat_id": user.telegram_id.strip(),
                        "text": f"💬 Ответ поддержки:\n\n{text}",
                        "reply_markup": CLIENT_SUPPORT_REPLY_MARKUP,
                    },
                )
        except Exception as e:
            print(f"[support telegram reply] ERROR: {e}")

    return _message_payload(msg)


@router.post("/dialogs/{user_id}/close")
async def close_user_dialog(
    user_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Закрывает текущий чат поддержки с обеих сторон."""
    username = _require_admin(request)

    user = await db.get(Client, user_id)
    if not user or user.is_deleted:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    if not _is_support_chat_open(user):
        return {"ok": True, "already_closed": True}

    user.support_chat_closed_at = now_kz()
    await db.commit()
    await record_admin_action(
        db, username, "support_close_client",
        f"Администратор закрыл чат поддержки с клиентом «{user.name}».",
    )

    if user.telegram_id and settings.BOT_TOKEN:
        try:
            async with httpx.AsyncClient(timeout=10) as tg_client:
                await tg_client.post(
                    f"https://api.telegram.org/bot{settings.BOT_TOKEN}/sendMessage",
                    json={
                        "chat_id": user.telegram_id.strip(),
                        "text": "💬 Чат с администратором завершён.",
                        "reply_markup": CLIENT_MAIN_REPLY_MARKUP,
                    },
                )
        except Exception as e:
            print(f"[support telegram close] ERROR: {e}")

    return {"ok": True, "already_closed": False}


async def get_unread_support_count(db: AsyncSession) -> int:
    """Use the same visible-dialog predicates as the admin support screen."""
    unread_clients = (await db.execute(
        select(func.count()).select_from(SupportMessage)
        .join(Client, SupportMessage.client_id == Client.id)
        .where(
            Client.is_deleted == False,
            SupportMessage.sender == "user",
            SupportMessage.is_admin_read == False,
        )
    )).scalar() or 0
    unread_instructors = (await db.execute(
        select(func.count()).select_from(SupportMessage)
        .join(Instructor, SupportMessage.instructor_id == Instructor.id)
        .where(
            SupportMessage.sender == "instructor",
            SupportMessage.is_admin_read == False,
        )
    )).scalar() or 0
    return unread_clients + unread_instructors


@router.post("/instructors/dialogs/{instructor_id}/reply", status_code=status.HTTP_201_CREATED)
async def reply_to_instructor(
    instructor_id: int,
    body: ReplyRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    username = _require_admin(request)

    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Сообщение не может быть пустым")
    operation_id = _operation_id(request)
    if operation_id:
        existing = (await db.execute(select(SupportMessage).where(
            SupportMessage.offline_operation_id == operation_id
        ))).scalar_one_or_none()
        if existing:
            return _message_payload(existing)

    instructor = await db.get(Instructor, instructor_id)
    if not instructor:
        raise HTTPException(status_code=404, detail="Инструктор не найден")

    msg = SupportMessage(
        instructor_id=instructor_id,
        channel="instructor",
        sender="admin",
        text=text,
        is_read=False,
        is_admin_read=True,
        offline_operation_id=operation_id,
    )
    db.add(msg)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = (await db.execute(select(SupportMessage).where(
            SupportMessage.offline_operation_id == operation_id
        ))).scalar_one_or_none() if operation_id else None
        if existing:
            return _message_payload(existing)
        raise
    await db.refresh(msg)
    await record_admin_action(
        db, username, "support_reply_instructor",
        f"Администратор ответил инструктору «{instructor.name}» в поддержке.",
    )

    if instructor.telegram_id and settings.INSTRUCTOR_BOT_TOKEN:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://api.telegram.org/bot{settings.INSTRUCTOR_BOT_TOKEN}/sendMessage",
                json={"chat_id": instructor.telegram_id.strip(), "text": f"Сообщение от администратора:\n\n{text}"},
            )

    return _message_payload(msg)


@router.delete("/dialogs/{user_id}")
async def delete_dialog(user_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    """Удаляет весь диалог (все сообщения) с указанным пользователем"""
    username = _require_admin(request)
    user = await db.get(Client, user_id)
    user_name = user.name if user else f"ID {user_id}"

    result = await db.execute(
        select(SupportMessage).where(SupportMessage.client_id == user_id)
    )
    messages = result.scalars().all()
    if not messages:
        raise HTTPException(status_code=404, detail="Диалог не найден")

    for msg in messages:
        await db.delete(msg)
    await db.commit()
    await record_admin_action(
        db, username, "delete_support_dialog",
        f"Администратор удалил диалог поддержки с клиентом «{user_name}».",
    )
    return {"ok": True}


@router.delete("/instructors/dialogs/{instructor_id}")
async def delete_instructor_dialog(instructor_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    username = _require_admin(request)
    instructor = await db.get(Instructor, instructor_id)
    instructor_name = instructor.name if instructor else f"ID {instructor_id}"

    result = await db.execute(
        select(SupportMessage).where(SupportMessage.instructor_id == instructor_id)
    )
    messages = result.scalars().all()
    if not messages:
        raise HTTPException(status_code=404, detail="Диалог не найден")

    for msg in messages:
        await db.delete(msg)
    await db.commit()
    await record_admin_action(
        db, username, "delete_instructor_support_dialog",
        f"Администратор удалил диалог поддержки с инструктором «{instructor_name}».",
    )
    return {"ok": True}


@router.post("/mark-viewed")
async def mark_support_viewed(request: Request, db: AsyncSession = Depends(get_db)):
    """Устаревший совместимый маршрут без изменения состояния сообщений.

    Непрочитанность поддержки должна сниматься только при открытии конкретного
    диалога, а не при входе на вкладку. Маршрут оставлен для старых версий UI,
    чтобы они не могли массово пометить обращения прочитанными.
    """
    _require_admin(request)
    return {"ok": True, "deprecated": True}
