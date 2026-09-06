import csv
import io
import re
import secrets
from datetime import datetime, date, time, timedelta
from html import escape
from typing import Optional
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, Body
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import delete as sa_delete, select, and_, func, or_, update, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_db
from app.services.client_lifecycle import find_client_by_phone, reactivate_deleted_client
from app.services.phone_utils import normalize_phone, phones_match

# Павлодар / Алматы — UTC+6
KZ_TZ = ZoneInfo("Asia/Almaty")


def today_kz() -> date:
    return datetime.now(KZ_TZ).date()


def now_kz():
    return datetime.now(KZ_TZ).replace(tzinfo=None)


async def _manual_booking_window(db: AsyncSession) -> tuple[date, date]:
    """Return the rolling date window for manual appointments.

    The normal window is today plus four following calendar days. Once every
    instructor's workday has ended, today's unavailable date is replaced by
    the next fifth day. Existing bookings are never inspected or changed.
    """
    current = datetime.now(KZ_TZ)
    first_date = current.date()
    active_instructors = (await db.execute(
        select(Instructor).where(Instructor.is_active == True)
    )).scalars().all()
    latest_end = None
    for instructor in active_instructors:
        schedule = await get_effective_schedule(db, instructor, first_date)
        if schedule and (latest_end is None or schedule[1] > latest_end):
            latest_end = schedule[1]
    if latest_end and current.time() > latest_end:
        first_date += timedelta(days=1)
    return first_date, first_date + timedelta(days=4)


async def _ensure_manual_booking_date_in_window(db: AsyncSession, booking_date: date) -> None:
    first_date, last_date = await _manual_booking_window(db)
    if booking_date < first_date or booking_date > last_date:
        raise HTTPException(
            status_code=400,
            detail=(f"Для ручной записи доступен период с {first_date.strftime('%d.%m.%Y')} "
                    f"по {last_date.strftime('%d.%m.%Y')}")
        )


async def _ensure_days_off_have_no_active_bookings(
    db: AsyncSession, instructor: "Instructor", requested_dates: set[date]
) -> None:
    """A day off must never hide an already active lesson."""
    if not requested_dates:
        return
    existing_dates = set((await db.execute(
        select(InstructorDayOff.day_off_date).where(and_(
            InstructorDayOff.instructor_id == instructor.id,
            InstructorDayOff.day_off_date.in_(requested_dates),
        ))
    )).scalars().all())
    newly_blocked_dates = requested_dates - existing_dates
    if not newly_blocked_dates:
        return
    appointments = await _get_active_instructor_appointments(
        db, instructor.id, dates=newly_blocked_dates
    )
    if appointments:
        by_date: dict[date, list[str]] = {}
        for appointment in appointments:
            by_date.setdefault(appointment["date"], []).append(appointment["label"])
        details = "; ".join(
            f"{day.strftime('%d.%m.%Y')}: {', '.join(numbers)}"
            for day, numbers in by_date.items()
        )
        raise HTTPException(
            status_code=400,
            detail=(f'Нельзя назначить выходной: у инструктора «{instructor.name}» есть '
                    f'активные записи — {details}. Сначала перенесите или отмените их.'),
        )


async def _restore_booking_package_if_needed(db: AsyncSession, booking: "Booking") -> None:
    """Return a reserved package entitlement exactly once on final cancellation."""
    if not booking.package_id:
        return
    purchase = (await db.execute(select(ClientPackage).where(
        ClientPackage.client_id == booking.client_id,
        ClientPackage.package_id == booking.package_id,
    ).order_by(ClientPackage.purchased_at.desc()))).scalars().first()
    if purchase:
        if booking.package_bonus_exam_used:
            purchase.remaining_bonus_exams += 1
        else:
            purchase.remaining_sessions += 1
        purchase.is_active = True


from app.models.models import (
    Admin, Instructor, Client, Booking, BookingStatus, ServiceType,
    TransmissionType, RatingRecord, RatingVote, Package, ClientPackage,
    Certificate, FAQItem, AuditLog, Event, ArchivedLog, NotificationSent, InstructorGender,
    MobileBooking, MobileUser, MobileUserPackage, MobileAppReview, ReferralRecord, SupportMessage, FAQ, InstructorDayOff,
    InstructorDailySchedule, InstructorRotation, AdminState, WaitingListEntry, ClientBlock,
    CertificateRequest, GenderAnalytics, MobileSession, Vehicle
)
from app.services.auth import hash_password, verify_password
from app.services.activity_log import record_admin_action
from app.services.push_service import send_push_to_user
from app.routers.support import get_unread_support_count
from app.services.booking_service import (
    appointment_fits_schedule, count_booked_at_location, find_best_instructor,
    get_busy_instructor_ids, get_effective_schedule, is_instructor_available,
    RUSSIAN_DAY_NAMES, reserve_available_vehicle, slot_has_capacity, teaches_service
)

router = APIRouter(prefix="/api/admin", tags=["admin"])

ACTIVE_BOOKING_STATUSES = (
    "pending", "cancellation_pending", "reschedule_pending",
    "planned", "confirmed", "in_progress",
)
ACTIVE_MOBILE_BOOKING_STATUSES = ("pending", "planned", "confirmed", "in_progress")

# Отправляется отдельным сообщением только после подтверждения новой записи.
# В HTML-режиме Telegram используем тег <b>, а не Markdown-звёздочки.
CASH_PAYMENT_NOTICE = (
    "📢 Уважаемые клиенты!\n"
    "💵 Обращаем ваше внимание: <b>оплатить занятие можно наличными или через Kaspi QR.</b>\n"
    "🙏 Пожалуйста, учитывайте это перед занятием.\n"
    "🤝 Спасибо!"
)


async def _send_confirmed_booking_messages(tg_client, chat_id: str, confirmation_message: str) -> None:
    """Send the confirmation and its cash-payment notice as two ordered messages."""
    await tg_client.post(
        f"https://api.telegram.org/bot{settings.BOT_TOKEN}/sendMessage",
        json={"chat_id": chat_id, "text": confirmation_message, "parse_mode": "HTML"},
    )
    await tg_client.post(
        f"https://api.telegram.org/bot{settings.BOT_TOKEN}/sendMessage",
        json={"chat_id": chat_id, "text": CASH_PAYMENT_NOTICE, "parse_mode": "HTML"},
    )


async def _get_active_instructor_appointments(
    db: AsyncSession,
    instructor_id: int,
    *,
    dates: Optional[set[date]] = None,
    from_date: Optional[date] = None,
) -> list[dict]:
    """Return active appointments from both booking tables without mutating them."""
    booking_conditions = [
        Booking.instructor_id == instructor_id,
        Booking.status.in_(ACTIVE_BOOKING_STATUSES),
    ]
    mobile_conditions = [
        MobileBooking.instructor_id == instructor_id,
        MobileBooking.status.in_(ACTIVE_MOBILE_BOOKING_STATUSES),
    ]
    if dates is not None:
        if not dates:
            return []
        booking_conditions.append(Booking.booking_date.in_(dates))
        mobile_conditions.append(MobileBooking.booking_date.in_(dates))
    if from_date is not None:
        booking_conditions.append(Booking.booking_date >= from_date)
        mobile_conditions.append(MobileBooking.booking_date >= from_date)

    bookings = (await db.execute(
        select(Booking).where(and_(*booking_conditions)).order_by(
            Booking.booking_date, Booking.start_time
        )
    )).scalars().all()
    mobile_bookings = (await db.execute(
        select(MobileBooking).where(and_(*mobile_conditions)).order_by(
            MobileBooking.booking_date, MobileBooking.start_time
        )
    )).scalars().all()

    appointments = [
        {
            "date": item.booking_date,
            "start": item.start_time,
            "end": item.end_time,
            "label": f"№ {item.booking_number or f'ID {item.id}'}",
        }
        for item in bookings
    ]
    for item in mobile_bookings:
        end_time = item.end_time
        if end_time is None:
            service = item.service_type.value if hasattr(item.service_type, "value") else str(item.service_type)
            duration = (
                settings.EXAM_DURATION_MINUTES
                if service == "exam" else settings.TRAINING_DURATION_MINUTES
            )
            end_time = (datetime.combine(item.booking_date, item.start_time) + timedelta(
                minutes=duration
            )).time()
        appointments.append({
            "date": item.booking_date,
            "start": item.start_time,
            "end": end_time,
            "label": f"мобильная ID {item.id}",
        })
    appointments.sort(key=lambda item: (item["date"], item["start"]))
    return appointments


def _schedule_conflict_detail(appointments: list[dict]) -> str:
    preview = "; ".join(
        f"{item['date'].strftime('%d.%m.%Y')} {item['start'].strftime('%H:%M')} ({item['label']})"
        for item in appointments[:5]
    )
    if len(appointments) > 5:
        preview += f"; ещё {len(appointments) - 5}"
    return preview


async def _ensure_default_schedule_preserves_active_bookings(
    db: AsyncSession,
    instructor: Instructor,
    *,
    work_start: Optional[time],
    work_end: Optional[time],
    lunch_start: Optional[time],
    lunch_end: Optional[time],
    days_off: str,
) -> None:
    appointments = await _get_active_instructor_appointments(
        db, instructor.id, from_date=today_kz()
    )
    if not appointments:
        return

    appointment_dates = {item["date"] for item in appointments}
    daily_rows = (await db.execute(select(InstructorDailySchedule).where(and_(
        InstructorDailySchedule.instructor_id == instructor.id,
        InstructorDailySchedule.schedule_date.in_(appointment_dates),
    )))).scalars().all()
    daily_by_date = {item.schedule_date: item for item in daily_rows}
    date_days_off = set((await db.execute(select(InstructorDayOff.day_off_date).where(and_(
        InstructorDayOff.instructor_id == instructor.id,
        InstructorDayOff.day_off_date.in_(appointment_dates),
    )))).scalars().all())
    current_weekly_days_off = {
        item.strip() for item in (instructor.days_off or "").split(",") if item.strip()
    }
    proposed_weekly_days_off = {
        item.strip() for item in (days_off or "").split(",") if item.strip()
    }

    conflicts = []
    for appointment in appointments:
        booking_date = appointment["date"]
        daily = daily_by_date.get(booking_date)
        if daily:
            if daily.is_day_off:
                continue
            current_schedule = (
                daily.working_hours_start or instructor.working_hours_start,
                daily.working_hours_end or instructor.working_hours_end,
                daily.lunch_start,
                daily.lunch_end,
            )
            proposed_schedule = (
                daily.working_hours_start or work_start,
                daily.working_hours_end or work_end,
                daily.lunch_start,
                daily.lunch_end,
            )
        elif booking_date in date_days_off:
            continue
        else:
            day_name = RUSSIAN_DAY_NAMES[booking_date.weekday()]
            current_schedule = None if day_name in current_weekly_days_off else (
                instructor.working_hours_start, instructor.working_hours_end,
                instructor.lunch_start, instructor.lunch_end,
            )
            proposed_schedule = None if day_name in proposed_weekly_days_off else (
                work_start, work_end, lunch_start, lunch_end,
            )

        current_fits = bool(current_schedule) and appointment_fits_schedule(
            appointment["start"], appointment["end"], *current_schedule
        )
        proposed_fits = bool(proposed_schedule) and appointment_fits_schedule(
            appointment["start"], appointment["end"], *proposed_schedule
        )
        if current_fits and not proposed_fits:
            conflicts.append(appointment)

    if conflicts:
        raise HTTPException(
            status_code=409,
            detail=(
                "Нельзя изменить общий график: новые часы, обед или выходные "
                f"конфликтуют с активными записями — {_schedule_conflict_detail(conflicts)}. "
                "Сначала перенесите записи либо оставьте текущий график."
            ),
        )


async def _ensure_daily_schedule_preserves_active_bookings(
    db: AsyncSession,
    instructor: Instructor,
    schedule_date: date,
    *,
    is_day_off: bool,
    work_start: Optional[time],
    work_end: Optional[time],
    lunch_start: Optional[time],
    lunch_end: Optional[time],
) -> None:
    appointments = await _get_active_instructor_appointments(
        db, instructor.id, dates={schedule_date}
    )
    if not appointments:
        return
    current_schedule = await get_effective_schedule(db, instructor, schedule_date)
    proposed_schedule = None if is_day_off else (
        work_start or instructor.working_hours_start,
        work_end or instructor.working_hours_end,
        lunch_start,
        lunch_end,
    )
    conflicts = [
        item for item in appointments
        if current_schedule
        and appointment_fits_schedule(item["start"], item["end"], *current_schedule)
        and (
            not proposed_schedule
            or not appointment_fits_schedule(item["start"], item["end"], *proposed_schedule)
        )
    ]
    if conflicts:
        raise HTTPException(
            status_code=409,
            detail=(
                "Нельзя изменить расписание дня: оно конфликтует с активными записями — "
                f"{_schedule_conflict_detail(conflicts)}. Сначала перенесите записи."
            ),
        )


async def _ensure_daily_schedule_deletion_preserves_active_bookings(
    db: AsyncSession,
    instructor: Instructor,
    schedule_date: date,
) -> None:
    """Do not remove an override if the fallback schedule hides a booking."""
    appointments = await _get_active_instructor_appointments(
        db, instructor.id, dates={schedule_date}
    )
    if not appointments:
        return

    current_schedule = await get_effective_schedule(db, instructor, schedule_date)
    has_date_day_off = (await db.scalar(select(InstructorDayOff.id).where(and_(
        InstructorDayOff.instructor_id == instructor.id,
        InstructorDayOff.day_off_date == schedule_date,
    )))) is not None
    weekly_days_off = {
        item.strip() for item in (instructor.days_off or "").split(",") if item.strip()
    }
    falls_on_weekly_day_off = RUSSIAN_DAY_NAMES[schedule_date.weekday()] in weekly_days_off
    fallback_schedule = None if has_date_day_off or falls_on_weekly_day_off else (
        instructor.working_hours_start,
        instructor.working_hours_end,
        instructor.lunch_start,
        instructor.lunch_end,
    )
    conflicts = [
        item for item in appointments
        if current_schedule
        and appointment_fits_schedule(item["start"], item["end"], *current_schedule)
        and (
            not fallback_schedule
            or not appointment_fits_schedule(
                item["start"], item["end"], *fallback_schedule
            )
        )
    ]
    if conflicts:
        raise HTTPException(
            status_code=409,
            detail=(
                "Нельзя удалить индивидуальный график: после возврата к общему "
                "графику возникнет конфликт с активными записями — "
                f"{_schedule_conflict_detail(conflicts)}. Сначала перенесите записи."
            ),
        )


async def _count_related(db: AsyncSession, model, *conditions) -> int:
    return int((await db.scalar(
        select(func.count()).select_from(model).where(and_(*conditions))
    )) or 0)


async def _detach_instructor_history(db: AsyncSession, instructor_id: int) -> None:
    """Keep historical rows, but remove a deleted instructor from their links."""
    for model in (
        Booking, MobileBooking, RatingRecord, Event, NotificationSent,
        SupportMessage, WaitingListEntry,
    ):
        await db.execute(
            update(model).where(model.instructor_id == instructor_id).values(instructor_id=None)
        )
    await db.execute(
        sa_delete(InstructorRotation).where(InstructorRotation.instructor_id == instructor_id)
    )


async def _client_history_links(db: AsyncSession, client_id: int) -> list[str]:
    checks = [
        ("записи", Booking, Booking.client_id == client_id),
        ("пакеты", ClientPackage, ClientPackage.client_id == client_id),
        ("реферальные связи", ReferralRecord, or_(
            ReferralRecord.referrer_client_id == client_id,
            ReferralRecord.referred_client_id == client_id,
        )),
        ("сообщения поддержки", SupportMessage, SupportMessage.client_id == client_id),
        ("события", Event, Event.client_id == client_id),
        ("мобильные сессии", MobileSession, MobileSession.client_id == client_id),
        ("ограничения", ClientBlock, ClientBlock.client_id == client_id),
        ("заявки сертификатов", CertificateRequest, CertificateRequest.client_id == client_id),
        ("сертификаты", Certificate, or_(
            Certificate.activated_by_client_id == client_id,
            Certificate.used_by_user_id == client_id,
        )),
        ("приглашённые клиенты", Client, Client.referred_by_client_id == client_id),
    ]
    links = []
    for label, model, condition in checks:
        count = await _count_related(db, model, condition)
        if count:
            links.append(f"{label}: {count}")
    return links


def _get_admin_username(request: Request) -> str:
    username = request.session.get("admin_username")
    if not username:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return username


def _idempotency_key(request: Request) -> Optional[str]:
    """Return a bounded key that identifies a single browser write attempt."""
    value = request.headers.get("x-idempotency-key", "").strip()
    return value[:128] or None


async def _commit_idempotent_create(db: AsyncSession, model, operation_id: Optional[str]):
    """Commit a create and recover the winning row from a concurrent retry."""
    try:
        await db.commit()
        return None
    except IntegrityError:
        await db.rollback()
        if operation_id:
            existing = (await db.execute(select(model).where(
                model.offline_operation_id == operation_id
            ))).scalar_one_or_none()
            if existing:
                return existing
        raise


async def _audit(db: AsyncSession, admin_username: str, action: str, details: str = ""):
    await record_admin_action(db, admin_username, action, details)


def _validate_client_password(password: str) -> None:
    """Passwords for the mobile app are typed with an English keyboard."""
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="Пароль должен содержать минимум 6 символов")
    if not password.isascii() or any(char.isspace() for char in password) or not any(char.isalpha() for char in password):
        raise HTTPException(
            status_code=400,
            detail="Используйте пароль от 6 символов: латинские буквы, цифры и символы без пробелов",
        )


async def _recent_client_cancellation_count(db: AsyncSession, client_id: int) -> int:
    """Count final client-requested cancellations during the preceding 24 hours."""
    since = now_kz() - timedelta(hours=24)
    return (await db.execute(
        select(func.count()).select_from(AuditLog).where(
            AuditLog.action == "booking_cancelled",
            AuditLog.created_at >= since,
            AuditLog.details.contains(f"(id={client_id})"),
            or_(
                AuditLog.details.contains("source=client_cancellation"),
                # Direct cancellations from the mobile API used this wording
                # before the explicit source marker was added.
                AuditLog.details.contains("отменена клиентом"),
            ),
        )
    )).scalar() or 0


async def _add_support_notice(db: AsyncSession, client_id: int, text: str) -> None:
    db.add(SupportMessage(
        client_id=client_id,
        channel="client",
        sender="admin",
        text=text,
        is_read=False,
        is_admin_read=True,
    ))


async def _apply_cancellation_limit(db: AsyncSession, client: Client) -> tuple[int, str | None]:
    """Apply the one-hour cancellation pause or the fifth-cancellation daily block."""
    cancellations = await _recent_client_cancellation_count(db, client.id)
    now = now_kz()
    duration = timedelta(hours=24) if cancellations >= 5 else timedelta(hours=1)
    reason = (
        "Превышен лимит: 5 отмен записей за 24 часа"
        if cancellations >= 5 else "Отмена записи: ограничение на 1 час"
    )
    active = (await db.execute(select(ClientBlock).where(
        ClientBlock.client_id == client.id,
        ClientBlock.blocked_until > now,
    ))).scalars().first()
    blocked_until = now + duration
    if active:
        active.blocked_until = max(active.blocked_until, blocked_until)
        active.reason = reason
    else:
        db.add(ClientBlock(client_id=client.id, blocked_until=blocked_until, reason=reason))

    if cancellations >= 5:
        return cancellations, (
            "Мы временно ограничили создание и отмену записей на 24 часа: за последние сутки "
            "было подтверждено 5 отмен. Это помогает сохранять свободные слоты доступными для всех клиентов. "
            "Спасибо за понимание."
        )
    if cancellations == 2:
        return cancellations, (
            "Внимание: это вторая отмена за последние 24 часа. После подтверждения каждой отмены "
            "действует пауза на 1 час. При пяти отменах за сутки создание и отмена записей будут "
            "ограничены на 24 часа. Пожалуйста, выбирайте время внимательно."
        )
    return cancellations, None


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


@router.post("/login")
async def login(request: Request, body: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Admin).where(Admin.username == body.username))
    admin = result.scalar_one_or_none()
    if not admin or not verify_password(body.password, admin.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    request.session["admin_username"] = admin.username
    request.session["password_changed_at"] = admin.password_changed_at.isoformat() if admin.password_changed_at else None
    await _audit(db, admin.username, "login", "Администратор вошёл в систему.")
    return {"ok": True}


@router.get("/check-session")
async def check_session(
    request: Request, response: Response, db: AsyncSession = Depends(get_db)
):
    """Проверка активной сессии с учётом смены пароля"""
    response.headers["Cache-Control"] = "no-store, max-age=0"
    username = request.session.get("admin_username")
    if not username:
        raise HTTPException(
            status_code=401, detail="Not authenticated",
            headers={"Cache-Control": "no-store, max-age=0"},
        )
    # Проверяем не изменился ли пароль с момента создания сессии
    result = await db.execute(select(Admin).where(Admin.username == username))
    admin = result.scalar_one_or_none()
    if not admin:
        request.session.clear()
        raise HTTPException(
            status_code=401, detail="Not authenticated",
            headers={"Cache-Control": "no-store, max-age=0"},
        )
    session_pca = request.session.get("password_changed_at")
    admin_pca = admin.password_changed_at.isoformat() if admin.password_changed_at else None
    if session_pca != admin_pca:
        request.session.clear()
        raise HTTPException(
            status_code=401, detail="Session expired due to password change",
            headers={"Cache-Control": "no-store, max-age=0"},
        )
    return {"ok": True, "username": username}


@router.post("/logout")
async def logout(request: Request, db: AsyncSession = Depends(get_db)):
    username = request.session.get("admin_username")
    if username:
        await _audit(db, username, "logout", "Администратор вышел из системы.")
    request.session.clear()
    return {"ok": True}


@router.post("/change-password")
async def change_password(
    request: Request, body: ChangePasswordRequest, db: AsyncSession = Depends(get_db)
):
    username = _get_admin_username(request)
    result = await db.execute(select(Admin).where(Admin.username == username))
    admin = result.scalar_one_or_none()
    if not admin or not verify_password(body.old_password, admin.password_hash):
        raise HTTPException(status_code=400, detail="Invalid old password")
    admin.password_hash = hash_password(body.new_password)
    admin.password_changed_at = datetime.now(KZ_TZ).replace(tzinfo=None)
    await db.commit()
    # Обновляем сессию текущего устройства, чтобы она осталась валидной
    request.session["password_changed_at"] = admin.password_changed_at.isoformat()
    await _audit(db, username, "change_password")
    return {"ok": True}


class InstructorCreate(BaseModel):
    name: str
    phone: Optional[str] = None
    telegram_id: Optional[str] = None
    telegram_username: Optional[str] = None
    transmission: str = "both"
    lesson_type: str = "both"
    gender: str = "any"
    experience_years: int = 0
    working_hours_start: str = "09:00"
    working_hours_end: str = "20:00"
    lunch_start: Optional[str] = None
    lunch_end: Optional[str] = None
    days_off: str = "Суббота,Воскресенье"
    description: Optional[str] = None
    avatar_url: Optional[str] = None
    is_duty: bool = False
    is_lead: bool = False


class InstructorUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    telegram_id: Optional[str] = None
    telegram_username: Optional[str] = None
    transmission: Optional[str] = None
    lesson_type: Optional[str] = None
    gender: Optional[str] = None
    experience_years: Optional[int] = None
    rating: Optional[float] = None
    working_hours_start: Optional[str] = None
    working_hours_end: Optional[str] = None
    lunch_start: Optional[str] = None
    lunch_end: Optional[str] = None
    days_off: Optional[str] = None
    description: Optional[str] = None
    avatar_url: Optional[str] = None
    is_duty: Optional[bool] = None
    is_active: Optional[bool] = None
    is_lead: Optional[bool] = None


def _normalize_telegram_username(value: Optional[str]) -> Optional[str]:
    """Store Telegram usernames without @ and convert blank values to NULL."""
    if value is None:
        return None
    value = value.strip().lstrip('@').strip()
    return value or None


@router.get("/instructors")
async def list_instructors(request: Request, db: AsyncSession = Depends(get_db)):
    _get_admin_username(request)
    result = await db.execute(select(Instructor).order_by(Instructor.name))
    instructors = result.scalars().all()
    return [
        {
            "id": i.id, "name": i.name, "phone": i.phone,
            "telegram_id": i.telegram_id,
            "telegram_username": i.telegram_username, "transmission": i.transmission,
            "lesson_type": i.lesson_type or "both",
            "gender": i.gender,
            "experience_years": i.experience_years or 0, "rating": i.rating if i.rating is not None else 5.0,
            "is_active": i.is_active is not False,
            "working_hours_start": str(i.working_hours_start),
            "working_hours_end": str(i.working_hours_end),
            "lunch_start": str(i.lunch_start) if i.lunch_start else None,
            "lunch_end": str(i.lunch_end) if i.lunch_end else None,
            "days_off": i.days_off,
            "description": i.description,
            "avatar_url": i.avatar_url,
            "is_duty": i.is_duty,
            "is_lead": i.is_lead,
        }
        for i in instructors
    ]


@router.post("/instructors")
async def create_instructor(
    request: Request, body: InstructorCreate, db: AsyncSession = Depends(get_db)
):
    username = _get_admin_username(request)
    operation_id = _idempotency_key(request)
    if operation_id:
        existing = (await db.execute(
            select(Instructor).where(Instructor.offline_operation_id == operation_id)
        )).scalar_one_or_none()
        if existing:
            return {"id": existing.id}
    normalized_phone = normalize_phone(body.phone) if body.phone else None
    telegram_username = _normalize_telegram_username(body.telegram_username)
    identity_conditions = []
    if normalized_phone:
        identity_conditions.append(Instructor.phone == normalized_phone)
    if body.telegram_id and body.telegram_id.strip():
        identity_conditions.append(Instructor.telegram_id == body.telegram_id.strip())
    if telegram_username:
        identity_conditions.append(Instructor.telegram_username == telegram_username)
    # Operations queued by the old frontend did not yet have an idempotency
    # key on their first request.  Match a reliable instructor contact once so
    # upgrading cannot add one final duplicate from that old queue item.
    if identity_conditions:
        existing = (await db.execute(
            select(Instructor).where(or_(*identity_conditions))
        )).scalars().first()
        if existing:
            if operation_id and not existing.offline_operation_id:
                existing.offline_operation_id = operation_id
                await db.commit()
            return {"id": existing.id}
    t_map = {"manual": "manual", "automatic": "automatic", "both": "both"}
    lesson_map = {"training": "training", "exam": "exam", "both": "both"}
    g_map = {"male": "male", "female": "female", "any": "any"}
    inst = Instructor(
        name=body.name,
        phone=normalized_phone,
        telegram_id=body.telegram_id.strip() if body.telegram_id else None,
        telegram_username=telegram_username,
        transmission=t_map.get(body.transmission.lower(), "both"),
        lesson_type=lesson_map.get(body.lesson_type.lower(), "both"),
        gender=body.gender.lower() if body.gender else "any",
        experience_years=body.experience_years,
        working_hours_start=time.fromisoformat(body.working_hours_start),
        working_hours_end=time.fromisoformat(body.working_hours_end),
        lunch_start=time.fromisoformat(body.lunch_start) if body.lunch_start else None,
        lunch_end=time.fromisoformat(body.lunch_end) if body.lunch_end else None,
        days_off=body.days_off,
        description=body.description,
        avatar_url=body.avatar_url,
        is_duty=body.is_duty,
        is_lead=body.is_lead,
        offline_operation_id=operation_id,
    )
    db.add(inst)
    try:
        await db.commit()
    except IntegrityError:
        # Two retry requests with the same key can reach different workers at
        # the same moment.  The database unique index is the final guard.
        await db.rollback()
        if operation_id:
            existing = (await db.execute(
                select(Instructor).where(Instructor.offline_operation_id == operation_id)
            )).scalar_one_or_none()
            if existing:
                return {"id": existing.id}
        raise
    await db.refresh(inst)
    # Если новый инструктор — дежурный, снимаем флаг у остальных
    if body.is_duty:
        await db.execute(
            update(Instructor).where(Instructor.id != inst.id).values(is_duty=False)
        )
        await db.commit()
    if body.is_lead:
        await db.execute(update(Instructor).where(Instructor.id != inst.id).values(is_lead=False))
        await db.commit()
    await _audit(db, username, "create_instructor", f"Администратор создал карточку инструктора «{inst.name}».")
    return {"id": inst.id}


@router.put("/instructors/{instructor_id}")
async def update_instructor(
    request: Request, instructor_id: int, body: InstructorUpdate, db: AsyncSession = Depends(get_db)
):
    username = _get_admin_username(request)
    result = await db.execute(select(Instructor).where(Instructor.id == instructor_id))
    inst = result.scalar_one_or_none()
    if not inst:
        raise HTTPException(status_code=404, detail="Instructor not found")
    t_map = {"manual": "manual", "automatic": "automatic", "both": "both"}
    lesson_map = {"training": "training", "exam": "exam", "both": "both"}
    g_map = {"male": "male", "female": "female", "any": "any"}
    try:
        proposed_work_start = (
            inst.working_hours_start if body.working_hours_start is None
            else time.fromisoformat(body.working_hours_start)
        )
        proposed_work_end = (
            inst.working_hours_end if body.working_hours_end is None
            else time.fromisoformat(body.working_hours_end)
        )
        proposed_lunch_start = (
            inst.lunch_start if body.lunch_start is None
            else time.fromisoformat(body.lunch_start) if body.lunch_start else None
        )
        proposed_lunch_end = (
            inst.lunch_end if body.lunch_end is None
            else time.fromisoformat(body.lunch_end) if body.lunch_end else None
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Некорректное время в графике инструктора")
    if not proposed_work_start or not proposed_work_end or proposed_work_start > proposed_work_end:
        raise HTTPException(status_code=400, detail="Время начала работы должно быть раньше времени окончания")
    if (
        proposed_lunch_start and proposed_lunch_end
        and proposed_lunch_start >= proposed_lunch_end
    ):
        raise HTTPException(status_code=400, detail="Время начала обеда должно быть раньше времени окончания")
    proposed_days_off = inst.days_off if body.days_off is None else body.days_off
    if any(value is not None for value in (
        body.working_hours_start, body.working_hours_end,
        body.lunch_start, body.lunch_end, body.days_off,
    )):
        await _ensure_default_schedule_preserves_active_bookings(
            db, inst,
            work_start=proposed_work_start,
            work_end=proposed_work_end,
            lunch_start=proposed_lunch_start,
            lunch_end=proposed_lunch_end,
            days_off=proposed_days_off,
        )
    if body.name is not None:
        inst.name = body.name
    if body.phone is not None:
        inst.phone = normalize_phone(body.phone)  # пустая строка → NULL
    if body.telegram_id is not None:
        inst.telegram_id = body.telegram_id or None
    if body.telegram_username is not None:
        inst.telegram_username = _normalize_telegram_username(body.telegram_username)
    if body.transmission is not None:
        inst.transmission = t_map.get(body.transmission.lower(), inst.transmission)
    if body.lesson_type is not None:
        inst.lesson_type = lesson_map.get(body.lesson_type.lower(), inst.lesson_type)
    if body.gender is not None:
        inst.gender = body.gender.lower()
    if body.experience_years is not None:
        inst.experience_years = body.experience_years
    if body.rating is not None:
        inst.rating = max(settings.MIN_RATING, body.rating)
    if body.working_hours_start is not None:
        inst.working_hours_start = proposed_work_start
    if body.working_hours_end is not None:
        inst.working_hours_end = proposed_work_end
    if body.lunch_start is not None:
        inst.lunch_start = proposed_lunch_start
    if body.lunch_end is not None:
        inst.lunch_end = proposed_lunch_end
    if body.days_off is not None:
        inst.days_off = body.days_off
    if body.description is not None:
        inst.description = body.description or None
    if body.avatar_url is not None:
        inst.avatar_url = body.avatar_url or None
    if body.is_duty is not None:
        inst.is_duty = body.is_duty
        # Дежурный инструктор может быть только один — снимаем флаг у остальных
        if body.is_duty:
            await db.execute(
                update(Instructor).where(Instructor.id != instructor_id).values(is_duty=False)
            )
    if body.is_active is not None:
        inst.is_active = body.is_active
    if body.is_lead is not None:
        inst.is_lead = body.is_lead
        if body.is_lead:
            await db.execute(update(Instructor).where(Instructor.id != instructor_id).values(is_lead=False))
    await db.commit()
    await _audit(db, username, "update_instructor", f"Администратор изменил карточку инструктора «{inst.name}».")
    return {"ok": True}


@router.delete("/instructors/{instructor_id}")
async def delete_instructor(request: Request, instructor_id: int, db: AsyncSession = Depends(get_db)):
    username = _get_admin_username(request)
    result = await db.execute(
        select(Instructor).where(Instructor.id == instructor_id).with_for_update()
    )
    inst = result.scalar_one_or_none()
    if not inst:
        raise HTTPException(status_code=404, detail="Instructor not found")
    name = inst.name
    active_appointments = await _get_active_instructor_appointments(db, instructor_id)
    if active_appointments:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=(
                f'Нельзя удалить инструктора «{name}»: есть активные записи — '
                f'{_schedule_conflict_detail(active_appointments)}. '
                "Сначала перенесите или отмените их."
            ),
        )
    await _detach_instructor_history(db, instructor_id)
    await db.delete(inst)
    await db.commit()
    await _audit(db, username, "delete_instructor", f"Администратор удалил инструктора «{name}».")
    return {"ok": True}


@router.get("/instructors/{instructor_id}/week-bookings")
async def get_instructor_week_bookings(
    request: Request,
    instructor_id: int,
    start_date: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    _get_admin_username(request)
    instructor = await db.get(Instructor, instructor_id)
    if not instructor:
        raise HTTPException(status_code=404, detail="Instructor not found")

    week_start = date.fromisoformat(start_date) if start_date else today_kz()
    week_end = week_start + timedelta(days=6)

    result = await db.execute(
        select(Booking)
        .options(selectinload(Booking.client))
        .where(
            and_(
                Booking.instructor_id == instructor_id,
                Booking.booking_date >= week_start,
                Booking.booking_date <= week_end,
                Booking.status.in_(["pending", "reschedule_pending", "planned", "confirmed", "in_progress"]),
            )
        )
        .order_by(Booking.booking_date, Booking.start_time)
    )
    bookings = result.scalars().all()

    mobile_result = await db.execute(
        select(MobileBooking)
        .options(selectinload(MobileBooking.user))
        .where(
            and_(
                MobileBooking.instructor_id == instructor_id,
                MobileBooking.booking_date >= week_start,
                MobileBooking.booking_date <= week_end,
                MobileBooking.status.in_(["pending", "planned", "confirmed"]),
            )
        )
        .order_by(MobileBooking.booking_date, MobileBooking.start_time)
    )
    mobile_bookings = mobile_result.scalars().all()

    days = {}
    for offset in range(7):
        current_day = week_start + timedelta(days=offset)
        days[str(current_day)] = []

    for booking in bookings:
        days[str(booking.booking_date)].append({
            "id": booking.id,
            "source": booking.source or "telegram",
            "client_name": booking.client.name if booking.client else "—",
            "client_phone": booking.client.phone if booking.client else "",
            "service_type": booking.service_type,
            "transmission": booking.transmission,
            "location": booking.location,
            "start_time": str(booking.start_time)[:5],
            "end_time": str(booking.end_time)[:5],
            "status": booking.status,
            "price": booking.price,
        })

    for booking in mobile_bookings:
        days[str(booking.booking_date)].append({
            "id": booking.id,
            "source": "mobile",
            "client_name": booking.user.name if booking.user else "—",
            "client_phone": booking.user.phone if booking.user else "",
            "service_type": booking.service_type,
            "transmission": booking.transmission,
            "location": booking.location,
            "start_time": str(booking.start_time)[:5],
            "end_time": str(booking.end_time)[:5] if booking.end_time else "",
            "status": booking.status,
            "price": booking.price,
        })

    for items in days.values():
        items.sort(key=lambda item: item["start_time"])

    return {
        "instructor": {"id": instructor.id, "name": instructor.name},
        "start_date": str(week_start),
        "end_date": str(week_end),
        "days": [{"date": day, "bookings": items} for day, items in days.items()],
    }


class InstructorDaysOffUpdate(BaseModel):
    days_off_dates: list[str]  # Список дат в формате "YYYY-MM-DD"


class InstructorDailyScheduleSave(BaseModel):
    schedule_date: str
    is_day_off: bool = False
    working_hours_start: Optional[str] = None
    working_hours_end: Optional[str] = None
    lunch_start: Optional[str] = None
    lunch_end: Optional[str] = None


@router.get("/instructors/{instructor_id}/days-off")
async def get_instructor_days_off(
    request: Request, instructor_id: int, db: AsyncSession = Depends(get_db)
):
    """Получить все выходные дни инструктора"""
    _get_admin_username(request)
    result = await db.execute(
        select(InstructorDayOff)
        .where(InstructorDayOff.instructor_id == instructor_id)
        .order_by(InstructorDayOff.day_off_date)
    )
    days_off = result.scalars().all()
    return [{"id": d.id, "date": str(d.day_off_date)} for d in days_off]


@router.put("/instructors/{instructor_id}/days-off")
async def update_instructor_days_off(
    request: Request,
    instructor_id: int,
    body: InstructorDaysOffUpdate,
    db: AsyncSession = Depends(get_db)
):
    """Обновить выходные дни инструктора (полная замена)"""
    username = _get_admin_username(request)
    
    # Проверяем что инструктор существует
    result = await db.execute(select(Instructor).where(Instructor.id == instructor_id))
    instructor = result.scalar_one_or_none()
    if not instructor:
        raise HTTPException(status_code=404, detail="Instructor not found")

    requested_dates = set()
    for date_str in body.days_off_dates:
        try:
            requested_dates.add(date.fromisoformat(date_str))
        except ValueError:
            continue
    await _ensure_days_off_have_no_active_bookings(db, instructor, requested_dates)

    # Удаляем все старые выходные
    from sqlalchemy import delete as sa_delete
    await db.execute(sa_delete(InstructorDayOff).where(InstructorDayOff.instructor_id == instructor_id))
    
    # Добавляем новые
    for date_str in body.days_off_dates:
        try:
            day_off_date = date.fromisoformat(date_str)
            day_off = InstructorDayOff(instructor_id=instructor_id, day_off_date=day_off_date)
            db.add(day_off)
        except ValueError:
            continue  # Пропускаем невалидные даты
    
    await db.commit()
    await _audit(db, username, "update_instructor_days_off", f"instructor_id={instructor_id}, dates={len(body.days_off_dates)}")
    return {"ok": True}


@router.get("/instructors/{instructor_id}/daily-schedules")
async def get_instructor_daily_schedules(
    request: Request, instructor_id: int, db: AsyncSession = Depends(get_db)
):
    _get_admin_username(request)
    result = await db.execute(
        select(InstructorDailySchedule)
        .where(InstructorDailySchedule.instructor_id == instructor_id)
        .order_by(InstructorDailySchedule.schedule_date)
    )
    schedules = result.scalars().all()
    return [
        {
            "id": s.id,
            "schedule_date": str(s.schedule_date),
            "is_day_off": s.is_day_off,
            "working_hours_start": str(s.working_hours_start) if s.working_hours_start else None,
            "working_hours_end": str(s.working_hours_end) if s.working_hours_end else None,
            "lunch_start": str(s.lunch_start) if s.lunch_start else None,
            "lunch_end": str(s.lunch_end) if s.lunch_end else None,
        }
        for s in schedules
    ]


@router.put("/instructors/{instructor_id}/daily-schedules")
async def save_instructor_daily_schedule(
    request: Request,
    instructor_id: int,
    body: InstructorDailyScheduleSave,
    db: AsyncSession = Depends(get_db),
):
    username = _get_admin_username(request)
    instructor = await db.get(Instructor, instructor_id)
    if not instructor:
        raise HTTPException(status_code=404, detail="Instructor not found")
    try:
        schedule_date = date.fromisoformat(body.schedule_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")
    try:
        proposed_work_start = time.fromisoformat(body.working_hours_start) if body.working_hours_start else None
        proposed_work_end = time.fromisoformat(body.working_hours_end) if body.working_hours_end else None
        proposed_lunch_start = time.fromisoformat(body.lunch_start) if body.lunch_start else None
        proposed_lunch_end = time.fromisoformat(body.lunch_end) if body.lunch_end else None
    except ValueError:
        raise HTTPException(status_code=400, detail="Некорректное время в расписании дня")
    effective_start = proposed_work_start or instructor.working_hours_start
    effective_end = proposed_work_end or instructor.working_hours_end
    if not body.is_day_off and (
        not effective_start or not effective_end or effective_start > effective_end
    ):
        raise HTTPException(status_code=400, detail="Время начала работы должно быть раньше времени окончания")
    if (
        proposed_lunch_start and proposed_lunch_end
        and proposed_lunch_start >= proposed_lunch_end
    ):
        raise HTTPException(status_code=400, detail="Время начала обеда должно быть раньше времени окончания")

    result = await db.execute(
        select(InstructorDailySchedule).where(
            and_(
                InstructorDailySchedule.instructor_id == instructor_id,
                InstructorDailySchedule.schedule_date == schedule_date,
            )
        )
    )
    schedule = result.scalar_one_or_none()
    await _ensure_daily_schedule_preserves_active_bookings(
        db, instructor, schedule_date,
        is_day_off=body.is_day_off,
        work_start=proposed_work_start,
        work_end=proposed_work_end,
        lunch_start=proposed_lunch_start,
        lunch_end=proposed_lunch_end,
    )
    if not schedule:
        schedule = InstructorDailySchedule(instructor_id=instructor_id, schedule_date=schedule_date)
        db.add(schedule)

    schedule.is_day_off = body.is_day_off
    schedule.working_hours_start = proposed_work_start
    schedule.working_hours_end = proposed_work_end
    schedule.lunch_start = proposed_lunch_start
    schedule.lunch_end = proposed_lunch_end
    await db.commit()
    await _audit(db, username, "update_instructor_daily_schedule", f"instructor_id={instructor_id}, date={body.schedule_date}")
    return {"ok": True}


@router.delete("/instructors/{instructor_id}/daily-schedules/{schedule_date}")
async def delete_instructor_daily_schedule(
    request: Request,
    instructor_id: int,
    schedule_date: str,
    db: AsyncSession = Depends(get_db),
):
    username = _get_admin_username(request)
    try:
        day = date.fromisoformat(schedule_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")
    instructor = await db.get(Instructor, instructor_id)
    if not instructor:
        raise HTTPException(status_code=404, detail="Instructor not found")
    schedule = (await db.execute(select(InstructorDailySchedule).where(and_(
        InstructorDailySchedule.instructor_id == instructor_id,
        InstructorDailySchedule.schedule_date == day,
    )))).scalar_one_or_none()
    if not schedule:
        return {"ok": True}
    await _ensure_daily_schedule_deletion_preserves_active_bookings(
        db, instructor, day
    )
    await db.delete(schedule)
    await db.commit()
    await _audit(db, username, "delete_instructor_daily_schedule", f"instructor_id={instructor_id}, date={schedule_date}")
    return {"ok": True}


@router.post("/instructors/{instructor_id}/days-off")
async def add_instructor_day_off(
    request: Request,
    instructor_id: int,
    day_off_date: str,
    db: AsyncSession = Depends(get_db)
):
    """Добавить один выходной день"""
    username = _get_admin_username(request)
    
    try:
        day_date = date.fromisoformat(day_off_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")
    
    # Проверяем что уже не существует
    result = await db.execute(
        select(InstructorDayOff).where(
            and_(
                InstructorDayOff.instructor_id == instructor_id,
                InstructorDayOff.day_off_date == day_date
            )
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        return {"ok": True, "message": "Already exists"}

    instructor = await db.get(Instructor, instructor_id)
    if not instructor:
        raise HTTPException(status_code=404, detail="Instructor not found")
    await _ensure_days_off_have_no_active_bookings(db, instructor, {day_date})
    
    day_off = InstructorDayOff(instructor_id=instructor_id, day_off_date=day_date)
    db.add(day_off)
    await db.commit()
    await _audit(db, username, "add_instructor_day_off", f"instructor_id={instructor_id}, date={day_off_date}")
    return {"ok": True}


@router.delete("/instructors/{instructor_id}/days-off/{day_off_id}")
async def delete_instructor_day_off(
    request: Request,
    instructor_id: int,
    day_off_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Удалить один выходной день"""
    username = _get_admin_username(request)
    
    result = await db.execute(
        select(InstructorDayOff).where(
            and_(
                InstructorDayOff.id == day_off_id,
                InstructorDayOff.instructor_id == instructor_id
            )
        )
    )
    day_off = result.scalar_one_or_none()
    if not day_off:
        raise HTTPException(status_code=404, detail="Day off not found")
    
    await db.delete(day_off)
    await db.commit()
    await _audit(db, username, "delete_instructor_day_off", f"instructor_id={instructor_id}, id={day_off_id}")
    return {"ok": True}


ARCHIVE_RETENTION_DAYS = 7
ARCHIVABLE_BOOKING_STATUSES = ("completed", "no_show")


async def archive_previous_day_logs(db: AsyncSession) -> int:
    """Move every non-current-day audit/event row into permanent history.

    The insert is flushed before source rows are deleted, so an unsuccessful
    archive pass rolls back without losing a log.  Row locks plus the unique
    source key make repeated passes and concurrent service instances safe.
    """
    cutoff = datetime.combine(today_kz(), time.min)
    older_than_today = lambda model: or_(
        model.created_at.is_(None), model.created_at < cutoff,
    )
    audit_rows = (await db.execute(
        select(AuditLog).where(older_than_today(AuditLog)).with_for_update()
    )).scalars().all()
    event_rows = (await db.execute(
        select(Event).where(older_than_today(Event)).with_for_update()
    )).scalars().all()
    if not audit_rows and not event_rows:
        return 0

    for item in audit_rows:
        db.add(ArchivedLog(
            source_type="audit", source_log_id=item.id,
            admin_username=item.admin_username, action=item.action,
            details=item.details, created_at=item.created_at,
        ))
    for item in event_rows:
        db.add(ArchivedLog(
            source_type="event", source_log_id=item.id,
            event_type=item.event_type, event_source=item.source,
            client_id=item.client_id, instructor_id=item.instructor_id,
            booking_id=item.booking_id, message=item.message,
            created_at=item.created_at,
        ))

    try:
        await db.flush()
        for item in audit_rows:
            await db.delete(item)
        for item in event_rows:
            await db.delete(item)
        await db.commit()
    except IntegrityError:
        # A second instance can finish the same pass first.  Its transaction
        # has the durable copies; this one must leave source rows untouched.
        await db.rollback()
        return 0
    return len(audit_rows) + len(event_rows)


def _requested_booking_statuses(status: Optional[str]) -> list[str]:
    return [item.strip() for item in (status or "").split(",") if item.strip()]


async def archive_due_completed_bookings(db: AsyncSession) -> int:
    """Persistently archive final bookings older than seven lesson days.

    The nullable marker keeps the shared booking row and every foreign-key
    relationship intact. Only after the marker is committed do ordinary
    completed-list queries exclude the row.
    """
    archive_before = today_kz() - timedelta(days=ARCHIVE_RETENTION_DAYS)
    result = await db.execute(
        update(Booking)
        .where(
            Booking.status.in_(ARCHIVABLE_BOOKING_STATUSES),
            Booking.booking_date < archive_before,
            Booking.archived_at.is_(None),
        )
        .values(archived_at=now_kz())
    )
    await db.commit()
    return int(result.rowcount or 0)


def _booking_filter(model, date_from, date_to, instructor_id, status, location):
    conditions = []
    if date_from:
        conditions.append(model.booking_date >= date.fromisoformat(date_from))
    if date_to:
        conditions.append(model.booking_date <= date.fromisoformat(date_to))
    if instructor_id:
        conditions.append(model.instructor_id == instructor_id)
    if status:
        statuses = _requested_booking_statuses(status)
        if len(statuses) == 1:
            conditions.append(model.status == statuses[0])
        elif len(statuses) > 1:
            conditions.append(model.status.in_(statuses))
        # The archive marker is the durable source of truth. A row disappears
        # from "Завершённые" only after archive_due_completed_bookings commits.
        if set(statuses).intersection(ARCHIVABLE_BOOKING_STATUSES):
            conditions.append(or_(
                model.status.notin_(ARCHIVABLE_BOOKING_STATUSES),
                model.archived_at.is_(None),
            ))
        if "cancelled" in statuses:
            conditions.append(or_(
                model.status != "cancelled",
                model.admin_confirmed.isnot(False),
            ))
    if location:
        conditions.append(model.location == location)
    return conditions


def _serialize_booking(booking: Booking) -> dict:
    """Keep every booking list on the same response format."""
    completion_at = booking.completed_at or booking.paid_at or booking.created_at
    return {
        "id": booking.id,
        "client_id": booking.client_id,
        "instructor_id": booking.instructor_id,
        "client_telegram_id": booking.client.telegram_id if booking.client else None,
        "source": booking.source or "telegram",
        "client_name": booking.client.name if booking.client else "",
        "client_phone": booking.client.phone if booking.client else "",
        "instructor_name": booking.instructor.name if booking.instructor else "",
        "service_type": booking.service_type,
        "transmission": booking.transmission,
        "location": booking.location,
        "date": str(booking.booking_date),
        "start_time": str(booking.start_time),
        "end_time": str(booking.end_time),
        "status": booking.status,
        "admin_confirmed": booking.admin_confirmed,
        "price": booking.price,
        "base_price": booking.base_price or booking.price,
        "certificate_amount": booking.certificate_amount or 0,
        "referral_discount_amount": booking.referral_discount_amount or 0,
        "payment_status": booking.payment_status,
        "package_id": booking.package_id,
        "certificate_id": booking.certificate_id,
        "created_at": booking.created_at.replace(tzinfo=KZ_TZ).isoformat() if booking.created_at else "",
        "booking_number": booking.booking_number,
        "completed_at": completion_at.replace(tzinfo=KZ_TZ).isoformat() if completion_at else None,
        "archived_at": booking.archived_at.replace(tzinfo=KZ_TZ).isoformat() if booking.archived_at else None,
        "requested_reschedule_date": booking.requested_reschedule_date.isoformat() if booking.requested_reschedule_date else None,
        "requested_reschedule_start_time": booking.requested_reschedule_start_time.strftime("%H:%M") if booking.requested_reschedule_start_time else None,
        "requested_reschedule_end_time": booking.requested_reschedule_end_time.strftime("%H:%M") if booking.requested_reschedule_end_time else None,
    }


async def _prioritize_admin_booking_over_pending(
    db: AsyncSession,
    booking_date: date,
    start_time: time,
    end_time: time,
    transmission: str,
    instructor_id: Optional[int],
    reason: str,
) -> list[Booking]:
    """Reserve an admin-entered slot ahead of unconfirmed online requests.

    Confirmed and in-progress lessons remain hard constraints. A pending online
    request is intentionally not allowed to block an offline/manual paper-log
    entry: it is preserved, but shown to the administrator as a conflict.
    """
    rows = (await db.execute(
        select(Booking)
        .options(selectinload(Booking.client))
        .where(
            Booking.booking_date == booking_date,
            Booking.status == "pending",
            Booking.start_time < end_time,
            Booking.end_time > start_time,
        )
        .with_for_update()
    )).scalars().all()

    conflicts = []
    for pending in rows:
        # A selected instructor cannot conduct two lessons at once, regardless
        # of the transmission. Manual transmission also has one shared car.
        same_instructor = instructor_id is not None and pending.instructor_id == instructor_id
        same_manual_car = transmission == "manual" and pending.transmission == "manual"
        # Without an explicit instructor, the administrator's entry still has
        # priority over temporary online holds for that time slot.
        priority_slot = instructor_id is None
        if not (same_instructor or same_manual_car or priority_slot):
            continue
        pending.status = "conflict"
        pending.conflict_reason = reason
        conflicts.append(pending)
    return conflicts


@router.get("/bookings")
async def list_bookings(
    request: Request,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    instructor_id: Optional[int] = None,
    status: Optional[str] = None,
    location: Optional[str] = None,
    page: Optional[int] = None,
    page_size: int = 15,
    db: AsyncSession = Depends(get_db),
):
    _get_admin_username(request)

    requested_statuses = _requested_booking_statuses(status)
    if set(requested_statuses).intersection(ARCHIVABLE_BOOKING_STATUSES):
        await archive_due_completed_bookings(db)

    items = []

    tg_query = select(Booking).options(selectinload(Booking.client), selectinload(Booking.instructor))
    tg_conditions = _booking_filter(Booking, date_from, date_to, instructor_id, status, location)
    if tg_conditions:
        tg_query = tg_query.where(and_(*tg_conditions))
    newest_first = bool(set(requested_statuses).intersection(ARCHIVABLE_BOOKING_STATUSES))
    if newest_first:
        tg_query = tg_query.order_by(Booking.booking_date.desc(), Booking.start_time.desc(), Booking.id.desc())
    else:
        tg_query = tg_query.order_by(Booking.booking_date, Booking.start_time, Booking.id)
    pagination = None
    if page is not None:
        safe_page_size = max(1, min(page_size, 100))
        total = (await db.execute(
            select(func.count()).select_from(Booking).where(and_(*tg_conditions))
            if tg_conditions else select(func.count()).select_from(Booking)
        )).scalar() or 0
        total_pages = max(1, (total + safe_page_size - 1) // safe_page_size)
        safe_page = max(1, min(page, total_pages))
        tg_query = tg_query.offset((safe_page - 1) * safe_page_size).limit(safe_page_size)
        pagination = {
            "page": safe_page,
            "page_size": safe_page_size,
            "total": total,
            "total_pages": total_pages,
        }
    tg_result = await db.execute(tg_query)
    for b in tg_result.scalars().all():
        items.append(_serialize_booking(b))

    items.sort(key=lambda x: (x["date"], x["start_time"], x["id"]), reverse=newest_first)
    return {"items": items, "pagination": pagination} if pagination else items


@router.get("/bookings/archive")
async def list_archived_bookings(request: Request, db: AsyncSession = Depends(get_db)):
    """Return the persistent archive, including rows moved before this request."""
    _get_admin_username(request)
    await archive_due_completed_bookings(db)
    result = await db.execute(
        select(Booking)
        .options(selectinload(Booking.client), selectinload(Booking.instructor))
        .where(and_(
            Booking.status.in_(ARCHIVABLE_BOOKING_STATUSES),
            Booking.archived_at.isnot(None),
        ))
        .order_by(Booking.booking_date.desc(), Booking.start_time.desc(), Booking.id.desc())
    )
    return [_serialize_booking(booking) for booking in result.scalars().all()]


class ManualBookingCreate(BaseModel):
    client_name: Optional[str] = None
    client_phone: Optional[str] = None
    instructor_id: Optional[int] = None
    service_type: str
    transmission: str
    booking_date: str
    start_time: str
    location: Optional[str] = None
    offline_operation_id: Optional[str] = None


@router.get("/booking-window")
async def get_manual_booking_window(request: Request, db: AsyncSession = Depends(get_db)):
    _get_admin_username(request)
    first_date, last_date = await _manual_booking_window(db)
    return {"min_date": first_date.isoformat(), "max_date": last_date.isoformat()}


@router.get("/offline-snapshot")
async def get_offline_snapshot(request: Request, db: AsyncSession = Depends(get_db)):
    """One coherent database snapshot for the offline administrator UI.

    Keeping this as one request is intentional: polling separate screens made
    the online admin compete with itself and left IndexedDB half-updated.
    """
    _get_admin_username(request)
    await archive_previous_day_logs(db)
    # PostgreSQL REPEATABLE READ guarantees that every tab payload and every
    # raw table below sees one coherent database state. Lesson transitions are
    # performed by the site backend scheduler, never by merely opening admin.
    if db.get_bind().dialect.name == "postgresql":
        await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
    # The recurring local copy intentionally excludes the historical tabs.
    # The normal online booking screens and their API remain unchanged.
    bookings = await list_bookings(request, status="planned,confirmed,in_progress,pending,cancellation_pending,reschedule_pending,conflict,disputed", db=db)
    instructors = await list_instructors(request, db=db)
    vehicles = await list_vehicles(request, db=db)
    clients = await list_clients(request, db=db)
    packages = await list_packages(request, db=db)
    certificates = await certificates_list(request, db=db)
    faq = await faq_list(request, db=db)
    notifications = await get_notifications(request, db=db)
    waiting_list = await get_waiting_list(request, db=db)
    audit = await audit_logs(request, db=db)
    dashboard_data = await dashboard(request, db=db)
    notification_counts = await get_notification_counts(request, response=Response(), db=db)
    analytics_heatmap = await heatmap(request, db=db)
    analytics_instructor_load = await instructor_load(request, db=db)
    analytics_booking_sources = await booking_sources(request, db=db)
    analytics_gender = await gender_breakdown(request, db=db)
    analytics_revenue = await revenue_analytics(request, db=db)
    certificate_requests = await get_certificate_requests(request, db=db)
    conflict_groups = await get_conflict_groups(request, db=db)
    schedules = (await db.execute(select(InstructorDailySchedule))).scalars().all()
    daily_schedules = [{
        "instructor_id": item.instructor_id, "schedule_date": item.schedule_date.isoformat(),
        "is_day_off": item.is_day_off,
        "working_hours_start": str(item.working_hours_start) if item.working_hours_start else None,
        "working_hours_end": str(item.working_hours_end) if item.working_hours_end else None,
        "lunch_start": str(item.lunch_start) if item.lunch_start else None,
        "lunch_end": str(item.lunch_end) if item.lunch_end else None,
    } for item in schedules]
    days_off = (await db.execute(select(InstructorDayOff))).scalars().all()
    instructor_days_off = [{"instructor_id": item.instructor_id, "day_off_date": item.day_off_date.isoformat()} for item in days_off]
    mobile_bookings = (await db.execute(
        select(MobileBooking)
        .options(selectinload(MobileBooking.user), selectinload(MobileBooking.instructor))
        .where(MobileBooking.status.notin_(("completed", "cancelled", "no_show")))
    )).scalars().all()
    offline_mobile_bookings = [{
        "id": f"mobile-{item.id}", "is_mobile": True,
        "client_id": item.user_id, "client_name": item.user.name if item.user else "—",
        "instructor_id": item.instructor_id,
        "instructor_name": item.instructor.name if item.instructor else "—",
        "service_type": item.service_type, "transmission": item.transmission,
        "location": item.location, "date": item.booking_date.isoformat(),
        "start_time": item.start_time.strftime("%H:%M"),
        "end_time": item.end_time.strftime("%H:%M") if item.end_time else None,
        "status": item.status,
    } for item in mobile_bookings]
    first_date, last_date = await _manual_booking_window(db)
    return {
        "version": 12,
        "synced_at": now_kz().isoformat(),
        "booking_window": {"min_date": first_date.isoformat(), "max_date": last_date.isoformat()},
        "slot_rules": {
            "location": settings.LOCATION_EXAM,
            "capacity": settings.MAX_CARS_EXAM_LOCATION,
            "training_duration_minutes": settings.TRAINING_DURATION_MINUTES,
            "exam_duration_minutes": settings.EXAM_DURATION_MINUTES,
        },
        "data": {
            "/bookings": bookings, "/instructors": instructors, "/vehicles": vehicles, "/clients": clients,
            "/packages": packages, "/certificates": certificates, "/faq": faq,
            "/notifications": notifications, "/waiting-list": waiting_list,
            "/audit-logs": audit,
            "/instructor-daily-schedules": daily_schedules, "/instructor-days-off": instructor_days_off,
            "/offline-mobile-bookings": offline_mobile_bookings,
            "/dashboard": dashboard_data, "/notification-counts": notification_counts,
            "/analytics/heatmap": analytics_heatmap, "/analytics/instructor-load": analytics_instructor_load,
            "/analytics/booking-sources": analytics_booking_sources,
            "/analytics/gender": analytics_gender,
            "/analytics/revenue": analytics_revenue,
            "/certificate-requests": certificate_requests, "/bookings/conflicts": conflict_groups,
        },
    }


@router.get("/offline-slots-snapshot")
async def get_offline_slots_snapshot(request: Request, db: AsyncSession = Depends(get_db)):
    """Retired: slot calculation now runs only against IndexedDB offline data."""
    _get_admin_username(request)
    raise HTTPException(
        status_code=410,
        detail="Используйте /offline-snapshot: слоты рассчитываются в локальной копии админки",
    )


@router.get("/slots")
async def get_slots(
    request: Request,
    booking_date: str,
    service_type: str = "training",
    transmission: str = "automatic",
    instructor_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    _get_admin_username(request)
    from app.models.models import ServiceType as ST, TransmissionType as TT
    from datetime import time as dtime, timedelta

    s_map = {"training": ST.TRAINING, "training_30": ST.TRAINING, "exam": ST.EXAM}
    t_map = {"manual": TT.MANUAL, "automatic": TT.AUTOMATIC, "both": TT.BOTH}
    service = s_map.get(service_type, ST.TRAINING)
    trans = t_map.get(transmission, TT.AUTOMATIC)
    location = settings.LOCATION_EXAM
    duration = settings.TRAINING_DURATION_MINUTES if service == ST.TRAINING else settings.EXAM_DURATION_MINUTES

    target_date = date.fromisoformat(booking_date)
    await _ensure_manual_booking_date_in_window(db, target_date)
    instructors_result = await db.execute(select(Instructor).where(Instructor.is_active == True))
    instructors = [
        item for item in instructors_result.scalars().all()
        if teaches_service(item, service.value)
    ]
    if instructor_id is not None:
        instructors = [i for i in instructors if i.id == instructor_id]
    schedules = []
    for instructor in instructors:
        schedule = await get_effective_schedule(db, instructor, target_date)
        if schedule:
            schedules.append(schedule)
    if not schedules:
        return {"date": booking_date, "location": location, "slots": [], "instructor_id": instructor_id}

    start_hour = min(s[0].hour for s in schedules)
    end_hour = min(max(s[1].hour for s in schedules), 21)

    # Получаем текущее время в часовом поясе Павлодара
    current_kz = datetime.now(KZ_TZ)
    today_kz = current_kz.date()
    current_time_kz = current_kz.time()

    # Если выбран сегодняшний день и последний слот уже недоступен
    if target_date == today_kz and (current_time_kz.hour > end_hour or (current_time_kz.hour == end_hour and current_time_kz.minute >= 1)):
        return {"date": booking_date, "location": location, "slots": []}

    query = select(Booking).options(selectinload(Booking.instructor), selectinload(Booking.client)).where(
        and_(
            Booking.booking_date == target_date,
            Booking.status.in_(["pending", "cancellation_pending", "reschedule_pending", "planned", "confirmed", "in_progress"]),
        )
    )
    
    if instructor_id is not None:
        # Показываем только записи выбранного инструктора
        query = query.where(Booking.instructor_id == instructor_id)
    
    result = await db.execute(query)
    bookings = result.scalars().all()

    mobile_query = select(MobileBooking).options(selectinload(MobileBooking.user)).where(
        and_(
            MobileBooking.booking_date == target_date,
            MobileBooking.status.in_(["pending", "planned", "confirmed"]),
        )
    )
    if instructor_id is not None:
        mobile_query = mobile_query.where(MobileBooking.instructor_id == instructor_id)
    mobile_result = await db.execute(mobile_query)
    mobile_bookings = mobile_result.scalars().all()

    # Строим слоты
    slots = []
    current_minutes = start_hour * 60
    last_start_minutes = end_hour * 60

    while current_minutes <= last_start_minutes:
        slot_end = current_minutes + duration
        slot_time = dtime(current_minutes // 60, current_minutes % 60)
        slot_end_time = dtime(slot_end // 60, slot_end % 60)

        # Если это сегодняшний день, пропускаем слоты которые уже прошли
        if target_date == today_kz and slot_time <= current_time_kz:
            current_minutes += duration
            continue

        # Найти занятые записи на этот слот
        slot_bookings = []
        for b in bookings:
            b_start = b.start_time.hour * 60 + b.start_time.minute
            b_end = b.end_time.hour * 60 + b.end_time.minute
            if b_start < slot_end and b_end > current_minutes:
                slot_bookings.append({
                    "client": b.client.name if b.client else "—",
                    "instructor": b.instructor.name if b.instructor else "—",
                    "instructor_id": b.instructor_id,
                    "status": b.status,
                })
        for b in mobile_bookings:
            b_start = b.start_time.hour * 60 + b.start_time.minute
            b_end_time = b.end_time or slot_end_time
            b_end = b_end_time.hour * 60 + b_end_time.minute
            if b_start < slot_end and b_end > current_minutes:
                instructor_name = "—"
                if b.instructor_id:
                    inst = next((i for i in instructors if i.id == b.instructor_id), None)
                    instructor_name = inst.name if inst else f"ID {b.instructor_id}"
                slot_bookings.append({
                    "client": b.user.name if b.user else "—",
                    "instructor": instructor_name,
                    "instructor_id": b.instructor_id,
                    "status": b.status,
                })

        busy_ids = await get_busy_instructor_ids(db, target_date, slot_time, slot_end_time)
        booked_at_location = await count_booked_at_location(db, target_date, slot_time, slot_end_time, location)
        available_instructors_count = 0
        if instructor_id is not None:
            instructor = instructors[0] if instructors else None
            has_free_instructor = bool(instructor) and await is_instructor_available(
                db, instructor, target_date, slot_time, slot_end_time, trans.value if hasattr(trans, "value") else str(trans), busy_ids, allow_duty=True, service_type=service.value
            )
            available_instructors_count = 1 if has_free_instructor else 0
        else:
            has_free_instructor = False
            for instructor in instructors:
                if await is_instructor_available(
                    db, instructor, target_date, slot_time, slot_end_time, trans.value if hasattr(trans, "value") else str(trans), busy_ids, service_type=service.value
                ):
                    available_instructors_count += 1
                    has_free_instructor = True
            if available_instructors_count == 0:
                for instructor in instructors:
                    if await is_instructor_available(
                        db, instructor, target_date, slot_time, slot_end_time, trans.value if hasattr(trans, "value") else str(trans), busy_ids, allow_duty=True, service_type=service.value
                    ):
                        available_instructors_count = 1
                        has_free_instructor = True
                        break
        is_free = (
            has_free_instructor
            and await slot_has_capacity(
                db, target_date, slot_time, slot_end_time, location,
                trans.value if hasattr(trans, "value") else str(trans),
            )
        )
        slots.append({
            "time": slot_time.strftime("%H:%M"),
            "end_time": slot_end_time.strftime("%H:%M"),
            "bookings": slot_bookings,
            "is_free": is_free,
            "booked_count": booked_at_location,
            "capacity": settings.MAX_CARS_EXAM_LOCATION,
            "available_instructors_count": available_instructors_count,
        })
        current_minutes += duration

    return {"date": booking_date, "location": location, "slots": slots, "instructor_id": instructor_id}


@router.post("/bookings/manual")
async def create_manual_booking(
    request: Request, body: ManualBookingCreate, db: AsyncSession = Depends(get_db)
):
    username = _get_admin_username(request)
    operation_id = _idempotency_key(request) or body.offline_operation_id
    if operation_id:
        existing = (await db.execute(
            select(Booking).where(Booking.offline_operation_id == operation_id)
        )).scalar_one_or_none()
        if existing:
            return {"ok": True, "booking_id": existing.id, "client_id": existing.client_id}
    t_map = {"manual": "manual", "automatic": "automatic"}
    s_map = {"training": ServiceType.TRAINING, "training_30": ServiceType.TRAINING, "exam": ServiceType.EXAM}

    service = s_map.get(body.service_type, ServiceType.TRAINING)
    transmission = t_map.get(body.transmission, "automatic")
    booking_date = date.fromisoformat(body.booking_date)
    await _ensure_manual_booking_date_in_window(db, booking_date)
    st = time.fromisoformat(body.start_time)
    
    # Проверяем что запись не на прошедшее время
    # Do not shadow the module-level now_kz() helper used below when creating
    # the confirmed booking timestamp.
    current_kz = datetime.now(KZ_TZ)
    today_kz = current_kz.date()
    current_time_kz = current_kz.time()
    
    # Если выбрано время которое уже прошло сегодня
    if booking_date == today_kz and st <= current_time_kz:
        raise HTTPException(status_code=400, detail="Выбранное время уже прошло")
    
    duration = settings.TRAINING_DURATION_MINUTES if service == ServiceType.TRAINING else settings.EXAM_DURATION_MINUTES
    et_delta = timedelta(hours=st.hour, minutes=st.minute) + timedelta(minutes=duration)
    et = time(int(et_delta.total_seconds() // 3600), int((et_delta.total_seconds() % 3600) // 60))

    # Paper-log/manual entries have priority over online applications that
    # are still waiting for approval. Keep those applications for the admin
    # in the conflict tab instead of rejecting the manual record.
    pending_conflicts = await _prioritize_admin_booking_over_pending(
        db,
        booking_date,
        st,
        et,
        transmission,
        body.instructor_id,
        "Конфликт с ручной записью администратора",
    )

    # Площадка: берём из запроса или определяем по типу услуги
    location = settings.LOCATION_EXAM

    # Определяем цену в зависимости от типа услуги и площадки
    price = settings.PRICE_TRAINING_NEW if service == ServiceType.TRAINING else settings.PRICE_EXAM

    if not await slot_has_capacity(db, booking_date, st, et, location, transmission):
        raise HTTPException(status_code=400, detail="На это время нет свободного места на площадке")

    instructor = None
    if body.instructor_id:
        instructor = await db.get(Instructor, body.instructor_id)
        if not instructor:
            raise HTTPException(status_code=404, detail="Instructor not found")
        
        # ПРОВЕРЯЕМ: занят ли инструктор в это время (есть ли уже запись)
        existing_booking_result = await db.execute(
            select(Booking).where(
                and_(
                    Booking.instructor_id == instructor.id,
                    Booking.booking_date == booking_date,
                    Booking.start_time < et,
                    Booking.end_time > st,
                    Booking.status.in_(["pending", "cancellation_pending", "reschedule_pending", "planned", "confirmed", "in_progress"])
                )
            )
        )
        existing_booking = existing_booking_result.scalars().first()
        if existing_booking:
            raise HTTPException(
                status_code=400, 
                detail=f"Инструктор {instructor.name} уже занят в это время (запись #{existing_booking.id})"
            )
        mobile_conflict_result = await db.execute(
            select(MobileBooking).where(
                and_(
                    MobileBooking.instructor_id == instructor.id,
                    MobileBooking.booking_date == booking_date,
                    MobileBooking.start_time < et,
                    MobileBooking.end_time > st,
                    MobileBooking.status.in_(["pending", "planned", "confirmed", "in_progress"]),
                )
            )
        )
        if mobile_conflict_result.scalars().first():
            raise HTTPException(status_code=400, detail=f"Инструктор {instructor.name} уже занят в это время")
        
        busy_ids = await get_busy_instructor_ids(db, booking_date, st, et)
        if not await is_instructor_available(db, instructor, booking_date, st, et, transmission, busy_ids, service_type=service.value):
            raise HTTPException(status_code=400, detail="Инструктор не ведёт этот тип урока, занят или не работает в выбранное время")
    else:
        instructor = await find_best_instructor(db, booking_date, st, et, transmission, service.value)
        if not instructor:
            raise HTTPException(status_code=400, detail="Нет свободного инструктора для выбранного времени")

    vehicle = await reserve_available_vehicle(db, booking_date, st, et, transmission)
    if not vehicle:
        raise HTTPException(status_code=409, detail="Подходящая машина уже занята в это время")

    # Найти или создать клиента
    normalized_client_phone = normalize_phone(body.client_phone) if body.client_phone else None
    client = (
        await find_client_by_phone(
            db, normalized_client_phone, include_deleted=True, for_update=True,
        ) if normalized_client_phone else None
    )
    client_query = (
        select(Client).where(
            Client.name == body.client_name,
            Client.is_deleted == False,
        ) if body.client_name else select(Client).where(False)
    )
    if not client and not normalized_client_phone:
        client_result = await db.execute(client_query.with_for_update())
        client = client_result.scalar_one_or_none()
    if client and client.is_deleted:
        await reactivate_deleted_client(
            db,
            client,
            name=body.client_name or client.name,
            phone=normalized_client_phone or client.phone,
            password_hash=None,
        )
    if not client:
        import secrets as _secrets
        client = Client(
            name=body.client_name or body.client_phone or "Клиент без имени",
            phone=normalized_client_phone,
            referral_code=_secrets.token_hex(4).upper(),
        )
        db.add(client)
        await db.flush()

    daily_limit_result = await db.execute(
        select(func.count()).select_from(Booking).where(
            and_(
                Booking.client_id == client.id,
                Booking.booking_date == booking_date,
            Booking.status.in_(["pending", "cancellation_pending", "reschedule_pending", "planned", "confirmed"]),
            )
        )
    )
    daily_count = daily_limit_result.scalar() or 0
    if daily_count >= 2:
        raise HTTPException(status_code=400, detail="Максимум 2 записи на один день для одного клиента")

    # The next eligible manual booking consumes one package entitlement just
    # like bookings created in the mobile app or client Telegram bot.
    package_eligibility = (
        ClientPackage.remaining_sessions > 0 if service == ServiceType.TRAINING
        else (ClientPackage.remaining_sessions <= 0) & (ClientPackage.remaining_bonus_exams > 0)
    )
    package_purchase = (await db.execute(
        select(ClientPackage).options(selectinload(ClientPackage.package)).where(
            ClientPackage.client_id == client.id,
            ClientPackage.is_active == True,
            package_eligibility,
            (ClientPackage.expires_at.is_(None)) | (ClientPackage.expires_at >= now_kz()),
        ).order_by(ClientPackage.expires_at).with_for_update()
    )).scalars().first()
    package_bonus_exam_used = False
    if package_purchase:
        if service == ServiceType.TRAINING:
            package_purchase.remaining_sessions -= 1
        else:
            package_purchase.remaining_bonus_exams -= 1
            package_bonus_exam_used = True
        if package_purchase.remaining_sessions == 0 and package_purchase.remaining_bonus_exams == 0:
            package_purchase.is_active = False

    package_paid = package_purchase is not None
    package_progress = None
    if package_purchase and package_purchase.package:
        total = max(0, package_purchase.package.sessions_count or 0)
        remaining = max(0, package_purchase.remaining_sessions or 0)
        package_progress = (max(0, total - remaining), total)
    package_counter = (
        f" — {package_progress[0]}/{package_progress[1]} занятий"
        if package_progress else ""
    )
    final_price = 0 if package_paid else price
    referral_discount_amount = 0
    if not package_paid and client.referral_discount_available:
        existing_referral_booking = (await db.execute(
            select(func.count()).select_from(Booking).where(
                Booking.client_id == client.id,
                Booking.referral_discount_amount > 0,
                Booking.status.in_(["pending", "reschedule_pending", "planned", "confirmed", "in_progress", "completed"]),
            )
        )).scalar() or 0
        referral_eligible = not client.referred_by_client_id
        if client.referred_by_client_id:
            referral_eligible = bool((await db.execute(
                select(func.count()).select_from(Booking).where(
                    Booking.client_id == client.referred_by_client_id,
                    Booking.status == "completed",
                )
            )).scalar() or 0)
        if referral_eligible and not existing_referral_booking:
            referral_discount_amount = min(1000, price)
            final_price -= referral_discount_amount

    booking = Booking(
        client_id=client.id,
        instructor_id=instructor.id,
        vehicle_id=vehicle.id,
        service_type=service,
        transmission=transmission,
        location=location,
        booking_date=booking_date,
        start_time=st,
        end_time=et,
        status="confirmed",
        price=final_price,
        base_price=price,
        referral_discount_amount=referral_discount_amount,
        payment_status="paid" if package_paid else "unpaid",
        paid_amount=price if package_paid else 0,
        paid_at=now_kz() if package_paid else None,
        source="manual",
        package_id=package_purchase.package_id if package_purchase else None,
        package_bonus_exam_used=package_bonus_exam_used,
        admin_confirmed=True,
        admin_confirmed_at=now_kz(),
        offline_operation_id=operation_id,
    )
    db.add(booking)
    try:
        await db.flush()
        booking.booking_number = await _generate_booking_number(db)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        if operation_id:
            existing = (await db.execute(select(Booking).where(
                Booking.offline_operation_id == operation_id
            ))).scalar_one_or_none()
            if existing:
                return {"ok": True, "booking_id": existing.id, "client_id": existing.client_id}
        raise HTTPException(status_code=409, detail="Это время уже занято. Обновите список и выберите другое.")
    await db.refresh(booking)
    await _audit(db, username, "new_booking", f"Ручная запись #{booking.booking_number}: {body.client_name} на {body.booking_date} {body.start_time}")
    if pending_conflicts:
        clients = ", ".join(
            f"{item.client.name if item.client else 'Клиент'} — {item.start_time.strftime('%H:%M')}"
            for item in pending_conflicts
        )
        await _audit(
            db,
            username,
            "manual_booking_conflict",
            f"Ручная запись #{booking.booking_number} имеет приоритет; онлайн-заявки перенесены в конфликты: {clients}",
        )

    # Уведомление инструктору в Telegram
    import logging as _logging
    import httpx as _httpx
    _logger = _logging.getLogger(__name__)
    if instructor and instructor.telegram_id and settings.INSTRUCTOR_BOT_TOKEN:
        trans_label = "Механика" if body.transmission == "manual" else "Автомат"
        _RU_DAYS = ["в понедельник", "во вторник", "в среду", "в четверг", "в пятницу", "в субботу", "в воскресенье"]
        _RU_DAYS_SHORT = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
        _today = datetime.now(KZ_TZ).date()
        _delta = (booking_date - _today).days
        if _delta <= 7:
            if _delta == 0:
                _day_phrase = "сегодня"
            elif _delta == 1:
                _day_phrase = "завтра"
            elif _delta == 2:
                _day_phrase = "послезавтра"
            else:
                _day_phrase = _RU_DAYS[booking_date.weekday()]
            _header = f"{instructor.name}, у вас {_day_phrase} запись:"
            _date_line = f"📅 {body.booking_date} в {body.start_time}"
        else:
            _header = f"{instructor.name}, у вас запись:"
            _date_line = f"📅 {body.booking_date} ({_RU_DAYS_SHORT[booking_date.weekday()]}) в {body.start_time}"
        instr_text = (
            f"📌 Новая запись!\n"
            f"{_header}\n\n"
            f"{_date_line}\n"
            f"Клиент: {body.client_name}\n"
            f"Площадка: {location}\n"
            f"Коробка: {trans_label}\n"
            + ("📦 ОПЛАЧЕНО ПАКЕТОМ — деньги НЕ брать!" if package_paid
               else (f"🎁 Скидка по реферальному коду: {referral_discount_amount} ₸\n💰 К оплате: {final_price} ₸"
                     if referral_discount_amount else f"💰 К оплате: {final_price} ₸"))
        )
        try:
            async with _httpx.AsyncClient(timeout=10) as _client:
                resp = await _client.post(
                    f"https://api.telegram.org/bot{settings.INSTRUCTOR_BOT_TOKEN}/sendMessage",
                    json={"chat_id": instructor.telegram_id.strip(), "text": instr_text},
                )
        except Exception as e:
            _logger.error(f"[notify] Telegram error: {e}")

    if package_paid and client.telegram_id and settings.BOT_TOKEN:
        try:
            async with _httpx.AsyncClient(timeout=10) as _client:
                await _client.post(
                    f"https://api.telegram.org/bot{settings.BOT_TOKEN}/sendMessage",
                    json={
                        "chat_id": client.telegram_id.strip(),
                        "text": (
                            f"✅ Вы записаны на {booking_date.strftime('%d.%m.%Y')} в {st.strftime('%H:%M')}.\n\n"
                            f"📦 Запись оплачена пакетом{package_counter}. "
                            "Оплачивать инструктору ничего не нужно."
                        ),
                    },
                )
        except Exception as e:
            _logger.error(f"[notify manual package] Telegram error: {e}")

    return {"ok": True, "booking_id": booking.id, "client_id": client.id}


async def _purge_bookings(db: AsyncSession, bookings: list[Booking]) -> int:
    """Delete booking rows using the same dependent-record cleanup everywhere."""
    booking_ids = [booking.id for booking in bookings]
    if not booking_ids:
        return 0
    await db.execute(sa_delete(RatingRecord).where(RatingRecord.booking_id.in_(booking_ids)))
    for booking in bookings:
        await db.delete(booking)
    return len(booking_ids)


@router.delete("/bookings/cancelled")
async def purge_cancelled_bookings(request: Request, db: AsyncSession = Depends(get_db)):
    """Remove only records that are actually displayed on the cancelled tab."""
    username = _get_admin_username(request)
    result = await db.execute(
        select(Booking)
        .where(and_(
            Booking.status == "cancelled",
            or_(Booking.admin_confirmed.is_(None), Booking.admin_confirmed.is_(True)),
        ))
        .with_for_update()
    )
    deleted_count = await _purge_bookings(db, result.scalars().all())
    await db.commit()
    await _audit(db, username, "purge_cancelled_bookings", f"count={deleted_count}")
    return {"ok": True, "deleted": deleted_count}


@router.delete("/bookings/{booking_id}")
async def delete_booking(request: Request, booking_id: int, db: AsyncSession = Depends(get_db)):
    username = _get_admin_username(request)
    result = await db.execute(select(Booking).options(selectinload(Booking.instructor)).where(Booking.id == booking_id))
    booking = result.scalar_one_or_none()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    await _purge_bookings(db, [booking])
    await db.commit()
    await _audit(db, username, "delete_booking", f"id={booking_id}")
    return {"ok": True}


class UpdateBookingStatus(BaseModel):
    status: str

@router.put("/bookings/{booking_id}/status")
async def update_booking_status(
    request: Request, booking_id: int, body: UpdateBookingStatus, db: AsyncSession = Depends(get_db)
):
    username = _get_admin_username(request)
    result = await db.execute(select(Booking).where(Booking.id == booking_id))
    booking = result.scalar_one_or_none()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    valid_statuses = ['planned', 'confirmed', 'completed', 'cancelled', 'no_show', 'cancellation_pending', 'reschedule_pending']
    if body.status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {', '.join(valid_statuses)}")
    # A client cancellation is reversible until the admin makes the final
    # decision.  The normal status endpoint is the single confirmation point.
    is_client_cancellation_confirmation = (
        booking.status == "cancellation_pending" and body.status == "cancelled"
    )
    if booking.status == "cancellation_pending" and body.status in ("confirmed", "planned"):
        booking.status = booking.cancellation_previous_status or "confirmed"
        booking.cancellation_previous_status = None
    else:
        booking.status = body.status
        if body.status != "cancellation_pending":
            booking.cancellation_previous_status = None
    if booking.status == "completed":
        if booking.completed_at is None:
            booking.completed_at = now_kz()
    else:
        booking.completed_at = None
    booking.archived_at = None
    await db.commit()
    await _audit(db, username, "update_booking_status", f"id={booking_id}, status={body.status}")

    # При отмене — дополнительное событие + уведомление инструктору
    if body.status == "cancelled":
        await _restore_booking_package_if_needed(db, booking)
        client_result = await db.execute(select(Client).where(Client.id == booking.client_id))
        client = client_result.scalar_one_or_none()
        client_name = client.name if client else "Клиент"
        client_id_details = f" (id={client.id})" if client else ""
        await _audit(
            db,
            username,
            "booking_cancelled",
            f"Запись #{booking_id} отменена, клиент: {client_name}{client_id_details}, "
            f"дата: {booking.booking_date} {booking.start_time}"
            f"{' source=client_cancellation' if is_client_cancellation_confirmation else ''}",
        )
        cancellation_warning = None
        if is_client_cancellation_confirmation and client is not None:
            await _add_support_notice(
                db, client.id,
                "Ваша заявка на отмену подтверждена администратором. Запись отменена.",
            )
            _, cancellation_warning = await _apply_cancellation_limit(db, client)
            if cancellation_warning:
                await _add_support_notice(db, client.id, cancellation_warning)
            await db.commit()

        instructor_result = await db.execute(select(Instructor).where(Instructor.id == booking.instructor_id))
        instructor = instructor_result.scalar_one_or_none()
        print(f"[notify cancel] instructor={instructor.name if instructor else None}, tg_id={instructor.telegram_id if instructor else None}, token={bool(settings.INSTRUCTOR_BOT_TOKEN)}")
        if instructor and instructor.telegram_id and settings.INSTRUCTOR_BOT_TOKEN:
            try:
                import httpx as _httpx
                import logging as _logging
                _logger = _logging.getLogger(__name__)
                trans_label = "Механика" if booking.transmission == "manual" else "Автомат"
                instr_text = (
                    f"❌ Запись отменена!\n\n"
                    f"{instructor.name}, запись #{booking_id} отменена администратором.\n\n"
                    f"📅 {booking.booking_date.strftime('%d.%m.%Y')} в {booking.start_time.strftime('%H:%M')}\n"
                    f"👤 Клиент: {client_name}\n"
                    f"📍 {booking.location}\n"
                    f"Коробка: {trans_label}"
                )
                print(f"[notify cancel] Sending to {instructor.name} tg_id={instructor.telegram_id}")
                async with _httpx.AsyncClient(timeout=10) as _client_http:
                    resp = await _client_http.post(
                        f"https://api.telegram.org/bot{settings.INSTRUCTOR_BOT_TOKEN}/sendMessage",
                        json={"chat_id": instructor.telegram_id.strip(), "text": instr_text},
                    )
                    print(f"[notify cancel] Telegram response: {resp.status_code} {resp.text[:200]}")
            except Exception as e:
                print(f"[notify cancel] ERROR: {e}")
                _logger.error(f"[notify cancel] Telegram error: {e}")

        # The request only becomes a cancellation here, so notify the client
        # after the administrator has confirmed it.
        if client and client.telegram_id and settings.BOT_TOKEN:
            try:
                async with httpx.AsyncClient(timeout=10) as tg_client:
                    await tg_client.post(
                        f"https://api.telegram.org/bot{settings.BOT_TOKEN}/sendMessage",
                        json={"chat_id": client.telegram_id.strip(), "text": "❌ Ваша отмена записи подтверждена администратором."},
                    )
            except Exception:
                pass
        if client:
            await send_push_to_user(client.id, "Запись отменена", "Ваша отмена записи подтверждена администратором.",
                                    {"type": "booking_cancelled", "booking_id": booking.id})

        if cancellation_warning:
            warning_text = f"<b>Здравствуйте, {escape(client.name)}!</b>\n\n{cancellation_warning}"
            if client.telegram_id and settings.BOT_TOKEN:
                try:
                    async with httpx.AsyncClient(timeout=10) as tg_client:
                        await tg_client.post(
                            f"https://api.telegram.org/bot{settings.BOT_TOKEN}/sendMessage",
                            json={
                                "chat_id": client.telegram_id.strip(),
                                "text": warning_text,
                                "parse_mode": "HTML",
                            },
                        )
                except Exception:
                    pass
            await send_push_to_user(
                client.id,
                "Внимание: частые отмены",
                cancellation_warning,
                {"type": "cancellation_warning", "booking_id": booking.id},
            )

    return {"ok": True}


# ==================== АВТОПАРК ====================

class VehiclePayload(BaseModel):
    name: str
    transmission: str


class VehicleRepairPayload(BaseModel):
    is_under_repair: bool


def _vehicle_payload(vehicle: Vehicle) -> dict:
    return {
        "id": vehicle.id,
        "name": vehicle.name,
        "transmission": vehicle.transmission,
        "is_under_repair": vehicle.is_under_repair,
    }


def _validated_vehicle_payload(body: VehiclePayload) -> tuple[str, str]:
    name = body.name.strip()
    transmission = body.transmission.strip().lower()
    if not name:
        raise HTTPException(status_code=400, detail="Укажите название машины")
    if len(name) > 100:
        raise HTTPException(status_code=400, detail="Название машины не должно быть длиннее 100 символов")
    if transmission not in ("manual", "automatic"):
        raise HTTPException(status_code=400, detail="Для машины укажите МКПП или АКПП")
    return name, transmission


@router.get("/vehicles")
async def list_vehicles(request: Request, db: AsyncSession = Depends(get_db)):
    _get_admin_username(request)
    vehicles = (await db.execute(select(Vehicle).order_by(Vehicle.id))).scalars().all()
    return [_vehicle_payload(vehicle) for vehicle in vehicles]


@router.post("/vehicles", status_code=201)
async def create_vehicle(
    request: Request, body: VehiclePayload, db: AsyncSession = Depends(get_db)
):
    username = _get_admin_username(request)
    name, transmission = _validated_vehicle_payload(body)
    count = (await db.execute(select(func.count()).select_from(Vehicle))).scalar() or 0
    if count >= 6:
        raise HTTPException(status_code=400, detail="В автопарке может быть не больше 6 машин")
    vehicle = Vehicle(name=name, transmission=transmission)
    db.add(vehicle)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Машина с таким названием уже есть в автопарке")
    await db.refresh(vehicle)
    await _audit(db, username, "create_vehicle", f"Добавлена {vehicle.name} ({vehicle.transmission})")
    return _vehicle_payload(vehicle)


@router.put("/vehicles/{vehicle_id}")
async def update_vehicle(
    request: Request, vehicle_id: int, body: VehiclePayload, db: AsyncSession = Depends(get_db)
):
    username = _get_admin_username(request)
    name, transmission = _validated_vehicle_payload(body)
    vehicle = (await db.execute(
        select(Vehicle).where(Vehicle.id == vehicle_id).with_for_update()
    )).scalar_one_or_none()
    if not vehicle:
        raise HTTPException(status_code=404, detail="Машина не найдена")
    if transmission != vehicle.transmission:
        incompatible_booking = (await db.execute(
            select(Booking.id).where(
                Booking.vehicle_id == vehicle.id,
                Booking.transmission != transmission,
                Booking.status.in_(ACTIVE_BOOKING_STATUSES),
            ).limit(1)
        )).scalar_one_or_none()
        if incompatible_booking is not None:
            raise HTTPException(
                status_code=409,
                detail=(f"Нельзя сменить КПП: у машины есть активная несовместимая "
                        f"запись #{incompatible_booking}"),
            )
    vehicle.name = name
    vehicle.transmission = transmission
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Машина с таким названием уже есть в автопарке")
    await _audit(db, username, "update_vehicle", f"Изменена {vehicle.name} ({vehicle.transmission})")
    return _vehicle_payload(vehicle)


@router.put("/vehicles/{vehicle_id}/repair")
async def update_vehicle_repair_status(
    request: Request,
    vehicle_id: int,
    body: VehicleRepairPayload,
    db: AsyncSession = Depends(get_db),
):
    """Mark a car as unavailable for all new booking channels, or return it to service."""
    username = _get_admin_username(request)
    vehicle = (await db.execute(
        select(Vehicle).where(Vehicle.id == vehicle_id).with_for_update()
    )).scalar_one_or_none()
    if not vehicle:
        raise HTTPException(status_code=404, detail="Машина не найдена")

    vehicle.is_under_repair = body.is_under_repair
    await db.commit()
    state = "поставлена на ремонт" if vehicle.is_under_repair else "возвращена в строй"
    await _audit(db, username, "update_vehicle_repair_status", f"{vehicle.name}: {state}")
    return _vehicle_payload(vehicle)


@router.delete("/vehicles/{vehicle_id}")
async def delete_vehicle(request: Request, vehicle_id: int, db: AsyncSession = Depends(get_db)):
    username = _get_admin_username(request)
    vehicle = (await db.execute(
        select(Vehicle).where(Vehicle.id == vehicle_id).with_for_update()
    )).scalar_one_or_none()
    if not vehicle:
        raise HTTPException(status_code=404, detail="Машина не найдена")
    active_booking = (await db.execute(
        select(Booking.id).where(
            Booking.vehicle_id == vehicle.id,
            Booking.status.in_(ACTIVE_BOOKING_STATUSES),
        ).limit(1)
    )).scalar_one_or_none()
    if active_booking is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Нельзя удалить машину: к ней привязана активная запись #{active_booking}",
        )
    name = vehicle.name
    await db.delete(vehicle)
    await db.commit()
    await _audit(db, username, "delete_vehicle", f"Удалена {name}")
    return {"ok": True}


class RescheduleResolveRequest(BaseModel):
    action: str  # "confirm" | "reject"


@router.post("/bookings/{booking_id}/reschedule-request/resolve")
async def resolve_reschedule_request(
    request: Request,
    booking_id: int,
    body: RescheduleResolveRequest,
    db: AsyncSession = Depends(get_db),
):
    """Подтверждает или отклоняет заявку клиента на перенос, не меняя запись заранее."""
    username = _get_admin_username(request)
    booking = (await db.execute(
        select(Booking).options(selectinload(Booking.client), selectinload(Booking.instructor))
        .where(Booking.id == booking_id)
    )).scalar_one_or_none()
    if not booking or booking.status != "reschedule_pending":
        raise HTTPException(status_code=404, detail="Заявка на перенос не найдена")
    if body.action not in ("confirm", "reject"):
        raise HTTPException(status_code=400, detail="Некорректное действие")

    client = booking.client
    if body.action == "reject":
        booking.status = booking.reschedule_previous_status or "confirmed"
        booking.reschedule_previous_status = None
        booking.requested_reschedule_date = None
        booking.requested_reschedule_start_time = None
        booking.requested_reschedule_end_time = None
        booking.reschedule_requested_at = None
        await db.commit()
        await _audit(db, username, "booking_reschedule_rejected", f"Заявка на перенос записи #{booking_id} отклонена")
        message = "К сожалению, запрошенное время для переноса уже недоступно. Ваша текущая запись сохранена без изменений."
        if client and client.telegram_id and settings.BOT_TOKEN:
            try:
                async with httpx.AsyncClient(timeout=10) as tg_client:
                    await tg_client.post(
                        f"https://api.telegram.org/bot{settings.BOT_TOKEN}/sendMessage",
                        json={"chat_id": client.telegram_id.strip(), "text": f"❌ {message}"},
                    )
            except Exception:
                pass
        if client:
            await send_push_to_user(client.id, "Перенос не подтверждён", message,
                                    {"type": "booking_reschedule_rejected", "booking_id": booking.id})
        return {"ok": True}

    new_date = booking.requested_reschedule_date
    new_start = booking.requested_reschedule_start_time
    new_end = booking.requested_reschedule_end_time
    if not new_date or not new_start or not new_end:
        raise HTTPException(status_code=400, detail="В заявке не указано новое время")
    instructor = booking.instructor or await db.get(Instructor, booking.instructor_id)
    if not instructor or not await is_instructor_available(
        db, instructor, new_date, new_start, new_end, booking.transmission,
        None,
        service_type=booking.service_type.value if hasattr(booking.service_type, "value") else str(booking.service_type),
        preserve_existing_assignment=True,
    ):
        raise HTTPException(status_code=409, detail="Инструктор больше не свободен в запрошенное время")
    conflict = (await db.execute(select(Booking).where(and_(
        Booking.id != booking.id,
        Booking.instructor_id == booking.instructor_id,
        Booking.booking_date == new_date,
        Booking.status.in_(["pending", "reschedule_pending", "planned", "confirmed", "in_progress", "cancellation_pending"]),
        Booking.start_time < new_end,
        Booking.end_time > new_start,
    )))).scalars().first()
    mobile_conflict = (await db.execute(select(MobileBooking).where(and_(
        MobileBooking.instructor_id == booking.instructor_id,
        MobileBooking.booking_date == new_date,
        MobileBooking.status.in_(ACTIVE_MOBILE_BOOKING_STATUSES),
        MobileBooking.start_time < new_end,
        MobileBooking.end_time > new_start,
    )))).scalars().first()
    has_capacity = await slot_has_capacity(
        db, new_date, new_start, new_end, booking.location,
        booking.transmission,
        exclude_booking_id=booking.id,
    )
    if conflict or mobile_conflict or not has_capacity:
        raise HTTPException(status_code=409, detail="Запрошенный слот уже занят. Попросите клиента выбрать другое время")

    vehicle = await reserve_available_vehicle(
        db, new_date, new_start, new_end, booking.transmission,
        exclude_booking_id=booking.id,
    )
    if not vehicle:
        raise HTTPException(status_code=409, detail="Подходящая машина уже занята в это время")

    old_date, old_start, old_instructor = booking.booking_date, booking.start_time, instructor
    booking.booking_date = new_date
    booking.start_time = new_start
    booking.end_time = new_end
    booking.vehicle_id = vehicle.id
    booking.status = "confirmed"
    booking.reschedule_previous_status = None
    booking.requested_reschedule_date = None
    booking.requested_reschedule_start_time = None
    booking.requested_reschedule_end_time = None
    booking.reschedule_requested_at = None
    booking.confirmation_sent = False
    booking.confirmed_by_client = False
    booking.reminder_10min_sent = False
    booking.reminder_1h_sent = False
    booking.reminder_24h_sent = False
    await db.commit()
    await _audit(
        db, username, "booking_rescheduled",
        f"Запись #{booking_id} перенесена с {old_date} {old_start} на {new_date} {new_start}",
    )
    db.add(Event(
        event_type="booking_rescheduled", source="admin", client_id=client.id if client else None,
        instructor_id=booking.instructor_id, booking_id=booking.id,
        message=f"Администратор подтвердил перенос записи на {new_date.strftime('%d.%m.%Y')} {new_start.strftime('%H:%M')}",
    ))
    await db.commit()

    service_label = "Обучение вождению" if booking.service_type == "training" else "Пробный экзамен"
    trans_label = "Механика" if booking.transmission == "manual" else "Автомат"
    client_message = (
        "✅ Ваша заявка на перенос подтверждена!\n\n"
        f"📋 Номер записи: {booking.booking_number or '—'}\n"
        f"📅 {new_date.strftime('%d.%m.%Y')} в {new_start.strftime('%H:%M')}\n"
        f"📍 {booking.location}\n🚗 {service_label} ({trans_label})\n"
        f"👨‍🏫 Инструктор: {instructor.name}"
    )
    if client and client.telegram_id and settings.BOT_TOKEN:
        try:
            async with httpx.AsyncClient(timeout=10) as tg_client:
                await tg_client.post(
                    f"https://api.telegram.org/bot{settings.BOT_TOKEN}/sendMessage",
                    json={"chat_id": client.telegram_id.strip(), "text": client_message},
                )
        except Exception:
            pass
    if client:
        await send_push_to_user(client.id, "Запись перенесена", client_message,
                                {"type": "booking_rescheduled", "booking_id": booking.id})
    if old_instructor.telegram_id and settings.INSTRUCTOR_BOT_TOKEN:
        try:
            instructor_message = (
                "🔄 Администратор подтвердил перенос записи.\n\n"
                f"Клиент: {client.name if client else '—'}\n"
                f"Было: {old_date.strftime('%d.%m.%Y')} в {old_start.strftime('%H:%M')}\n"
                f"Стало: {new_date.strftime('%d.%m.%Y')} в {new_start.strftime('%H:%M')}\n"
                f"📍 {booking.location}\n🚗 {service_label} ({trans_label})"
            )
            async with httpx.AsyncClient(timeout=10) as tg_client:
                await tg_client.post(
                    f"https://api.telegram.org/bot{settings.INSTRUCTOR_BOT_TOKEN}/sendMessage",
                    json={"chat_id": old_instructor.telegram_id.strip(), "text": instructor_message},
                )
        except Exception:
            pass
    return {"ok": True}


async def _generate_booking_number(db: AsyncSession) -> str:
    if db.get_bind().dialect.name == "postgresql":
        # Serialise number allocation until the surrounding booking
        # transaction commits. This removes the MAX()+1 race.
        await db.execute(text("SELECT pg_advisory_xact_lock(2026082402)"))
    result = await db.execute(
        select(func.max(Booking.booking_number)).where(Booking.booking_number.isnot(None))
    )
    max_num = result.scalar()
    if max_num is None:
        return "000000"
    try:
        next_num = int(max_num) + 1
    except (ValueError, TypeError):
        next_num = 0
    return f"{next_num:06d}"


class ConfirmBookingRequest(BaseModel):
    action: str
    rejection_reason: Optional[str] = None


@router.post("/bookings/{booking_id}/confirm")
async def confirm_booking(
    request: Request, booking_id: int, body: ConfirmBookingRequest, db: AsyncSession = Depends(get_db)
):
    username = _get_admin_username(request)
    result = await db.execute(select(Booking).options(selectinload(Booking.client), selectinload(Booking.instructor)).where(Booking.id == booking_id))
    booking = result.scalar_one_or_none()
    if not booking:
        raise HTTPException(status_code=404, detail="Запись не найдена")

    if body.action == "confirm":
        if booking.status == "confirmed" and booking.admin_confirmed:
            return {"ok": True, "booking_number": booking.booking_number}
        if booking.status not in ("pending", "conflict", "disputed"):
            raise HTTPException(status_code=400, detail="Эта запись не ожидает подтверждения")
        conflict_result = await db.execute(
            select(Booking).where(
                and_(
                    Booking.instructor_id == booking.instructor_id,
                    Booking.booking_date == booking.booking_date,
                    Booking.start_time < booking.end_time,
                    Booking.end_time > booking.start_time,
                    Booking.id != booking.id,
                    Booking.status.in_(["planned", "confirmed", "in_progress"]),
                )
            )
        )
        mobile_conflict_result = await db.execute(
            select(MobileBooking).where(and_(
                MobileBooking.instructor_id == booking.instructor_id,
                MobileBooking.booking_date == booking.booking_date,
                MobileBooking.start_time < booking.end_time,
                MobileBooking.end_time > booking.start_time,
                MobileBooking.status.in_(ACTIVE_MOBILE_BOOKING_STATUSES),
            ))
        )
        if (
            conflict_result.scalars().first()
            or mobile_conflict_result.scalars().first()
        ):
            raise HTTPException(status_code=400, detail="Этот слот уже занят другой подтвержденной записью")
        # Compatibility was checked when the application received its
        # instructor. Later edits to that card must not invalidate this row.
        booking.status = "confirmed"
        booking.admin_confirmed = True
        booking.admin_confirmed_at = now_kz()
        booking.conflict_reason = None
        booking.booking_number = await _generate_booking_number(db)
        await db.commit()
        await _audit(db, username, "booking_confirmed", f"Заявка #{booking_id} подтверждена, номер {booking.booking_number}")
        client = booking.client
        # Событие для вкладки "События" (подтверждение заявки клиента)
        db.add(Event(
            event_type="booking_confirmed",
            source="admin",
            client_id=client.id if client else None,
            booking_id=booking.id,
            message=f"Запись #{booking.booking_number} подтверждена администратором",
        ))
        await db.commit()
        
        # Уведомление для Telegram
        if client and client.telegram_id and settings.BOT_TOKEN:
            try:
                trans_label = "Механика" if booking.transmission == "manual" else "Автомат"
                service_label = "Обучение вождению" if booking.service_type == "training" else "Пробный экзамен"
                msg = (
                    f"✅ <b>Ваша заявка подтверждена!</b>\n\n"
                    f"📋 Номер записи: <b>{booking.booking_number}</b>\n\n"
                    f"📍 {booking.location}\n"
                    f"📅 {booking.booking_date.strftime('%d.%m.%Y')}\n"
                    f"🕐 {booking.start_time.strftime('%H:%M')}\n"
                    f"🚗 {service_label} ({trans_label})\n"
                    f"👨‍🏫 Инструктор: {booking.instructor.name if booking.instructor else 'Не назначен'}\n\n"
                    f"Мы напомним вам за час до начала занятия."
                )
                async with httpx.AsyncClient(timeout=10) as tg_client:
                    await _send_confirmed_booking_messages(
                        tg_client, client.telegram_id.strip(), msg
                    )
            except Exception as e:
                print(f"[confirm notify] ERROR: {e}")
        
        # Уведомление для мобильного приложения
        if client and booking.source == "mobile":
            try:
                trans_label = "Механика" if booking.transmission == "manual" else "Автомат"
                service_label = "Обучение вождению" if booking.service_type == "training" else "Пробный экзамен"
                push_body = (
                    f"📋 Номер: {booking.booking_number}\n"
                    f"📍 {booking.location}\n"
                    f"📅 {booking.booking_date.strftime('%d.%m.%Y')} 🕐 {booking.start_time.strftime('%H:%M')}\n"
                    f"🚗 {service_label} ({trans_label})"
                )
                await send_push_to_user(
                    user_id=client.id,
                    title="✅ Ваша заявка подтверждена!",
                    body=push_body,
                    data={"type": "booking_confirmed", "booking_id": booking.id}
                )
            except Exception as e:
                print(f"[confirm push notify] ERROR: {e}")
                
        return {"ok": True, "booking_number": booking.booking_number}

    elif body.action == "reject":
        if booking.status == "cancelled":
            return {"ok": True}
        if booking.status not in ("pending", "conflict", "disputed"):
            raise HTTPException(status_code=400, detail="Эта запись не ожидает рассмотрения")
        rejection_reason = (body.rejection_reason or "").strip()
        if not rejection_reason:
            raise HTTPException(status_code=400, detail="Укажите причину отклонения для клиента")
        await _restore_booking_package_if_needed(db, booking)
        booking.status = "cancelled"
        booking.conflict_reason = None
        await db.commit()
        await _audit(
            db, username, "booking_rejected",
            f"Заявка #{booking_id} отклонена. Причина: {rejection_reason}",
        )
        client = booking.client

        # Событие для вкладки "События" (отклонение заявки клиента)
        db.add(Event(
            event_type="booking_rejected",
            source="admin",
            client_id=client.id if client else None,
            booking_id=booking.id,
            message=(
                f"Запись клиента {client.name if client else '—'} отклонена администратором. "
                f"Причина: {rejection_reason}"
            ),
        ))
        if client:
            await _add_support_notice(
                db,
                client.id,
                f"Ваша запись отменена администратором. Причина: {rejection_reason}",
            )
        await db.commit()
        
        # Уведомление для Telegram
        if client and client.telegram_id and settings.BOT_TOKEN:
            try:
                msg = (
                    f"❌ <b>Ваша заявка отклонена.</b>\n\n"
                    f"Причина: {rejection_reason}\n\n"
                    f"Пожалуйста, выберите другое время через «Записаться»."
                )
                async with httpx.AsyncClient(timeout=10) as tg_client:
                    await tg_client.post(
                        f"https://api.telegram.org/bot{settings.BOT_TOKEN}/sendMessage",
                        json={"chat_id": client.telegram_id.strip(), "text": msg, "parse_mode": "HTML"},
                    )
            except Exception as e:
                print(f"[reject notify] ERROR: {e}")
        
        # Уведомление для мобильного приложения
        if client and booking.source == "mobile":
            try:
                await send_push_to_user(
                    user_id=client.id,
                    title="❌ Ваша заявка отклонена",
                    body=f"Причина: {rejection_reason}",
                    data={"type": "booking_rejected", "booking_id": booking.id}
                )
            except Exception as e:
                print(f"[reject push notify] ERROR: {e}")
                
        return {"ok": True}

    else:
        raise HTTPException(status_code=400, detail="Некорректное действие")


@router.get("/bookings/pending")
async def get_pending_bookings(request: Request, db: AsyncSession = Depends(get_db)):
    _get_admin_username(request)
    result = await db.execute(
        select(Booking)
        .options(selectinload(Booking.client), selectinload(Booking.instructor))
        .where(Booking.status.in_(["pending", "conflict", "disputed"]))
        .order_by(Booking.created_at.asc())
    )
    bookings = result.scalars().all()
    items = []
    for b in bookings:
        items.append({
            "id": b.id,
            "client_name": b.client.name if b.client else "—",
            "client_phone": b.client.phone if b.client else "—",
            "instructor_name": b.instructor.name if b.instructor else "—",
            "booking_date": b.booking_date.isoformat(),
            "start_time": b.start_time.strftime("%H:%M"),
            "end_time": b.end_time.strftime("%H:%M"),
            "service_type": b.service_type,
            "transmission": b.transmission,
            "location": b.location,
            "status": b.status,
            "source": b.source,
            "conflict_reason": b.conflict_reason,
            "created_at": b.created_at.isoformat() if b.created_at else None,
        })
    return {"items": items}


@router.delete("/mobile-bookings/{booking_id}")
async def delete_mobile_booking(request: Request, booking_id: int, db: AsyncSession = Depends(get_db)):
    username = _get_admin_username(request)
    result = await db.execute(select(MobileBooking).where(MobileBooking.id == booking_id))
    booking = result.scalar_one_or_none()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    await db.delete(booking)
    await db.commit()
    await _audit(db, username, "delete_mobile_booking", f"id={booking_id}")
    return {"ok": True}


class ReassignBooking(BaseModel):
    new_date: Optional[str] = None
    new_start_time: Optional[str] = None
    new_instructor_id: Optional[int] = None


@router.put("/bookings/{booking_id}/reassign")
async def reassign_booking(
    request: Request, booking_id: int, body: ReassignBooking, db: AsyncSession = Depends(get_db)
):
    username = _get_admin_username(request)
    result = await db.execute(select(Booking).where(Booking.id == booking_id))
    booking = result.scalar_one_or_none()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    # Сохраняем старые значения для уведомления
    old_date = booking.booking_date
    old_time = booking.start_time.strftime("%H:%M")
    old_instructor_id = booking.instructor_id

    if body.new_date:
        booking.booking_date = date.fromisoformat(body.new_date)
    if body.new_start_time:
        st = time.fromisoformat(body.new_start_time)
        booking.start_time = st
        duration = settings.TRAINING_DURATION_MINUTES if booking.service_type == ServiceType.TRAINING else settings.EXAM_DURATION_MINUTES
        et = timedelta(hours=st.hour, minutes=st.minute) + timedelta(minutes=duration)
        booking.end_time = time(int(et.total_seconds() // 3600), int((et.total_seconds() % 3600) // 60))
    if body.new_instructor_id:
        booking.instructor_id = body.new_instructor_id

    if body.new_date or body.new_start_time or body.new_instructor_id:
        with db.no_autoflush:
            target_instructor = await db.get(Instructor, booking.instructor_id)
            keeps_original_assignment = booking.instructor_id == old_instructor_id
            service_value = (
                booking.service_type.value
                if hasattr(booking.service_type, "value") else str(booking.service_type)
            )
            target_is_available = bool(target_instructor) and await is_instructor_available(
                db, target_instructor, booking.booking_date, booking.start_time,
                booking.end_time, booking.transmission, None, allow_duty=True,
                service_type=service_value,
                preserve_existing_assignment=keeps_original_assignment,
            )
        if not target_instructor:
            raise HTTPException(status_code=404, detail="Новый инструктор не найден")
        if not target_is_available:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Инструктор недоступен по актуальному графику. При выборе нового "
                    "инструктора также должны совпадать тип урока и КПП."
                ),
            )

    # Проверяем конфликты у нового инструктора
    conflict_result = await db.execute(
        select(Booking).where(
            and_(
                Booking.instructor_id == booking.instructor_id,
                Booking.booking_date == booking.booking_date,
                Booking.id != booking.id,
            Booking.status.in_(["pending", "cancellation_pending", "reschedule_pending", "planned", "confirmed", "in_progress"]),
                Booking.start_time < booking.end_time,
                Booking.end_time > booking.start_time,
            )
        )
    )
    conflict = conflict_result.scalars().first()
    if conflict:
        raise HTTPException(status_code=409, detail="Инструктор занят в это время")

    mobile_conflict_result = await db.execute(
        select(MobileBooking).where(
            and_(
                MobileBooking.instructor_id == booking.instructor_id,
                MobileBooking.booking_date == booking.booking_date,
                MobileBooking.status.in_(["pending", "planned", "confirmed", "in_progress"]),
                MobileBooking.start_time < booking.end_time,
                MobileBooking.end_time > booking.start_time,
            )
        )
    )
    if mobile_conflict_result.scalars().first():
        raise HTTPException(status_code=409, detail="Инструктор занят в это время")

    vehicle = await reserve_available_vehicle(
        db, booking.booking_date, booking.start_time, booking.end_time,
        booking.transmission, exclude_booking_id=booking.id,
    )
    if not vehicle:
        raise HTTPException(status_code=409, detail="Подходящая машина уже занята в это время")
    booking.vehicle_id = vehicle.id

    # Сбрасываем флаги напоминаний если дата/время изменились
    if body.new_date or body.new_start_time:
        booking.reminder_10min_sent = False
        booking.reminder_1h_sent = False
        booking.reminder_24h_sent = False
        booking.confirmation_sent = False
        booking.confirmed_by_client = False
        booking.status = "planned"

    await db.commit()

    # Детальный audit log
    changes = []
    if body.new_date:
        changes.append(f"дата: {old_date} -> {body.new_date}")
    if body.new_start_time:
        changes.append(f"время: {old_time} -> {body.new_start_time}")
    if body.new_instructor_id:
        changes.append(f"инструктор: {old_instructor_id} -> {body.new_instructor_id}")
    audit_detail = f"id={booking_id}, " + ", ".join(changes) if changes else f"id={booking_id}"
    await _audit(db, username, "reassign_booking", audit_detail)

    import logging as _logging
    import httpx as _httpx
    _logger = _logging.getLogger(__name__)

    # Уведомление инструктору
    target_instructor_id = booking.instructor_id
    instructor_changed = body.new_instructor_id and body.new_instructor_id != old_instructor_id

    instr_result = await db.execute(select(Instructor).where(Instructor.id == target_instructor_id))
    instructor = instr_result.scalar_one_or_none()
    client_result = await db.execute(select(Client).where(Client.id == booking.client_id))
    client = client_result.scalar_one_or_none()

    print(f"[notify reassign] instructor={instructor.name if instructor else None}, tg_id={instructor.telegram_id if instructor else None}, token={bool(settings.INSTRUCTOR_BOT_TOKEN)}, changed={instructor_changed}")
    if instructor and instructor.telegram_id and settings.INSTRUCTOR_BOT_TOKEN:
        _RU_DAYS = ["в понедельник", "во вторник", "в среду", "в четверг", "в пятницу", "в субботу", "в воскресенье"]
        _RU_DAYS_SHORT = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
        _today = datetime.now(KZ_TZ).date()
        _bdate = booking.booking_date
        _delta = (_bdate - _today).days
        if _delta <= 7:
            if _delta == 0:
                _day_phrase = "сегодня"
            elif _delta == 1:
                _day_phrase = "завтра"
            elif _delta == 2:
                _day_phrase = "послезавтра"
            else:
                _day_phrase = _RU_DAYS[_bdate.weekday()]
            _date_with_day = f"{_bdate.strftime('%d.%m.%Y')} в {booking.start_time.strftime('%H:%M')}"
        else:
            _day_phrase = None
            _date_with_day = f"{_bdate.strftime('%d.%m.%Y')} ({_RU_DAYS_SHORT[_bdate.weekday()]}) в {booking.start_time.strftime('%H:%M')}"
        trans_label = "Механика" if booking.transmission == "manual" else "Автомат"
        client_name = client.name if client else "Клиент"

        if instructor_changed:
            _new_header = f"{instructor.name}, у вас запись:" if _day_phrase is None else f"{instructor.name}, у вас {_day_phrase} запись:"
            instr_text = (
                f"📌 Новая запись!\n"
                f"{_new_header}\n\n"
                f"📅 {_date_with_day}\n"
                f"👤 Клиент: {client_name}\n"
                f"📍 {booking.location}\n"
                f"Коробка: {trans_label}\n"
                f"💰 К оплате: {booking.price} ₸"
            )
        else:
            _date_changed = f"{_bdate.strftime('%d.%m.%Y')} в {booking.start_time.strftime('%H:%M')}" if _day_phrase else f"{_bdate.strftime('%d.%m.%Y')} ({_RU_DAYS_SHORT[_bdate.weekday()]}) в {booking.start_time.strftime('%H:%M')}"
            instr_text = (
                f"🔄 Запись изменена!\n\n"
                f"{instructor.name}, администратор изменил запись.\n\n"
                f"Было: {old_date.strftime('%d.%m.%Y')} в {old_time}\n"
                f"Стало: 📅 {_date_changed}\n\n"
                f"👤 Клиент: {client_name}\n"
                f"📍 {booking.location}\n"
                f"Коробка: {trans_label}\n"
                f"💰 К оплате: {booking.price} ₸"
            )
        try:
            print(f"[notify reassign] Sending to {instructor.name} tg_id={instructor.telegram_id}, changed={instructor_changed}")
            async with _httpx.AsyncClient(timeout=10) as _client_http:
                resp = await _client_http.post(
                    f"https://api.telegram.org/bot{settings.INSTRUCTOR_BOT_TOKEN}/sendMessage",
                    json={"chat_id": instructor.telegram_id.strip(), "text": instr_text},
                )
                print(f"[notify reassign] Telegram response: {resp.status_code} {resp.text[:200]}")
        except Exception as e:
            print(f"[notify reassign] ERROR: {e}")
            _logger.error(f"[notify reassign] Telegram error: {e}")

    return {"ok": True}


class EditBookingRequest(BaseModel):
    new_date: Optional[str] = None
    new_start_time: Optional[str] = None
    new_instructor_id: Optional[int] = None
    new_transmission: Optional[str] = None
    new_location: Optional[str] = None


@router.put("/bookings/{booking_id}/edit")
async def edit_booking(
    request: Request, booking_id: int, body: EditBookingRequest, db: AsyncSession = Depends(get_db)
):
    """Полное редактирование записи с проверкой доступности инструктора."""
    username = _get_admin_username(request)
    result = await db.execute(select(Booking).where(Booking.id == booking_id))
    booking = result.scalar_one_or_none()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    old_instructor_id = booking.instructor_id
    old_transmission = booking.transmission
    old_date = booking.booking_date
    old_time = booking.start_time.strftime("%H:%M")

    # Применяем изменения к полям
    if body.new_date:
        booking.booking_date = date.fromisoformat(body.new_date)
    if body.new_start_time:
        st = time.fromisoformat(body.new_start_time)
        booking.start_time = st
        duration = settings.TRAINING_DURATION_MINUTES if booking.service_type == ServiceType.TRAINING else settings.EXAM_DURATION_MINUTES
        et = timedelta(hours=st.hour, minutes=st.minute) + timedelta(minutes=duration)
        booking.end_time = time(int(et.total_seconds() // 3600), int((et.total_seconds() % 3600) // 60))
    if body.new_transmission:
        booking.transmission = body.new_transmission
    if body.new_location:
        booking.location = settings.LOCATION_EXAM
        if booking.service_type == ServiceType.TRAINING:
            booking.price = settings.PRICE_TRAINING_NEW
    if body.new_instructor_id:
        booking.instructor_id = body.new_instructor_id

    if body.new_date or body.new_start_time or body.new_instructor_id or body.new_transmission:
        with db.no_autoflush:
            target_instructor = await db.get(Instructor, booking.instructor_id)
            keeps_original_assignment = (
                booking.instructor_id == old_instructor_id
                and booking.transmission == old_transmission
            )
            service_value = (
                booking.service_type.value
                if hasattr(booking.service_type, "value") else str(booking.service_type)
            )
            target_is_available = bool(target_instructor) and await is_instructor_available(
                db, target_instructor, booking.booking_date, booking.start_time,
                booking.end_time, booking.transmission, None, allow_duty=True,
                service_type=service_value,
                preserve_existing_assignment=keeps_original_assignment,
            )
        if not target_instructor:
            raise HTTPException(status_code=404, detail="Новый инструктор не найден")
        if not target_is_available:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Инструктор недоступен по актуальному графику. При выборе нового "
                    "инструктора или КПП должны совпадать параметры карточки."
                ),
            )

    # Проверяем конфликты у назначенного инструктора
    conflict_result = await db.execute(
        select(Booking).where(
            and_(
                Booking.instructor_id == booking.instructor_id,
                Booking.booking_date == booking.booking_date,
                Booking.id != booking.id,
                Booking.status.in_(["pending", "planned", "confirmed", "in_progress"]),
                Booking.start_time < booking.end_time,
                Booking.end_time > booking.start_time,
            )
        )
    )
    conflict = conflict_result.scalars().first()
    if conflict:
        raise HTTPException(status_code=409, detail="Инструктор занят в это время")

    mobile_conflict_result = await db.execute(
        select(MobileBooking).where(
            and_(
                MobileBooking.instructor_id == booking.instructor_id,
                MobileBooking.booking_date == booking.booking_date,
                MobileBooking.status.in_(["pending", "planned", "confirmed", "in_progress"]),
                MobileBooking.start_time < booking.end_time,
                MobileBooking.end_time > booking.start_time,
            )
        )
    )
    if mobile_conflict_result.scalars().first():
        raise HTTPException(status_code=409, detail="Инструктор занят в это время")

    vehicle = await reserve_available_vehicle(
        db, booking.booking_date, booking.start_time, booking.end_time,
        booking.transmission, exclude_booking_id=booking.id,
    )
    if not vehicle:
        raise HTTPException(status_code=409, detail="Подходящая машина уже занята в это время")
    booking.vehicle_id = vehicle.id

    await db.commit()
    await _audit(db, username, "edit_booking", f"id={booking_id}, изменения: дата={body.new_date}, время={body.new_start_time}, инструктор={body.new_instructor_id}")

    # Уведомление инструктору
    instructor_changed = body.new_instructor_id and body.new_instructor_id != old_instructor_id
    target_instructor_id = booking.instructor_id

    import logging as _logging
    import httpx as _httpx
    _logger = _logging.getLogger(__name__)

    instr_result = await db.execute(select(Instructor).where(Instructor.id == target_instructor_id))
    instructor = instr_result.scalar_one_or_none()
    client_result = await db.execute(select(Client).where(Client.id == booking.client_id))
    client = client_result.scalar_one_or_none()
    client_name = client.name if client else "Клиент"

    print(f"[notify edit] instructor={instructor.name if instructor else None}, tg_id={instructor.telegram_id if instructor else None}, token={bool(settings.INSTRUCTOR_BOT_TOKEN)}, changed={instructor_changed}")
    if instructor and instructor.telegram_id and settings.INSTRUCTOR_BOT_TOKEN:
        _RU_DAYS = ["в понедельник", "во вторник", "в среду", "в четверг", "в пятницу", "в субботу", "в воскресенье"]
        _RU_DAYS_SHORT = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
        _today = datetime.now(KZ_TZ).date()
        _bdate = booking.booking_date
        _delta = (_bdate - _today).days
        if _delta <= 7:
            if _delta == 0:
                _day_phrase = "сегодня"
            elif _delta == 1:
                _day_phrase = "завтра"
            elif _delta == 2:
                _day_phrase = "послезавтра"
            else:
                _day_phrase = _RU_DAYS[_bdate.weekday()]
            _date_with_day = f"{_bdate.strftime('%d.%m.%Y')} в {booking.start_time.strftime('%H:%M')}"
        else:
            _day_phrase = None
            _date_with_day = f"{_bdate.strftime('%d.%m.%Y')} ({_RU_DAYS_SHORT[_bdate.weekday()]}) в {booking.start_time.strftime('%H:%M')}"
        trans_label = "Механика" if booking.transmission == "manual" else "Автомат"
        client_name = client.name if client else "Клиент"

        if instructor_changed:
            _new_header = f"{instructor.name}, у вас запись:" if _day_phrase is None else f"{instructor.name}, у вас {_day_phrase} запись:"
            instr_text = (
                f"📌 Новая запись!\n"
                f"{_new_header}\n\n"
                f"📅 {_date_with_day}\n"
                f"👤 Клиент: {client_name}\n"
                f"📍 {booking.location}\n"
                f"Коробка: {trans_label}\n"
                f"💰 К оплате: {booking.price} ₸"
            )
        else:
            _date_changed = f"{_bdate.strftime('%d.%m.%Y')} в {booking.start_time.strftime('%H:%M')}" if _day_phrase else f"{_bdate.strftime('%d.%m.%Y')} ({_RU_DAYS_SHORT[_bdate.weekday()]}) в {booking.start_time.strftime('%H:%M')}"
            instr_text = (
                f"🔄 Запись изменена!\n\n"
                f"{instructor.name}, администратор изменил запись.\n\n"
                f"Было: {old_date.strftime('%d.%m.%Y')} в {old_time}\n"
                f"Стало: 📅 {_date_changed}\n\n"
                f"👤 Клиент: {client_name}\n"
                f"📍 {booking.location}\n"
                f"Коробка: {trans_label}\n"
                f"💰 К оплате: {booking.price} ₸"
            )
        try:
            print(f"[notify edit] Sending to instructor {instructor.name} (tg_id={instructor.telegram_id}), token_len={len(settings.INSTRUCTOR_BOT_TOKEN)}")
            async with _httpx.AsyncClient(timeout=10) as _client_http:
                resp = await _client_http.post(
                    f"https://api.telegram.org/bot{settings.INSTRUCTOR_BOT_TOKEN}/sendMessage",
                    json={"chat_id": instructor.telegram_id.strip(), "text": instr_text},
                )
                print(f"[notify edit] Telegram response: {resp.status_code} {resp.text[:200]}")
        except Exception as e:
            print(f"[notify edit] ERROR: {e}")
            _logger.error(f"[notify edit] Telegram error: {e}")

    # Если инструктор сменился — уведомляем СТАРОГО инструктора об отмене
    if instructor_changed and old_instructor_id:
        old_instr_result = await db.execute(select(Instructor).where(Instructor.id == old_instructor_id))
        old_instructor = old_instr_result.scalar_one_or_none()
        if old_instructor and old_instructor.telegram_id and settings.INSTRUCTOR_BOT_TOKEN:
            try:
                print(f"[notify old instructor] Sending to {old_instructor.name} (tg_id={old_instructor.telegram_id})")
                old_instr_text = (
                    f"❌ Запись отменена!\n\n"
                    f"{old_instructor.name}, клиент {client_name} сменил инструктора.\n\n"
                    f"📅 {old_date.strftime('%d.%m.%Y')} в {old_time}\n"
                    f"Запись к вам отменена."
                )
                async with _httpx.AsyncClient(timeout=10) as _client_http:
                    resp = await _client_http.post(
                        f"https://api.telegram.org/bot{settings.INSTRUCTOR_BOT_TOKEN}/sendMessage",
                        json={"chat_id": old_instructor.telegram_id.strip(), "text": old_instr_text},
                    )
                    print(f"[notify old instructor] Telegram response: {resp.status_code} {resp.text[:200]}")
            except Exception as e:
                print(f"[notify old instructor] ERROR: {e}")
                _logger.error(f"[notify old instructor] Telegram error: {e}")

    return {"ok": True}


class ApplyCertificateRequest(BaseModel):
    certificate_code: str


@router.post("/bookings/{booking_id}/apply-certificate")
async def apply_certificate_to_booking(
    request: Request, booking_id: int, body: ApplyCertificateRequest, db: AsyncSession = Depends(get_db)
):
    """Применить сертификат к записи. Сумма сертификата должна точно совпадать с ценой услуги."""
    username = _get_admin_username(request)
    
    # Получаем запись
    booking_result = await db.execute(select(Booking).where(Booking.id == booking_id).with_for_update())
    booking = booking_result.scalar_one_or_none()
    if not booking:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    
    # Проверяем что сертификат еще не применен
    if booking.certificate_id:
        existing_certificate = await db.get(Certificate, booking.certificate_id)
        if existing_certificate and existing_certificate.code == body.certificate_code.strip().upper():
            return {
                "ok": True, "certificate_applied": True,
                "amount": booking.certificate_amount,
                "certificate_remaining": existing_certificate.remaining,
            }
        raise HTTPException(status_code=400, detail="К этой записи уже применен сертификат")
    
    # Получаем сертификат по коду
    cert_result = await db.execute(
        select(Certificate).where(Certificate.code == body.certificate_code.strip().upper()).with_for_update()
    )
    certificate = cert_result.scalar_one_or_none()
    if not certificate:
        raise HTTPException(status_code=404, detail="Сертификат не найден")
    
    # Проверяем что номинал сертификата ТОЧНО совпадает с ценой записи
    if certificate.nominal != booking.price:
        raise HTTPException(
            status_code=400,
            detail=f"Номинал сертификата ({certificate.nominal}₸) не совпадает с ценой услуги ({booking.price}₸). Необходимо точное совпадение."
        )
    
    # Проверяем что в сертификате достаточно средств
    if certificate.remaining < booking.price:
        raise HTTPException(
            status_code=400,
            detail=f"Недостаточно средств на сертификате. Остаток: {certificate.remaining}₸, требуется: {booking.price}₸"
        )
    
    # Применяем сертификат
    booking.certificate_id = certificate.id
    booking.certificate_amount = booking.price
    booking.payment_status = "paid"
    booking.paid_amount = booking.price
    booking.paid_at = datetime.now(KZ_TZ).replace(tzinfo=None)
    
    # Уменьшаем остаток на сертификате
    certificate.remaining -= booking.price
    if certificate.remaining == 0:
        certificate.is_used = True
    
    await db.commit()
    await _audit(
        db, username, "apply_certificate",
        f"Сертификат {certificate.code} применен к записи #{booking_id}, сумма: {booking.price}₸"
    )
    
    # Отправка уведомления инструктору
    import logging as _logging
    import httpx as _httpx
    _logger = _logging.getLogger(__name__)
    
    instructor_result = await db.execute(select(Instructor).where(Instructor.id == booking.instructor_id))
    instructor = instructor_result.scalar_one_or_none()
    client_result = await db.execute(select(Client).where(Client.id == booking.client_id))
    client = client_result.scalar_one_or_none()
    
    if instructor and instructor.telegram_id and settings.INSTRUCTOR_BOT_TOKEN:
        client_name = client.name if client else "Клиент"
        trans_label = "Механика" if booking.transmission == "manual" else "Автомат"
        
        _RU_DAYS_SHORT = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
        date_str = f"{booking.booking_date.strftime('%d.%m.%Y')} ({_RU_DAYS_SHORT[booking.booking_date.weekday()]})"
        time_str = booking.start_time.strftime('%H:%M')
        
        instr_text = (
            f"🎟️ Применен сертификат!\n\n"
            f"{instructor.name}, клиент оплатил занятие сертификатом.\n\n"
            f"👤 Клиент: {client_name}\n"
            f"📅 Дата: {date_str} в {time_str}\n"
            f"📍 {booking.location}\n"
            f"Коробка: {trans_label}\n"
            f"💰 Оплачено сертификатом: {booking.price}₸"
        )
        
        try:
            async with _httpx.AsyncClient(timeout=10) as _client_http:
                await _client_http.post(
                    f"https://api.telegram.org/bot{settings.INSTRUCTOR_BOT_TOKEN}/sendMessage",
                    json={"chat_id": instructor.telegram_id.strip(), "text": instr_text},
                )
        except Exception as e:
            _logger.error(f"[notify apply_certificate] Telegram error: {e}")
    
    return {
        "ok": True,
        "certificate_applied": True,
        "amount": booking.price,
        "certificate_remaining": certificate.remaining
    }


@router.get("/dashboard")
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    _get_admin_username(request)
    today = today_kz()
    week_ago = today - timedelta(days=7)
    month_ago = today - timedelta(days=30)

    revenue_today = await db.execute(
        select(func.coalesce(func.sum(Booking.price), 0)).where(
            and_(Booking.booking_date == today, Booking.status == "completed")
        )
    )
    revenue_week = await db.execute(
        select(func.coalesce(func.sum(Booking.price), 0)).where(
            and_(Booking.booking_date >= week_ago, Booking.status == "completed")
        )
    )
    revenue_month = await db.execute(
        select(func.coalesce(func.sum(Booking.price), 0)).where(
            and_(Booking.booking_date >= month_ago, Booking.status == "completed")
        )
    )

    total_bookings = await db.execute(select(func.count()).select_from(Booking))
    total_bookings_count = total_bookings.scalar() or 0
    cancelled = await db.execute(
        select(func.count()).select_from(Booking).where(Booking.status == "cancelled")
    )
    no_shows = await db.execute(
        select(func.count()).select_from(Booking).where(Booking.status == "no_show")
    )

    clients_count = await db.execute(
        select(func.count()).select_from(Client).where(Client.is_deleted == False)
    )
    instructors_count = await db.execute(
        select(func.count()).select_from(Instructor)
    )

    return {
        "revenue_today": revenue_today.scalar(),
        "revenue_week": revenue_week.scalar(),
        "revenue_month": revenue_month.scalar(),
        "total_bookings": total_bookings_count,
        "cancelled": cancelled.scalar(),
        "no_shows": no_shows.scalar(),
        "clients_count": clients_count.scalar(),
        "instructors_count": instructors_count.scalar(),
    }


@router.get("/notification-counts")
async def get_notification_counts(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Возвращает количество новых клиентов, непрочитанных сообщений поддержки и непрочитанных записей"""
    _get_admin_username(request)
    # Счётчики меняются сразу после открытия вкладки/диалога. Запрещаем браузеру
    # и Vercel proxy повторно отдавать старое значение следующему polling-запросу.
    response.headers["Cache-Control"] = "no-store, max-age=0"
    
    # Количество новых клиентов. Для новых версий используем watermark ID,
    # а не время: импорт мог записать некорректные даты в будущем.
    state_result = await db.execute(select(AdminState).where(AdminState.id == 1))
    state = state_result.scalar_one_or_none()
    if state and state.clients_viewed_id is not None:
        clients_query = select(func.count()).select_from(Client).where(
            Client.id > state.clients_viewed_id,
            Client.is_deleted == False,
        )
    else:
        # Backward-compatible first run before the watermark is initialized.
        clients_since = (state.clients_viewed_at if state and state.clients_viewed_at else None) or (datetime.now(KZ_TZ) - timedelta(days=1)).replace(tzinfo=None)
        clients_query = select(func.count()).select_from(Client).where(
            Client.created_at > clients_since,
            Client.is_deleted == False,
        )
    new_clients_result = await db.execute(clients_query)
    new_clients = new_clients_result.scalar() or 0
    
    # The navbar must count exactly the messages that have a visible dialog.
    unread_support = await get_unread_support_count(db)

    # Количество непрочитанных записей (admin_viewed == False, статус planned/confirmed)
    unread_bookings_result = await db.execute(
        select(func.count()).select_from(Booking).where(
            Booking.admin_viewed == False,
            Booking.status.in_(["pending", "planned", "confirmed"])
        )
    )
    unread_bookings = unread_bookings_result.scalar() or 0

    # Pending-заявки требуют решения, а не просто просмотра. Этот счётчик не
    # зависит от admin_viewed, чтобы напоминание не исчезало при открытии
    # раздела «Записи».
    pending_applications_result = await db.execute(
        select(func.count()).select_from(Booking).where(
            Booking.status.in_(["pending", "cancellation_pending", "reschedule_pending"])
        )
    )
    pending_applications_count = pending_applications_result.scalar() or 0

    # Количество конфликтных записей (требующих решения администратора)
    conflicts_result = await db.execute(
        select(func.count()).select_from(Booking).where(
            Booking.status.in_(["conflict", "disputed"])
        )
    )
    conflicts_count = conflicts_result.scalar() or 0

    # Количество непрочитанных событий (события из таблицы events после последнего просмотра)
    state_result = await db.execute(select(AdminState).where(AdminState.id == 1))
    state = state_result.scalar_one_or_none()
    viewed_at = state.notifications_viewed_at if state else None
    notif_query = select(func.count()).select_from(Event)
    if state and state.notifications_viewed_id is not None:
        notif_query = notif_query.where(Event.id > state.notifications_viewed_id)
    elif viewed_at:
        notif_query = notif_query.where(Event.created_at > viewed_at)
    unread_notifications_result = await db.execute(notif_query)
    unread_notifications = unread_notifications_result.scalar() or 0
    
    return {
        "new_clients": new_clients,
        "unread_support": unread_support,
        "unread_bookings": unread_bookings,
        "pending_applications_count": pending_applications_count,
        "unread_notifications": unread_notifications,
        "conflicts_count": conflicts_count,
    }


@router.post("/bookings/mark-viewed")
async def mark_bookings_viewed(request: Request, db: AsyncSession = Depends(get_db)):
    """Помечает все непрочитанные записи как просмотренные"""
    _get_admin_username(request)
    await db.execute(
        Booking.__table__.update().where(
            Booking.admin_viewed == False
        ).values(admin_viewed=True)
    )
    await db.commit()
    return {"ok": True}


@router.post("/notifications/mark-viewed")
async def mark_notifications_viewed(request: Request, db: AsyncSession = Depends(get_db)):
    """Сохраняет время последнего просмотра уведомлений"""
    _get_admin_username(request)
    state_result = await db.execute(select(AdminState).where(AdminState.id == 1))
    state = state_result.scalar_one_or_none()
    now = datetime.now(KZ_TZ).replace(tzinfo=None)
    latest_event_id = (await db.execute(select(func.max(Event.id)))).scalar() or 0
    if state:
        state.notifications_viewed_at = now
        state.notifications_viewed_id = latest_event_id
    else:
        db.add(AdminState(id=1, notifications_viewed_at=now, notifications_viewed_id=latest_event_id))
    await db.commit()
    return {"ok": True}


@router.post("/clients/mark-viewed")
async def mark_clients_viewed(request: Request, db: AsyncSession = Depends(get_db)):
    """Сохраняет время последнего просмотра вкладки клиенты"""
    _get_admin_username(request)
    state_result = await db.execute(select(AdminState).where(AdminState.id == 1))
    state = state_result.scalar_one_or_none()
    now = datetime.now(KZ_TZ).replace(tzinfo=None)
    latest_client_id = (await db.execute(select(func.max(Client.id)))).scalar() or 0
    if state:
        state.clients_viewed_at = now
        state.clients_viewed_id = latest_client_id
    else:
        db.add(AdminState(id=1, clients_viewed_at=now, clients_viewed_id=latest_client_id))
    await db.commit()
    return {"ok": True}


class FAQCreate(BaseModel):
    question: str
    answer: str
    sort_order: int = 0


class FAQUpdate(BaseModel):
    question: Optional[str] = None
    answer: Optional[str] = None


@router.get("/faq")
async def faq_list(request: Request, db: AsyncSession = Depends(get_db)):
    _get_admin_username(request)
    result = await db.execute(select(FAQItem).where(FAQItem.is_active == True).order_by(FAQItem.sort_order))
    items = result.scalars().all()
    return [{"id": f.id, "question": f.question, "answer": f.answer, "sort_order": f.sort_order} for f in items]


@router.post("/faq")
async def faq_create(request: Request, body: FAQCreate, db: AsyncSession = Depends(get_db)):
    username = _get_admin_username(request)
    operation_id = _idempotency_key(request)
    if operation_id:
        existing = (await db.execute(select(FAQItem).where(FAQItem.offline_operation_id == operation_id))).scalar_one_or_none()
        if existing:
            return {"ok": True, "id": existing.id}
    item = FAQItem(question=body.question, answer=body.answer, sort_order=body.sort_order,
                   offline_operation_id=operation_id)
    db.add(item)
    existing = await _commit_idempotent_create(db, FAQItem, operation_id)
    if existing:
        return {"ok": True, "id": existing.id}
    await db.refresh(item)
    await _audit(db, username, "create_faq")
    return {"ok": True, "id": item.id}


@router.put("/faq/{faq_id}")
async def faq_update(request: Request, faq_id: int, body: FAQUpdate, db: AsyncSession = Depends(get_db)):
    username = _get_admin_username(request)
    result = await db.execute(select(FAQItem).where(FAQItem.id == faq_id))
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404)
    if body.question is not None:
        item.question = body.question
    if body.answer is not None:
        item.answer = body.answer
    await db.commit()
    await _audit(db, username, "update_faq", f"id={faq_id}")
    return {"ok": True}


@router.delete("/faq/{faq_id}")
async def faq_delete(request: Request, faq_id: int, db: AsyncSession = Depends(get_db)):
    username = _get_admin_username(request)
    result = await db.execute(select(FAQItem).where(FAQItem.id == faq_id))
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404)
    await db.delete(item)
    await db.commit()
    await _audit(db, username, "delete_faq", f"id={faq_id}")
    return {"ok": True}


class CertificateCreate(BaseModel):
    nominal: int


@router.get("/certificates")
async def certificates_list(request: Request, db: AsyncSession = Depends(get_db)):
    _get_admin_username(request)
    result = await db.execute(select(Certificate).order_by(Certificate.created_at.desc()))
    certs = result.scalars().all()
    output = []
    for c in certs:
        client_name = None
        client_phone = None
        if c.activated_by_client_id:
            client_result = await db.execute(select(Client.name, Client.phone).where(Client.id == c.activated_by_client_id))
            row = client_result.first()
            client_name = row[0] if row else None
            client_phone = row[1] if row else None
        output.append({
            "id": c.id, "code": c.code, "nominal": c.nominal,
            "remaining": c.remaining, "is_used": c.is_used,
            "client_name": client_name,
            "client_phone": client_phone,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        })
    return output


@router.post("/certificates")
async def certificate_create(request: Request, body: CertificateCreate, db: AsyncSession = Depends(get_db)):
    username = _get_admin_username(request)
    operation_id = _idempotency_key(request)
    if operation_id:
        existing = (await db.execute(select(Certificate).where(Certificate.offline_operation_id == operation_id))).scalar_one_or_none()
        if existing:
            return {"id": existing.id, "code": existing.code, "nominal": existing.nominal}
    if body.nominal not in (settings.PRICE_EXAM, settings.PRICE_TRAINING_NEW):
        raise HTTPException(status_code=400, detail="Certificate nominal must be 5000 or 10000")
    code = secrets.token_hex(8).upper()
    cert = Certificate(code=code, nominal=body.nominal, remaining=body.nominal,
                       offline_operation_id=operation_id)
    db.add(cert)
    existing = await _commit_idempotent_create(db, Certificate, operation_id)
    if existing:
        return {"id": existing.id, "code": existing.code, "nominal": existing.nominal}
    await _audit(
        db,
        username,
        "create_certificate",
        f"Администратор создал сертификат «{code}» номиналом {body.nominal} ₸.",
    )
    return {"id": cert.id, "code": code, "nominal": body.nominal}


@router.delete("/certificates/{cert_id}")
async def certificate_delete(request: Request, cert_id: int, db: AsyncSession = Depends(get_db)):
    username = _get_admin_username(request)
    result = await db.execute(select(Certificate).where(Certificate.id == cert_id))
    cert = result.scalar_one_or_none()
    if not cert:
        raise HTTPException(status_code=404, detail="Certificate not found")
    await db.delete(cert)
    await db.commit()
    await _audit(db, username, "delete_certificate", f"Администратор удалил сертификат «{cert.code}».")
    return {"ok": True}


@router.get("/audit-logs")
async def audit_logs(request: Request, db: AsyncSession = Depends(get_db)):
    """Понятный журнал действий без технических имён и внутренних ID."""
    _get_admin_username(request)
    await archive_previous_day_logs(db)
    
    # Словарь переводов действий админа на русский
    ACTION_TRANSLATIONS = {
        "login": "Вход в систему",
        "logout": "Выход из системы",
        "change_password": "Смена пароля",
        "create_instructor": "Создал карточку инструктора",
        "update_instructor": "Изменил карточку инструктора",
        "delete_instructor": "Удалил инструктора",
        "update_instructor_days_off": "Обновлены выходные инструктора",
        "add_instructor_day_off": "Добавлен выходной инструктор",
        "delete_instructor_day_off": "Удалён выходной инструктора",
        "new_booking": "Создана запись",
        "delete_booking": "Удалена запись",
        "update_booking_status": "Изменён статус записи",
        "booking_cancelled": "Запись окончательно отменена",
        "booking_cancellation_requested": "Клиент запросил отмену",
        "booking_confirmed": "Заявка подтверждена",
        "booking_rejected": "Заявка отклонена",
        "booking_merged": "Найден дубль записи",
        "possible_duplicate_detected": "Найден возможный дубль",
        "offline_booking_conflict": "Конфликт офлайн-записи и заявки клиента",
        "manual_booking_conflict": "Ручная запись получила приоритет над онлайн-заявкой",
        "check_pending_conflicts": "Проверены конфликты записей",
        "merge_resolved_confirm": "Спорная запись подтверждена",
        "merge_resolved_reject": "Спорная запись оставлена на рассмотрении",
        "dispute_resolved_confirm": "Спорная запись подтверждена",
        "dispute_resolved_reject": "Спорная запись отклонена",
        "create_client": "Создал карточку клиента",
        "update_client": "Отредактировал карточку клиента",
        "reset_client_password": "Назначил новый пароль клиенту",
        "delete_client": "Удалил клиента",
        "client_profile_reactivated": "Восстановил карточку клиента",
        "create_certificate": "Создал сертификат",
        "delete_certificate": "Удалил сертификат",
        "certificate_confirmed": "Сертификат подтверждён",
        "certificate_rejected": "Сертификат отклонён",
        "activate_certificate_for_client": "Активирован сертификат для клиента",
        "apply_certificate": "Применил сертификат к записи",
        "assign_package": "Применил пакет клиенту",
        "package_created": "Создал пакет",
        "package_updated": "Изменил пакет",
        "package_deleted": "Удалил пакет",
        "create_vehicle": "Добавил автомобиль",
        "update_vehicle": "Изменил карточку автомобиля",
        "update_vehicle_repair_status": "Изменил состояние автомобиля",
        "delete_vehicle": "Удалил автомобиль",
        "waiting_list_add": "Добавил клиента в лист ожидания",
        "waiting_list_update": "Изменил запись в листе ожидания",
        "waiting_list_delete": "Удалил запись из листа ожидания",
        "waiting_list_status": "Изменил статус в листе ожидания",
        "client_blocked": "Ограничил доступ клиенту",
        "client_unblocked": "Снял ограничение с клиента",
        "set_lead_instructor": "Назначил главного инструктора",
        "update_lead_days_off": "Изменил выходные главного инструктора",
        "update_instructor_daily_schedule": "Изменил график инструктора на дату",
        "delete_instructor_daily_schedule": "Удалил особый график инструктора",
        "booking_rescheduled": "Подтвердил перенос записи",
        "booking_reschedule_rejected": "Отклонил перенос записи",
        "delete_mobile_booking": "Удалил запись из приложения",
        "purge_cancelled_bookings": "Очистил отменённые записи",
        "offline_same_client_dispute": "Зафиксировал конфликт офлайн-записи",
        "support_reply_client": "Ответил клиенту в поддержке",
        "support_close_client": "Закрыл чат поддержки с клиентом",
        "support_reply_instructor": "Ответил инструктору в поддержке",
        "delete_support_dialog": "Удалил диалог поддержки с клиентом",
        "delete_instructor_support_dialog": "Удалил диалог поддержки с инструктором",
        "create_faq": "Создан FAQ",
        "update_faq": "Обновлён FAQ",
        "delete_faq": "Удалён FAQ",
        "edit_booking": "Запись изменена",
        "reassign_booking": "Инструктор в записи заменён",
        "instructor_client_arrived": "Инструктор отметил приход клиента",
        "instructor_lesson_completed": "Инструктор отметил завершение занятия",
        "instructor_bot_opened": "Инструктор открыл бот",
        "instructor_bot_authorized": "Инструктор подтвердил номер телефона",
        "instructor_schedule_viewed": "Инструктор открыл расписание",
        "instructor_support_message": "Инструктор отправил сообщение в поддержку",
        "system_client_arrived_auto": "Система автоматически отметила приход клиента",
        "system_lesson_completed_auto": "Система автоматически завершила занятие и зафиксировала оплату",
        "system_booking_history_purged": "Система очистила историю записей старше двух месяцев",
        "full_backup_export": "Экспорт полной резервной копии",
        "full_backup_restore": "Восстановление из резервной копии",
        "export_archived_logs": "Выгрузил архив логов",
    }
    
    # Аудит владельца содержит только действия реальных учётных записей
    # администраторов. Клиентские, инструкторские и системные записи живут в
    # отдельных журналах и не должны маскироваться под администратора.
    admin_usernames = list((await db.execute(select(Admin.username))).scalars().all())
    if not admin_usernames:
        return []
    result = await db.execute(
        select(AuditLog).where(
            AuditLog.admin_username.in_(admin_usernames)
        ).order_by(AuditLog.created_at.desc())
    )
    logs = result.scalars().all()

    # Старые записи аудита содержали внутренние ID после символа '#'. В
    # интерфейсе показываем только пользовательский шестизначный номер, если
    # он уже есть. Это сохраняет понятную связь с записью и не смешивает два
    # разных идентификатора.
    booking_ids: set[int] = set()
    booking_id_pattern = re.compile(
        r"(?i)(?:запись|записи|заявка|online|manual)\s*#(\d{1,5})\b"
    )
    for log in logs:
        booking_ids.update(int(value) for value in booking_id_pattern.findall(log.details or ""))
    booking_numbers: dict[int, str] = {}
    if booking_ids:
        number_rows = await db.execute(
            select(Booking.id, Booking.booking_number).where(Booking.id.in_(booking_ids))
        )
        booking_numbers = {
            booking_id: booking_number
            for booking_id, booking_number in number_rows.all()
            if booking_number
        }

    def format_details(details: str | None) -> str | None:
        if not details:
            return details

        def replace_booking_id(match: re.Match) -> str:
            booking_id = int(match.group(2))
            number = booking_numbers.get(booking_id)
            if not number:
                return match.group(0)
            return f"{match.group(1)} №{number}"

        return re.sub(
            r"(?i)(запись|записи|заявка|online|manual)\s*#(\d{1,5})\b",
            replace_booking_id,
            details,
        )

    return [
        {
            "id": l.id,
            "admin_username": l.admin_username,
            "action": ACTION_TRANSLATIONS.get(l.action, "Выполнил действие в админке"),
            "details": format_details(l.details),
            "created_at": str(l.created_at)
        }
        for l in logs
    ]


@router.get("/logs/archive/export")
async def export_archived_logs(request: Request, db: AsyncSession = Depends(get_db)):
    """Download the permanent audit and client-event archive as an Excel-safe CSV."""
    username = _get_admin_username(request)
    await archive_previous_day_logs(db)
    rows = (await db.execute(
        select(ArchivedLog).order_by(ArchivedLog.created_at.desc(), ArchivedLog.id.desc())
    )).scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Дата и время", "Раздел", "Источник", "Действие", "Администратор",
        "Клиент ID", "Инструктор ID", "Запись ID", "Описание", "Перенесено в архив",
    ])
    for item in rows:
        is_audit = item.source_type == "audit"
        writer.writerow([
            item.created_at.isoformat(sep=" ", timespec="seconds") if item.created_at else "",
            "Аудит" if is_audit else "События",
            "Админка" if is_audit else (item.event_source or ""),
            item.action if is_audit else (item.event_type or ""),
            item.admin_username or "",
            item.client_id or "", item.instructor_id or "", item.booking_id or "",
            item.details if is_audit else (item.message or ""),
            item.archived_at.isoformat(sep=" ", timespec="seconds") if item.archived_at else "",
        ])
    await _audit(db, username, "export_archived_logs", "Администратор выгрузил архив логов.")
    filename = f"nomad_logs_{today_kz().isoformat()}.csv"
    return StreamingResponse(
        iter(["\ufeff" + output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


async def _list_archived_logs(db: AsyncSession, source_type: str) -> list[dict]:
    """Return one server-side archive section for the Archive tab."""
    await archive_previous_day_logs(db)
    filters = [ArchivedLog.source_type == source_type]
    if source_type == "event":
        # The Events tab contains client actions from Telegram and the mobile
        # app. Keep its permanent archive scoped to the very same records.
        filters.extend((
            ArchivedLog.client_id.isnot(None),
            ArchivedLog.event_source.in_(("telegram", "mobile")),
        ))
    rows = (await db.execute(
        select(ArchivedLog)
        .where(*filters)
        .order_by(ArchivedLog.created_at.desc(), ArchivedLog.id.desc())
    )).scalars().all()
    return [
        {
            "id": row.id,
            "admin_username": row.admin_username,
            "action": row.action,
            "details": row.details,
            "event_type": row.event_type,
            "event_source": row.event_source,
            "client_id": row.client_id,
            "instructor_id": row.instructor_id,
            "booking_id": row.booking_id,
            "message": row.message,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "archived_at": row.archived_at.isoformat() if row.archived_at else None,
        }
        for row in rows
    ]


@router.get("/logs/archive/audit")
async def list_archived_audit_logs(request: Request, db: AsyncSession = Depends(get_db)):
    """Show every archived administrator action from the server database."""
    _get_admin_username(request)
    return await _list_archived_logs(db, "audit")


@router.get("/logs/archive/events")
async def list_archived_event_logs(request: Request, db: AsyncSession = Depends(get_db)):
    """Show every archived client event from the server database."""
    _get_admin_username(request)
    return await _list_archived_logs(db, "event")


@router.get("/analytics/heatmap")
async def heatmap(request: Request, db: AsyncSession = Depends(get_db)):
    _get_admin_username(request)
    result = await db.execute(
        select(Booking.booking_date, Booking.start_time, func.count()).where(
            Booking.status.in_(["confirmed", "completed"])
        ).group_by(Booking.booking_date, Booking.start_time).order_by(Booking.booking_date)
    )
    rows = result.all()
    data = []
    for row in rows:
        d, t, count = row
        day_name = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"][d.weekday()] if isinstance(d, date) else str(d)
        data.append({"date": str(d), "day_name": day_name, "hour": t.hour if isinstance(t, time) else 0, "count": count})
    return data


REVENUE_REFRESH_INTERVAL_HOURS = 3
REVENUE_PERIOD_DAYS = {"day": 1, "week": 7, "month": 30}
REVENUE_PERIOD_LABELS = {
    "day": "1 день", "week": "7 дней", "month": "30 дней", "all": "Всё время",
}
REVENUE_GRANULARITY_LABELS = {
    "day": "по часам", "week": "по дням", "month": "по неделям", "all": "по дням",
}
WEEKDAY_NAMES = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]


def _hour_bucket(moment: datetime) -> datetime:
    return moment.replace(minute=0, second=0, microsecond=0)


def _day_bucket(moment: datetime) -> datetime:
    return datetime.combine(moment.date(), time.min)


def _build_revenue_analytics(rows: list[tuple], current: datetime) -> dict:
    """Build non-cumulative revenue curves with a period-specific time scale."""
    current = current.replace(tzinfo=None)
    lessons = []
    for booking_date, start_time, price in rows:
        if not isinstance(booking_date, date) or not isinstance(start_time, time):
            continue
        moment = datetime.combine(booking_date, start_time)
        if moment <= current:
            lessons.append((moment, max(0, int(price or 0))))

    earliest_date = min((moment.date() for moment, _ in lessons), default=current.date())
    starts = {key: datetime.combine(current.date() - timedelta(days=days - 1), time.min)
              for key, days in REVENUE_PERIOD_DAYS.items()}
    starts["all"] = datetime.combine(earliest_date, time.min)

    period_specs = {
        "day": {"step": timedelta(hours=1), "end": _hour_bucket(current), "granularity": "hour"},
        "week": {"step": timedelta(days=1), "end": _day_bucket(current), "granularity": "day"},
        "month": {"step": timedelta(days=7), "end": _day_bucket(current), "granularity": "week"},
        "all": {"step": timedelta(days=1), "end": _day_bucket(current), "granularity": "day"},
    }
    periods = {}
    for key in ("day", "week", "month", "all"):
        start = starts[key]
        spec = period_specs[key]
        revenue_by_bucket: dict[datetime, int] = {}
        bookings_by_bucket: dict[datetime, int] = {}
        for moment, revenue in lessons:
            if moment < start:
                continue
            if key == "day":
                bucket = _hour_bucket(moment)
            elif key == "month":
                days_from_start = (moment.date() - start.date()).days
                bucket = start + timedelta(days=(days_from_start // 7) * 7)
            else:
                bucket = _day_bucket(moment)
            revenue_by_bucket[bucket] = revenue_by_bucket.get(bucket, 0) + revenue
            bookings_by_bucket[bucket] = bookings_by_bucket.get(bucket, 0) + 1

        points = []
        cursor = start
        while cursor <= spec["end"]:
            bucket_revenue = revenue_by_bucket.get(cursor, 0)
            points.append({
                "timestamp": cursor.replace(tzinfo=KZ_TZ).isoformat(),
                "revenue": bucket_revenue,
                # Retained for compatibility with an older cached frontend.
                "bucket_revenue": bucket_revenue,
                "bookings": bookings_by_bucket.get(cursor, 0),
            })
            cursor += spec["step"]
        periods[key] = {
            "label": REVENUE_PERIOD_LABELS[key],
            "granularity": spec["granularity"],
            "granularity_label": REVENUE_GRANULARITY_LABELS[key],
            "total_revenue": sum(point["revenue"] for point in points),
            "points": points,
        }

    hour_totals = {hour: {"revenue": 0, "bookings": 0} for hour in range(24)}
    day_totals = {weekday: {"revenue": 0, "bookings": 0} for weekday in range(7)}
    for moment, revenue in lessons:
        hour = moment.hour
        hour_totals[hour]["revenue"] += revenue
        hour_totals[hour]["bookings"] += 1
        day_totals[moment.weekday()]["revenue"] += revenue
        day_totals[moment.weekday()]["bookings"] += 1

    profitable_hours = [
        {
            "start_hour": hour,
            "end_hour": (hour + 1) % 24,
            "label": f"{hour:02d}:00–{(hour + 1):02d}:00",
            **totals,
        }
        for hour, totals in hour_totals.items()
        if totals["bookings"] > 0
    ]
    profitable_hours.sort(key=lambda item: (-item["revenue"], -item["bookings"], item["start_hour"]))

    profitable_days = [
        {"weekday": weekday, "label": WEEKDAY_NAMES[weekday], **totals}
        for weekday, totals in day_totals.items()
        if totals["bookings"] > 0
    ]
    profitable_days.sort(key=lambda item: (-item["revenue"], -item["bookings"], item["weekday"]))

    return {
        "refresh_interval_hours": REVENUE_REFRESH_INTERVAL_HOURS,
        "updated_at": current.replace(tzinfo=KZ_TZ).isoformat(),
        "periods": periods,
        "profitable_hours": profitable_hours[:3],
        "profitable_days": profitable_days[:3],
    }


@router.get("/analytics/revenue")
async def revenue_analytics(request: Request, db: AsyncSession = Depends(get_db)):
    """Return live interval revenue curves and all-time profit leaders."""
    _get_admin_username(request)
    rows = (await db.execute(
        select(Booking.booking_date, Booking.start_time, Booking.price)
        .where(Booking.status == "completed")
        .order_by(Booking.booking_date, Booking.start_time, Booking.id)
    )).all()
    return _build_revenue_analytics(rows, now_kz())


@router.get("/analytics/instructor-load")
async def instructor_load(request: Request, days: int = 30, db: AsyncSession = Depends(get_db)):
    _get_admin_username(request)
    since = datetime.now(KZ_TZ).date() - timedelta(days=days)
    result = await db.execute(
        select(Instructor.name, func.count(Booking.id)).join(Booking).where(
            and_(Booking.booking_date >= since, Booking.status.in_(["confirmed", "completed"]))
        ).group_by(Instructor.id, Instructor.name)
    )
    rows = result.all()
    return [{"name": r[0], "bookings": r[1]} for r in rows]


@router.get("/analytics/booking-sources")
async def booking_sources(request: Request, db: AsyncSession = Depends(get_db)):
    """Count booking actions by channel; clients may appear in every channel."""
    _get_admin_username(request)
    rows = (await db.execute(
        select(Booking.source, func.count(Booking.id)).group_by(Booking.source)
    )).all()
    return _build_booking_source_breakdown(rows)


def _build_booking_source_breakdown(rows: list[tuple]) -> dict:
    raw: dict[str, int] = {}
    for source, count in rows:
        key = str(source or "").lower()
        raw[key] = raw.get(key, 0) + int(count)
    counts = {
        "telegram": raw.get("telegram", 0),
        "mobile": raw.get("mobile", 0),
        "manual": sum(raw.get(source, 0) for source in ("manual", "admin", "admin_offline", "offline")),
    }
    total = sum(raw.values())

    def item(key: str) -> dict:
        count = counts[key]
        return {"count": count, "percent": round(count * 100 / total, 1) if total else 0.0}

    known = sum(counts.values())
    return {
        "total": total,
        "telegram": item("telegram"),
        "mobile": item("mobile"),
        "manual": item("manual"),
        "unknown": max(0, total - known),
    }


@router.get("/analytics/booking-sources/extended")
async def extended_booking_sources(request: Request, db: AsyncSession = Depends(get_db)):
    """Online-only expanded source and completed-lesson analytics."""
    _get_admin_username(request)
    all_rows = (await db.execute(
        select(Booking.source, func.count(Booking.id)).group_by(Booking.source)
    )).all()
    today_start = datetime.combine(today_kz(), time.min)
    tomorrow_start = today_start + timedelta(days=1)
    today_rows = (await db.execute(
        select(Booking.source, func.count(Booking.id))
        .where(Booking.created_at >= today_start, Booking.created_at < tomorrow_start)
        .group_by(Booking.source)
    )).all()
    lesson_rows = (await db.execute(
        select(Booking.client_id, func.count(Booking.id))
        .where(Booking.status == "completed")
        .group_by(Booking.client_id)
    )).all()
    lesson_counts = [int(count) for _, count in lesson_rows]
    completed_lessons = sum(lesson_counts)
    clients_counted = len(lesson_counts)
    all_time = _build_booking_source_breakdown(all_rows)
    today = _build_booking_source_breakdown(today_rows)

    return {
        "periods": {"today": today, "all": all_time},
        "client_lessons": {
            "completed_lessons": completed_lessons,
            "clients_counted": clients_counted,
            "average_per_client": round(completed_lessons / clients_counted, 1) if clients_counted else 0.0,
            "maximum_per_client": max(lesson_counts, default=0),
        },
    }


@router.get("/analytics/gender")
async def gender_breakdown(request: Request, db: AsyncSession = Depends(get_db)):
    """Return the latest cached name-based estimate; never call AI from a page request."""
    _get_admin_username(request)
    cached = await db.get(GenderAnalytics, 1)
    if cached is None:
        return {
            "status": "pending", "total": 0, "updated_at": None,
            "male": {"count": 0, "percent": 0.0},
            "female": {"count": 0, "percent": 0.0},
            "unknown": {"count": 0, "percent": 0.0},
        }

    total = max(0, int(cached.total_count or 0))
    known_total = max(0, int(cached.male_count or 0)) + max(0, int(cached.female_count or 0))

    def item(count: int, denominator: int) -> dict:
        value = max(0, int(count or 0))
        return {"count": value, "percent": round(value * 100 / denominator, 1) if denominator else 0.0}

    return {
        "status": "ready",
        "total": total,
        "updated_at": cached.updated_at.isoformat() if cached.updated_at else None,
        "male": item(cached.male_count, known_total),
        "female": item(cached.female_count, known_total),
        "unknown": item(cached.unknown_count, total),
    }


@router.get("/export/bookings")
async def export_bookings(
    request: Request,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    _get_admin_username(request)
    query = select(Booking).options(selectinload(Booking.client), selectinload(Booking.instructor))
    conditions = []
    if date_from:
        conditions.append(Booking.booking_date >= date.fromisoformat(date_from))
    if date_to:
        conditions.append(Booking.booking_date <= date.fromisoformat(date_to))
    if conditions:
        query = query.where(and_(*conditions))
    query = query.order_by(Booking.booking_date, Booking.start_time)
    result = await db.execute(query)
    bookings = result.scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Дата", "Время", "Клиент", "Телефон", "Инструктор", "Услуга", "Коробка", "Площадка", "Статус", "Цена"])
    for b in bookings:
        writer.writerow([
            str(b.booking_date), str(b.start_time),
            b.client.name if b.client else "", b.client.phone if b.client else "",
            b.instructor.name if b.instructor else "",
            b.service_type, b.transmission, b.location, b.status, b.price,
        ])
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=bookings.csv"},
    )


@router.get("/export/clients")
async def export_clients(request: Request, db: AsyncSession = Depends(get_db)):
    _get_admin_username(request)
    result = await db.execute(
        select(Client).where(Client.is_deleted == False).order_by(Client.created_at.desc())
    )
    clients = result.scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Имя", "Телефон", "Telegram ID", "Записей", "Дата регистрации"])
    for c in clients:
        bookings_count_result = await db.execute(select(func.count()).select_from(Booking).where(Booking.client_id == c.id))
        bookings_count = bookings_count_result.scalar() or 0
        writer.writerow([c.name, c.phone or "", c.telegram_id, bookings_count, str(c.created_at)])
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=clients.csv"},
    )


@router.get("/export/full-backup")
async def export_full_backup(request: Request, format: str = "html", db: AsyncSession = Depends(get_db)):
    """Полная резервная копия всей базы данных в HTML или JSON формате"""
    username = _get_admin_username(request)
    await archive_previous_day_logs(db)
    
    import json
    
    # Получаем все данные из всех таблиц
    backup_data = {
        "format_version": 3,
        "backup_date": datetime.now(KZ_TZ).isoformat(),
        "backup_by": username,
    }
    
    # Клиенты
    clients_result = await db.execute(select(Client).order_by(Client.created_at.desc()))
    clients = clients_result.scalars().all()
    backup_data["clients"] = []
    for c in clients:
        bookings_count_result = await db.execute(select(func.count()).select_from(Booking).where(Booking.client_id == c.id))
        bookings_count = bookings_count_result.scalar() or 0
        backup_data["clients"].append({
            "id": c.id,
            "name": c.name,
            "phone": c.phone,
            "password_hash": c.password_hash,
            "avatar_url": c.avatar_url,
            "is_deleted": c.is_deleted,
            "telegram_id": c.telegram_id,
            "referral_code": c.referral_code,
            "referral_discount_available": c.referral_discount_available,
            "referred_by_client_id": c.referred_by_client_id,
            "offline_operation_id": c.offline_operation_id,
            "reschedule_count_24h": c.reschedule_count_24h,
            "reschedule_window_started_at": c.reschedule_window_started_at.isoformat() if c.reschedule_window_started_at else None,
            "support_chat_opened_at": c.support_chat_opened_at.isoformat() if c.support_chat_opened_at else None,
            "support_chat_closed_at": c.support_chat_closed_at.isoformat() if c.support_chat_closed_at else None,
            "bookings_count": bookings_count,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        })
    
    # Инструкторы
    instructors_result = await db.execute(select(Instructor).order_by(Instructor.name))
    instructors = instructors_result.scalars().all()
    backup_data["instructors"] = []
    for i in instructors:
        backup_data["instructors"].append({
            "id": i.id,
            "name": i.name,
            "phone": i.phone,
            "telegram_id": i.telegram_id,
            "telegram_username": i.telegram_username,
            "transmission": i.transmission,
            "lesson_type": i.lesson_type,
            "gender": i.gender,
            "experience_years": i.experience_years,
            "rating": i.rating,
            "is_active": i.is_active,
            "is_duty": i.is_duty,
            "is_lead": i.is_lead,
            "working_hours_start": str(i.working_hours_start) if i.working_hours_start else None,
            "working_hours_end": str(i.working_hours_end) if i.working_hours_end else None,
            "lunch_start": str(i.lunch_start) if i.lunch_start else None,
            "lunch_end": str(i.lunch_end) if i.lunch_end else None,
            "days_off": i.days_off,
            "description": i.description,
            "avatar_url": i.avatar_url,
            "offline_operation_id": i.offline_operation_id,
            "created_at": i.created_at.isoformat() if i.created_at else None,
        })

    vehicles = (await db.execute(select(Vehicle).order_by(Vehicle.id))).scalars().all()
    backup_data["vehicles"] = [{
        "id": vehicle.id, "name": vehicle.name, "transmission": vehicle.transmission,
        "is_under_repair": vehicle.is_under_repair,
        "created_at": vehicle.created_at.isoformat() if vehicle.created_at else None,
    } for vehicle in vehicles]

    # Записи (Bookings)
    bookings_result = await db.execute(
        select(Booking)
        .options(selectinload(Booking.client), selectinload(Booking.instructor))
        .order_by(Booking.booking_date.desc(), Booking.start_time.desc())
    )
    bookings = bookings_result.scalars().all()
    backup_data["bookings"] = []
    for b in bookings:
        backup_data["bookings"].append({
            "id": b.id,
            "client_id": b.client_id,
            "client_name": b.client.name if b.client else None,
            "client_phone": b.client.phone if b.client else None,
            "instructor_id": b.instructor_id,
            "instructor_name": b.instructor.name if b.instructor else None,
            "vehicle_id": b.vehicle_id,
            "service_type": b.service_type,
            "transmission": b.transmission,
            "location": b.location,
            "booking_date": str(b.booking_date),
            "start_time": str(b.start_time),
            "end_time": str(b.end_time),
            "status": b.status,
            "price": b.price,
            "base_price": b.base_price,
            "certificate_amount": b.certificate_amount,
            "referral_discount_amount": b.referral_discount_amount,
            "payment_status": b.payment_status,
            "paid_amount": b.paid_amount,
            "paid_at": b.paid_at.isoformat() if b.paid_at else None,
            "source": b.source,
            "package_id": b.package_id,
            "certificate_id": b.certificate_id,
            "package_bonus_exam_used": b.package_bonus_exam_used,
            "cancellation_previous_status": b.cancellation_previous_status,
            "confirmation_sent": b.confirmation_sent,
            "confirmed_by_client": b.confirmed_by_client,
            "admin_confirmed": b.admin_confirmed,
            "admin_confirmed_at": b.admin_confirmed_at.isoformat() if b.admin_confirmed_at else None,
            "conflict_reason": b.conflict_reason,
            "rating_sent": b.rating_sent,
            "reminder_24h_sent": b.reminder_24h_sent,
            "reminder_1h_sent": b.reminder_1h_sent,
            "reminder_10min_sent": b.reminder_10min_sent,
            "admin_viewed": b.admin_viewed,
            "booking_number": b.booking_number,
            "offline_operation_id": b.offline_operation_id,
            "reschedule_previous_status": b.reschedule_previous_status,
            "requested_reschedule_date": b.requested_reschedule_date.isoformat() if b.requested_reschedule_date else None,
            "requested_reschedule_start_time": str(b.requested_reschedule_start_time) if b.requested_reschedule_start_time else None,
            "requested_reschedule_end_time": str(b.requested_reschedule_end_time) if b.requested_reschedule_end_time else None,
            "reschedule_requested_at": b.reschedule_requested_at.isoformat() if b.reschedule_requested_at else None,
            "completed_at": b.completed_at.isoformat() if b.completed_at else None,
            "archived_at": b.archived_at.isoformat() if b.archived_at else None,
            "created_at": b.created_at.isoformat() if b.created_at else None,
        })
    
    # Сертификаты
    certificates_result = await db.execute(select(Certificate).order_by(Certificate.created_at.desc()))
    certificates = certificates_result.scalars().all()
    backup_data["certificates"] = []
    for cert in certificates:
        client_name = None
        if cert.activated_by_client_id:
            client_result = await db.execute(select(Client.name).where(Client.id == cert.activated_by_client_id))
            row = client_result.first()
            client_name = row[0] if row else None
        backup_data["certificates"].append({
            "id": cert.id,
            "code": cert.code,
            "nominal": cert.nominal,
            "remaining": cert.remaining,
            "is_used": cert.is_used,
            "activated_by_client_id": cert.activated_by_client_id,
            "activated_by_client_name": client_name,
            "used_by_user_id": cert.used_by_user_id,
            "used_at": cert.used_at.isoformat() if cert.used_at else None,
            "offline_operation_id": cert.offline_operation_id,
            "created_at": cert.created_at.isoformat() if cert.created_at else None,
        })
    
    # Пакеты
    packages_result = await db.execute(select(Package))
    packages = packages_result.scalars().all()
    backup_data["packages"] = []
    for p in packages:
        backup_data["packages"].append({
            "id": p.id,
            "name": p.name,
            "sessions_count": p.sessions_count,
            "price": p.price,
            "description": p.description,
            "validity_days": p.validity_days,
            "bonus_exam": p.bonus_exam,
            "code": p.code,
            "is_active": p.is_active,
            "offline_operation_id": p.offline_operation_id,
            "created_at": p.created_at.isoformat() if p.created_at else None,
        })
    
    # Пакеты клиентов
    client_packages_result = await db.execute(
        select(ClientPackage).options(selectinload(ClientPackage.package))
    )
    client_packages = client_packages_result.scalars().all()
    backup_data["client_packages"] = []
    for cp in client_packages:
        backup_data["client_packages"].append({
            "id": cp.id,
            "client_id": cp.client_id,
            "package_id": cp.package_id,
            "package_name": cp.package.name if cp.package else None,
            "remaining_sessions": cp.remaining_sessions,
            "is_active": cp.is_active,
            "purchased_at": cp.purchased_at.isoformat() if cp.purchased_at else None,
            "expires_at": cp.expires_at.isoformat() if cp.expires_at else None,
            "remaining_bonus_exams": cp.remaining_bonus_exams,
        })

    # Расписание и выходные определяют фактическую доступность слотов.
    schedules = (await db.execute(select(InstructorDailySchedule))).scalars().all()
    backup_data["instructor_daily_schedules"] = [{
        "instructor_id": item.instructor_id, "schedule_date": item.schedule_date.isoformat(),
        "is_day_off": item.is_day_off,
        "working_hours_start": str(item.working_hours_start) if item.working_hours_start else None,
        "working_hours_end": str(item.working_hours_end) if item.working_hours_end else None,
        "lunch_start": str(item.lunch_start) if item.lunch_start else None,
        "lunch_end": str(item.lunch_end) if item.lunch_end else None,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    } for item in schedules]
    days_off = (await db.execute(select(InstructorDayOff))).scalars().all()
    backup_data["instructor_days_off"] = [{
        "instructor_id": item.instructor_id, "day_off_date": item.day_off_date.isoformat(),
        "created_at": item.created_at.isoformat() if item.created_at else None,
    } for item in days_off]
    rotations = (await db.execute(select(InstructorRotation))).scalars().all()
    backup_data["instructor_rotations"] = [{
        "instructor_id": item.instructor_id,
        "last_booking_date": item.last_booking_date.isoformat() if item.last_booking_date else None,
        "last_booking_time": str(item.last_booking_time) if item.last_booking_time else None,
        "rotation_count": item.rotation_count,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    } for item in rotations]

    certificate_requests = (await db.execute(select(CertificateRequest))).scalars().all()
    backup_data["certificate_requests"] = [{
        "client_id": item.client_id, "code_entered": item.code_entered,
        "matched_certificate_id": item.matched_certificate_id, "booking_id": item.booking_id,
        "status": item.status, "created_at": item.created_at.isoformat() if item.created_at else None,
    } for item in certificate_requests]
    events = (await db.execute(select(Event))).scalars().all()
    backup_data["events"] = [{
        "event_type": item.event_type, "source": item.source, "client_id": item.client_id,
        "instructor_id": item.instructor_id, "booking_id": item.booking_id, "message": item.message,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    } for item in events]
    archived_logs = (await db.execute(select(ArchivedLog))).scalars().all()
    backup_data["archived_logs"] = [{
        "source_type": item.source_type, "source_log_id": item.source_log_id,
        "admin_username": item.admin_username, "action": item.action,
        "details": item.details, "event_type": item.event_type,
        "event_source": item.event_source, "client_id": item.client_id,
        "instructor_id": item.instructor_id, "booking_id": item.booking_id,
        "message": item.message,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "archived_at": item.archived_at.isoformat() if item.archived_at else None,
    } for item in archived_logs]

    backup_data["waiting_list"] = [{
        "id": item.id, "name": item.name, "phone": item.phone,
        "desired_date": item.desired_date.isoformat() if item.desired_date else None,
        "desired_time_start": str(item.desired_time_start) if item.desired_time_start else None,
        "desired_time_end": str(item.desired_time_end) if item.desired_time_end else None,
        "transmission": item.transmission, "instructor_id": item.instructor_id,
        "instructor_gender": item.instructor_gender, "status": item.status, "notes": item.notes,
        "offline_operation_id": item.offline_operation_id,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    } for item in (await db.execute(select(WaitingListEntry))).scalars().all()]
    backup_data["client_blocks"] = [{
        "id": item.id, "client_id": item.client_id,
        "blocked_until": item.blocked_until.isoformat(), "reason": item.reason,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    } for item in (await db.execute(select(ClientBlock))).scalars().all()]
    admin_state = await db.get(AdminState, 1)
    backup_data["admin_state"] = ({
        "notifications_viewed_at": admin_state.notifications_viewed_at.isoformat() if admin_state.notifications_viewed_at else None,
        "clients_viewed_at": admin_state.clients_viewed_at.isoformat() if admin_state.clients_viewed_at else None,
        "notifications_viewed_id": admin_state.notifications_viewed_id,
        "clients_viewed_id": admin_state.clients_viewed_id,
    } if admin_state else None)
    gender_analytics = await db.get(GenderAnalytics, 1)
    backup_data["gender_analytics"] = ({
        "male_count": gender_analytics.male_count, "female_count": gender_analytics.female_count,
        "unknown_count": gender_analytics.unknown_count, "total_count": gender_analytics.total_count,
        "model": gender_analytics.model,
        "updated_at": gender_analytics.updated_at.isoformat() if gender_analytics.updated_at else None,
    } if gender_analytics else None)
    
    # FAQ
    faq_result = await db.execute(select(FAQItem).order_by(FAQItem.sort_order))
    faq_items = faq_result.scalars().all()
    backup_data["faq"] = []
    for f in faq_items:
        backup_data["faq"].append({
            "id": f.id,
            "question": f.question,
            "answer": f.answer,
            "sort_order": f.sort_order,
            "is_active": f.is_active,
            "offline_operation_id": f.offline_operation_id,
        })
    
    # Рейтинги
    ratings_result = await db.execute(select(RatingRecord).order_by(RatingRecord.created_at.desc()))
    ratings = ratings_result.scalars().all()
    backup_data["ratings"] = []
    for r in ratings:
        backup_data["ratings"].append({
            "id": r.id,
            "booking_id": r.booking_id,
            "instructor_id": r.instructor_id,
            "vote": r.vote,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })
    
    # Рефералы
    referrals_result = await db.execute(select(ReferralRecord).order_by(ReferralRecord.created_at.desc()))
    referrals = referrals_result.scalars().all()
    backup_data["referrals"] = []
    for ref in referrals:
        backup_data["referrals"].append({
            "id": ref.id,
            "referrer_client_id": ref.referrer_client_id,
            "referred_client_id": ref.referred_client_id,
            "discount_applied": ref.discount_applied,
            "created_at": ref.created_at.isoformat() if ref.created_at else None,
        })
    
    # Уведомления
    notifications_result = await db.execute(
        select(NotificationSent).options(selectinload(NotificationSent.instructor_rel))
        .order_by(NotificationSent.sent_at.desc())
        .limit(500)
    )
    notifications = notifications_result.scalars().all()
    backup_data["notifications"] = []
    for n in notifications:
        backup_data["notifications"].append({
            "id": n.id,
            "instructor_id": n.instructor_id,
            "instructor_name": n.instructor_rel.name if n.instructor_rel else None,
            "notification_type": n.notification_type,
            "sent_at": n.sent_at.isoformat() if n.sent_at else None,
        })
    
    # Мобильные пользователи
    mobile_users_result = await db.execute(select(MobileUser).order_by(MobileUser.created_at.desc()))
    mobile_users = mobile_users_result.scalars().all()
    backup_data["mobile_users"] = []
    for mu in mobile_users:
        backup_data["mobile_users"].append({
            "id": mu.id,
            "name": mu.name,
            "phone": mu.phone,
            "password_hash": mu.password_hash,
            "referral_code": mu.referral_code,
            "created_at": mu.created_at.isoformat() if mu.created_at else None,
        })

    backup_data["mobile_user_packages"] = [{
        "id": item.id, "user_id": item.user_id, "package_id": item.package_id,
        "remaining_sessions": item.remaining_sessions, "is_active": item.is_active,
        "purchased_at": item.purchased_at.isoformat() if item.purchased_at else None,
    } for item in (await db.execute(select(MobileUserPackage))).scalars().all()]
    backup_data["mobile_app_reviews"] = [{
        "id": item.id, "user_id": item.user_id, "client_id": item.client_id,
        "stars": item.stars, "created_at": item.created_at.isoformat() if item.created_at else None,
    } for item in (await db.execute(select(MobileAppReview))).scalars().all()]
    backup_data["mobile_sessions"] = [{
        "id": item.id, "client_id": item.client_id, "is_active": item.is_active,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "expires_at": item.expires_at.isoformat(),
    } for item in (await db.execute(select(MobileSession))).scalars().all()]
    
    # Мобильные записи
    mobile_bookings_result = await db.execute(
        select(MobileBooking)
        .options(selectinload(MobileBooking.user), selectinload(MobileBooking.instructor))
        .order_by(MobileBooking.booking_date.desc())
    )
    mobile_bookings = mobile_bookings_result.scalars().all()
    backup_data["mobile_bookings"] = []
    for mb in mobile_bookings:
        backup_data["mobile_bookings"].append({
            "id": mb.id,
            "user_id": mb.user_id,
            "user_name": mb.user.name if mb.user else None,
            "instructor_id": mb.instructor_id,
            "instructor_name": mb.instructor.name if mb.instructor else None,
            "booking_date": str(mb.booking_date),
            "start_time": str(mb.start_time),
            "end_time": str(mb.end_time) if mb.end_time else None,
            "service_type": mb.service_type,
            "transmission": mb.transmission,
            "location": mb.location,
            "status": mb.status,
            "price": mb.price,
            "rating_vote": mb.rating_vote,
            "created_at": mb.created_at.isoformat() if mb.created_at else None,
        })
    
    # Сообщения поддержки
    support_messages_result = await db.execute(
        select(SupportMessage).order_by(SupportMessage.created_at.desc()).limit(1000)
    )
    support_messages = support_messages_result.scalars().all()
    backup_data["support_messages"] = []
    for sm in support_messages:
        backup_data["support_messages"].append({
            "id": sm.id,
            "user_id": sm.user_id,
            "client_id": sm.client_id,
            "instructor_id": sm.instructor_id,
            "channel": sm.channel,
            "sender": sm.sender,
            "text": sm.text,
            "is_read": sm.is_read,
            "is_admin_read": sm.is_admin_read,
            "offline_operation_id": sm.offline_operation_id,
            "created_at": sm.created_at.isoformat() if sm.created_at else None,
        })
    
    # Журнал текущего дня. Исторические строки сохраняются в archived_logs.
    audit_result = await db.execute(
        select(AuditLog).order_by(AuditLog.created_at.desc())
    )
    audit_logs = audit_result.scalars().all()
    backup_data["audit_logs"] = []
    for log in audit_logs:
        backup_data["audit_logs"].append({
            "id": log.id,
            "admin_username": log.admin_username,
            "action": log.action,
            "details": log.details,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        })
    
    # Создаем JSON с красивым форматированием
    json_output = json.dumps(backup_data, ensure_ascii=False, indent=2)
    
    # Формируем имя файла с датой и временем
    now = datetime.now(KZ_TZ)
    
    await _audit(db, username, "full_backup_export", f"Создана полная резервная копия")
    
    # Если запрошен JSON формат
    if format == "json":
        filename = f"nomad_backup_{now.strftime('%Y-%m-%d_%H-%M-%S')}.json"
        return StreamingResponse(
            iter([json_output]),
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    
    # По умолчанию возвращаем HTML с интерактивным просмотрщиком
    filename = f"nomad_backup_{now.strftime('%Y-%m-%d_%H-%M-%S')}.html"
    
    html_content = f"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Резервная копия NOMAD - {now.strftime('%Y-%m-%d %H:%M:%S')}</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            padding: 20px;
            color: #333;
        }}
        .container {{
            max-width: 1400px;
            margin: 0 auto;
            background: white;
            border-radius: 16px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            overflow: hidden;
        }}
        .header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 32px;
            text-align: center;
        }}
        .header h1 {{
            font-size: 32px;
            margin-bottom: 8px;
            font-weight: 700;
        }}
        .header p {{
            font-size: 16px;
            opacity: 0.9;
        }}
        .stats {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px;
            padding: 24px;
            background: #f8f9fa;
            border-bottom: 2px solid #e9ecef;
        }}
        .stat-card {{
            background: white;
            padding: 20px;
            border-radius: 12px;
            text-align: center;
            box-shadow: 0 2px 8px rgba(0,0,0,0.08);
            transition: transform 0.2s, box-shadow 0.2s;
        }}
        .stat-card:hover {{
            transform: translateY(-4px);
            box-shadow: 0 8px 24px rgba(0,0,0,0.12);
        }}
        .stat-value {{
            font-size: 36px;
            font-weight: 700;
            color: #667eea;
            margin-bottom: 8px;
        }}
        .stat-label {{
            font-size: 14px;
            color: #6c757d;
            font-weight: 500;
        }}
        .tabs {{
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            padding: 24px;
            background: #f8f9fa;
            border-bottom: 2px solid #e9ecef;
            position: sticky;
            top: 0;
            z-index: 100;
        }}
        .tab {{
            padding: 12px 24px;
            background: white;
            border: 2px solid #e9ecef;
            border-radius: 8px;
            cursor: pointer;
            font-weight: 600;
            transition: all 0.2s;
            font-size: 14px;
        }}
        .tab:hover {{
            border-color: #667eea;
            color: #667eea;
        }}
        .tab.active {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border-color: #667eea;
        }}
        .tab-content {{
            display: none;
            padding: 24px;
        }}
        .tab-content.active {{
            display: block;
        }}
        .search-box {{
            width: 100%;
            padding: 16px;
            font-size: 16px;
            border: 2px solid #e9ecef;
            border-radius: 8px;
            margin-bottom: 20px;
            transition: border-color 0.2s;
        }}
        .search-box:focus {{
            outline: none;
            border-color: #667eea;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            background: white;
            border-radius: 8px;
            overflow: hidden;
            box-shadow: 0 2px 8px rgba(0,0,0,0.08);
            table-layout: auto;
        }}
        thead {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
        }}
        th {{
            padding: 12px 8px;
            text-align: left;
            font-weight: 600;
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 0.3px;
            white-space: nowrap;
            vertical-align: middle;
        }}
        td {{
            padding: 12px 8px;
            border-bottom: 1px solid #f1f3f5;
            font-size: 13px;
            vertical-align: middle;
            max-width: 300px;
            overflow: hidden;
            text-overflow: ellipsis;
        }}
        td.compact {{
            white-space: nowrap;
        }}
        tr:hover {{
            background: #f8f9fa;
        }}
        tr:last-child td {{
            border-bottom: none;
        }}
        .table-wrapper {{
            overflow-x: auto;
            -webkit-overflow-scrolling: touch;
        }}
        .badge {{
            display: inline-block;
            padding: 4px 12px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: 600;
            text-transform: uppercase;
        }}
        .badge-success {{ background: #d4edda; color: #155724; }}
        .badge-warning {{ background: #fff3cd; color: #856404; }}
        .badge-danger {{ background: #f8d7da; color: #721c24; }}
        .badge-info {{ background: #d1ecf1; color: #0c5460; }}
        .badge-primary {{ background: #d6d8f5; color: #4c51bf; }}
        .empty-state {{
            text-align: center;
            padding: 60px 20px;
            color: #6c757d;
        }}
        .empty-state-icon {{
            font-size: 64px;
            margin-bottom: 16px;
            opacity: 0.5;
        }}
        .actions {{
            padding: 24px;
            background: #f8f9fa;
            border-top: 2px solid #e9ecef;
            display: flex;
            gap: 12px;
            flex-wrap: wrap;
        }}
        .btn {{
            padding: 12px 24px;
            border: none;
            border-radius: 8px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
            font-size: 14px;
        }}
        .btn-primary {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
        }}
        .btn-primary:hover {{
            transform: translateY(-2px);
            box-shadow: 0 8px 16px rgba(102, 126, 234, 0.4);
        }}
        .btn-secondary {{
            background: white;
            color: #667eea;
            border: 2px solid #667eea;
        }}
        .btn-secondary:hover {{
            background: #667eea;
            color: white;
        }}
        @media (max-width: 768px) {{
            .stats {{
                grid-template-columns: repeat(2, 1fr);
            }}
            .tabs {{
                padding: 16px;
            }}
            .tab {{
                padding: 10px 16px;
                font-size: 13px;
            }}
            .table-wrapper {{ overflow: visible; }}
            table, thead, tbody, tr, th, td {{ display: block; width: 100%; }}
            thead {{ display: none; }}
            table {{ font-size: 13px; }}
            tr {{ background: #fff; border: 1px solid #e9ecef; border-radius: 10px; padding: 8px 12px; margin-bottom: 10px; box-shadow: 0 2px 5px rgba(0,0,0,.03); }}
            td {{ display: grid; grid-template-columns: minmax(105px, 42%) 1fr; gap: 8px; padding: 7px 0; border-bottom: 1px solid #f0f1f3; overflow-wrap: anywhere; text-align: right; }}
            td:last-child {{ border-bottom: 0; }}
            td::before {{ content: attr(data-label); color: #6c757d; font-weight: 600; text-align: left; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📦 Резервная копия NOMAD</h1>
            <p>Дата создания: {now.strftime('%d.%m.%Y %H:%M:%S')} | Создал: {username}</p>
        </div>
        
        <div class="stats">
            <div class="stat-card">
                <div class="stat-value" id="stat-clients">0</div>
                <div class="stat-label">Клиентов</div>
            </div>
            <div class="stat-card">
                <div class="stat-value" id="stat-bookings">0</div>
                <div class="stat-label">Записей</div>
            </div>
            <div class="stat-card">
                <div class="stat-value" id="stat-instructors">0</div>
                <div class="stat-label">Инструкторов</div>
            </div>
            <div class="stat-card">
                <div class="stat-value" id="stat-certificates">0</div>
                <div class="stat-label">Сертификатов</div>
            </div>
            <div class="stat-card">
                <div class="stat-value" id="stat-mobile-users">0</div>
                <div class="stat-label">Моб. пользователей</div>
            </div>
            <div class="stat-card">
                <div class="stat-value" id="stat-mobile-bookings">0</div>
                <div class="stat-label">Моб. записей</div>
            </div>
        </div>
        
        <div class="tabs" id="tabs"></div>
        
        <div id="tab-contents"></div>
        
        <div class="actions">
            <button class="btn btn-primary" onclick="downloadJSON()">📥 Скачать JSON</button>
            <button class="btn btn-secondary" onclick="printPage()">🖨️ Печать</button>
        </div>
    </div>

    <script>
        const backupData = {json_output};
        
        // Обновляем статистику
        document.getElementById('stat-clients').textContent = backupData.clients?.length || 0;
        document.getElementById('stat-bookings').textContent = backupData.bookings?.length || 0;
        document.getElementById('stat-instructors').textContent = backupData.instructors?.length || 0;
        document.getElementById('stat-certificates').textContent = backupData.certificates?.length || 0;
        document.getElementById('stat-mobile-users').textContent = backupData.mobile_users?.length || 0;
        document.getElementById('stat-mobile-bookings').textContent = backupData.mobile_bookings?.length || 0;
        
        const sections = [
            {{ key: 'clients', title: '👤 Клиенты', icon: '👤' }},
            {{ key: 'bookings', title: '📋 Записи', icon: '📋' }},
            {{ key: 'instructors', title: '👨‍🏫 Инструкторы', icon: '👨‍🏫' }},
            {{ key: 'instructor_daily_schedules', title: '🗓️ Графики инструкторов', icon: '🗓️' }},
            {{ key: 'instructor_days_off', title: '🏖️ Выходные инструкторов', icon: '🏖️' }},
            {{ key: 'instructor_rotations', title: '🔄 Ротация инструкторов', icon: '🔄' }},
            {{ key: 'certificates', title: '🎟️ Сертификаты', icon: '🎟️' }},
            {{ key: 'certificate_requests', title: '📝 Заявки сертификатов', icon: '📝' }},
            {{ key: 'packages', title: '📦 Пакеты', icon: '📦' }},
            {{ key: 'client_packages', title: '📦 Пакеты клиентов', icon: '📦' }},
            {{ key: 'faq', title: '❓ FAQ', icon: '❓' }},
            {{ key: 'ratings', title: '⭐ Рейтинги', icon: '⭐' }},
            {{ key: 'referrals', title: '🎁 Рефералы', icon: '🎁' }},
            {{ key: 'notifications', title: '🔔 Уведомления', icon: '🔔' }},
            {{ key: 'mobile_users', title: '📱 Моб. пользователи', icon: '📱' }},
            {{ key: 'mobile_bookings', title: '📱 Моб. записи', icon: '📱' }},
            {{ key: 'support_messages', title: '💬 Сообщения поддержки', icon: '💬' }},
            {{ key: 'events', title: '⚡ События', icon: '⚡' }},
            {{ key: 'audit_logs', title: '📜 Журнал действий', icon: '📜' }},
            {{ key: 'archived_logs', title: '🗃️ Архив логов', icon: '🗃️' }}
        ];
        
        // Создаем вкладки
        const tabsContainer = document.getElementById('tabs');
        const contentsContainer = document.getElementById('tab-contents');
        
        sections.forEach((section, index) => {{
            const data = backupData[section.key] || [];
            
            // Создаем кнопку вкладки
            const tab = document.createElement('button');
            tab.className = 'tab' + (index === 0 ? ' active' : '');
            tab.textContent = `${{section.icon}} ${{section.title}} (${{data.length}})`;
            tab.onclick = () => switchTab(section.key);
            tabsContainer.appendChild(tab);
            
            // Создаем контент вкладки
            const content = document.createElement('div');
            content.id = `content-${{section.key}}`;
            content.className = 'tab-content' + (index === 0 ? ' active' : '');
            
            if (data.length === 0) {{
                content.innerHTML = `
                    <div class="empty-state">
                        <div class="empty-state-icon">${{section.icon}}</div>
                        <p>Нет данных в этом разделе</p>
                    </div>
                `;
            }} else {{
                // Поиск
                const searchId = `search-${{section.key}}`;
                content.innerHTML = `<input type="text" class="search-box" id="${{searchId}}" placeholder="🔍 Поиск по таблице...">`;
                
                // Обертка для горизонтального скролла
                const wrapper = document.createElement('div');
                wrapper.className = 'table-wrapper';
                
                // Создаем таблицу
                const table = document.createElement('table');
                table.id = `table-${{section.key}}`;
                
                // Заголовки
                const thead = document.createElement('thead');
                const headerRow = document.createElement('tr');
                const keys = Object.keys(data[0]);
                keys.forEach(key => {{
                    const th = document.createElement('th');
                    th.textContent = formatHeader(key);
                    headerRow.appendChild(th);
                }});
                thead.appendChild(headerRow);
                table.appendChild(thead);
                
                // Данные
                const tbody = document.createElement('tbody');
                data.forEach(item => {{
                    const row = document.createElement('tr');
                    keys.forEach(key => {{
                        const td = document.createElement('td');
                        // Добавляем класс для компактных колонок
                        if (isCompactColumn(key)) {{
                            td.className = 'compact';
                        }}
                        td.dataset.label = formatHeader(key);
                        td.innerHTML = formatValue(key, item[key]);
                        row.appendChild(td);
                    }});
                    tbody.appendChild(row);
                }});
                table.appendChild(tbody);
                
                wrapper.appendChild(table);
                content.appendChild(wrapper);
                
                // Обработчик поиска
                setTimeout(() => {{
                    const searchBox = document.getElementById(searchId);
                    if (searchBox) {{
                        searchBox.addEventListener('input', (e) => {{
                            filterTable(`table-${{section.key}}`, e.target.value);
                        }});
                    }}
                }}, 100);
            }}
            
            contentsContainer.appendChild(content);
        }});
        
        function switchTab(key) {{
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            
            event.target.classList.add('active');
            document.getElementById(`content-${{key}}`).classList.add('active');
        }}
        
        function isCompactColumn(key) {{
            // Колонки которые должны быть компактными (не переносятся)
            const compactKeys = [
                'id', 'price', 'nominal', 'remaining', 'bookings_count',
                'sessions_count', 'remaining_sessions', 'experience_years',
                'rating', 'is_active', 'is_used', 'is_read', 'vote',
                'status', 'gender', 'transmission', 'service_type'
            ];
            return compactKeys.includes(key);
        }}
        
        function formatHeader(key) {{
            const headers = {{
                'id': 'ID',
                'name': 'Имя',
                'phone': 'Телефон',
                'telegram_id': 'Telegram ID',
                'telegram_username': 'Telegram',
                'referral_code': 'Реф. код',
                'bookings_count': 'Записей',
                'created_at': 'Создано',
                'booking_date': 'Дата',
                'start_time': 'Начало',
                'end_time': 'Конец',
                'client_name': 'Клиент',
                'client_phone': 'Телефон клиента',
                'instructor_name': 'Инструктор',
                'service_type': 'Услуга',
                'transmission': 'КПП',
                'location': 'Площадка',
                'status': 'Статус',
                'price': 'Цена',
                'code': 'Код',
                'nominal': 'Номинал',
                'remaining': 'Остаток',
                'is_used': 'Использован',
                'question': 'Вопрос',
                'answer': 'Ответ',
                'vote': 'Оценка',
                'notification_type': 'Тип',
                'text': 'Текст',
                'sender': 'Отправитель',
                'is_read': 'Прочитано',
                'action': 'Действие',
                'details': 'Детали',
                'admin_username': 'Администратор',
                'rating': 'Рейтинг',
                'experience_years': 'Стаж (лет)',
                'gender': 'Пол',
                'is_active': 'Активен',
                'sessions_count': 'Занятий',
                'remaining_sessions': 'Осталось',
                'package_name': 'Пакет',
                'user_name': 'Пользователь',
                'activated_by_client_name': 'Активирован клиентом'
            }};
            return headers[key] || key.replace(/_/g, ' ').replace(/\\b\\w/g, l => l.toUpperCase());
        }}
        
        function formatValue(key, value) {{
            if (value === null || value === undefined) return '<span style="color:#adb5bd">—</span>';
            if (value === true) return '<span class="badge badge-success">Да</span>';
            if (value === false) return '<span class="badge badge-danger">Нет</span>';
            
            // Статусы
            if (key === 'status') {{
                const badges = {{
                    'planned': 'warning',
                    'confirmed': 'success',
                    'completed': 'success',
                    'cancelled': 'danger',
                    'no_show': 'danger',
                    'in_progress': 'info'
                }};
                const labels = {{
                    'planned': 'Запланирована',
                    'confirmed': 'Подтверждена',
                    'completed': 'Завершена',
                    'cancelled': 'Отменена',
                    'no_show': 'Не явился',
                    'in_progress': 'В процессе'
                }};
                return `<span class="badge badge-${{badges[value] || 'info'}}">${{labels[value] || value}}</span>`;
            }}
            
            // Оценки
            if (key === 'vote') {{
                const badges = {{ 'good': 'success', 'normal': 'warning', 'bad': 'danger' }};
                const labels = {{ 'good': '👍 Хорошо', 'normal': '👌 Нормально', 'bad': '👎 Плохо' }};
                return `<span class="badge badge-${{badges[value]}}">${{labels[value] || value}}</span>`;
            }}
            
            // Цена
            if (key.includes('price') || key === 'nominal' || key === 'remaining') {{
                return `<strong>${{value.toLocaleString('ru-RU')}} ₸</strong>`;
            }}
            
            // Даты
            if (key.includes('_at') || key.includes('date')) {{
                try {{
                    const date = new Date(value);
                    return date.toLocaleString('ru-RU');
                }} catch {{
                    return value;
                }}
            }}
            
            // Длинный текст
            if (typeof value === 'string' && value.length > 100) {{
                const short = value.substring(0, 80);
                return `<span title="${{value.replace(/"/g, '&quot;')}}">${{short}}...</span>`;
            }}
            
            return value;
        }}
        
        function filterTable(tableId, searchText) {{
            const table = document.getElementById(tableId);
            if (!table) return;
            
            const rows = table.querySelectorAll('tbody tr');
            const search = searchText.toLowerCase();
            
            rows.forEach(row => {{
                const text = row.textContent.toLowerCase();
                row.style.display = text.includes(search) ? '' : 'none';
            }});
        }}
        
        function downloadJSON() {{
            const blob = new Blob([JSON.stringify(backupData, null, 2)], {{ type: 'application/json' }});
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'nomad_backup_{now.strftime('%Y-%m-%d_%H-%M-%S')}.json';
            a.click();
            URL.revokeObjectURL(url);
        }}
        
        function printPage() {{
            window.print();
        }}
    </script>
</body>
</html>"""
    
    return StreamingResponse(
        iter([html_content]),
        media_type="text/html",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def _validate_backup_booking_conflicts(backup_data: dict) -> None:
    """Reject an import with overlapping active bookings before it changes data."""
    active_statuses = {"pending", "cancellation_pending", "reschedule_pending", "planned", "confirmed", "in_progress"}
    intervals: list[tuple[object, date, time, time]] = []
    vehicle_intervals: list[tuple[object, date, time, time]] = []

    for collection_name in ("bookings", "mobile_bookings"):
        rows = backup_data.get(collection_name, [])
        if rows is None:
            continue
        if not isinstance(rows, list):
            raise HTTPException(status_code=400, detail=f"Поле {collection_name} должно быть списком")

        for row_number, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                raise HTTPException(status_code=400, detail=f"Некорректная запись #{row_number} в {collection_name}")
            if str(row.get("status") or "planned") not in active_statuses:
                continue
            try:
                instructor_id = row["instructor_id"]
                booking_date = date.fromisoformat(str(row["booking_date"]))
                start = time.fromisoformat(str(row["start_time"]))
                raw_end = row.get("end_time")
                if raw_end:
                    end = time.fromisoformat(str(raw_end))
                else:
                    duration_minutes = (
                        settings.EXAM_DURATION_MINUTES
                        if row.get("service_type") == "exam"
                        else settings.TRAINING_DURATION_MINUTES
                    )
                    end = (datetime.combine(booking_date, start) + timedelta(minutes=duration_minutes)).time()
            except (KeyError, TypeError, ValueError):
                raise HTTPException(
                    status_code=400,
                    detail=f"Некорректные дата или время в записи #{row_number} ({collection_name})",
                )
            if end <= start:
                raise HTTPException(
                    status_code=400,
                    detail=f"В записи #{row_number} ({collection_name}) время окончания должно быть позже начала",
                )
            intervals.append((instructor_id, booking_date, start, end))
            if collection_name == "bookings" and row.get("vehicle_id") is not None:
                vehicle_intervals.append((row["vehicle_id"], booking_date, start, end))

    intervals.sort(key=lambda interval: (str(interval[0]), interval[1], interval[2], interval[3]))
    latest_end_by_instructor_day: dict[tuple[str, date], time] = {}
    for instructor_id, booking_date, start, end in intervals:
        key = (str(instructor_id), booking_date)
        latest_end = latest_end_by_instructor_day.get(key)
        if latest_end and start < latest_end:
            raise HTTPException(
                status_code=400,
                detail=(
                    "В резервной копии есть пересекающиеся записи одного инструктора: "
                    f"инструктор #{instructor_id}, {booking_date.isoformat()}, {start.strftime('%H:%M')}"
                ),
            )
        if not latest_end or end > latest_end:
            latest_end_by_instructor_day[key] = end

    vehicle_intervals.sort(key=lambda interval: (str(interval[0]), interval[1], interval[2], interval[3]))
    latest_end_by_vehicle_day: dict[tuple[str, date], time] = {}
    for vehicle_id, booking_date, start, end in vehicle_intervals:
        key = (str(vehicle_id), booking_date)
        latest_end = latest_end_by_vehicle_day.get(key)
        if latest_end and start < latest_end:
            raise HTTPException(
                status_code=400,
                detail=(
                    "В резервной копии есть пересекающиеся записи одной машины: "
                    f"машина #{vehicle_id}, {booking_date.isoformat()}, {start.strftime('%H:%M')}"
                ),
            )
        if not latest_end or end > latest_end:
            latest_end_by_vehicle_day[key] = end


@router.post("/import/full-backup")
async def import_full_backup(request: Request, db: AsyncSession = Depends(get_db)):
    """Восстановление всей базы данных из резервной копии"""
    username = _get_admin_username(request)
    
    try:
        # Получаем JSON данные из тела запроса
        backup_data = await request.json()
        
        if not backup_data or 'backup_date' not in backup_data:
            raise HTTPException(status_code=400, detail="Неверный формат резервной копии")

        # Validate before deleting anything.  A malformed or conflicting
        # backup must leave the current database untouched.
        _validate_backup_booking_conflicts(backup_data)
        
        import json
        from sqlalchemy import delete as sa_delete, text
        
        # Статистика для отчёта
        stats = {
            "clients": 0,
            "instructors": 0,
            "bookings": 0,
            "vehicles": 0,
            "certificates": 0,
            "packages": 0,
            "client_packages": 0,
            "faq": 0,
            "ratings": 0,
            "referrals": 0,
            "mobile_users": 0,
            "mobile_bookings": 0,
            "support_messages": 0,
            "waiting_list": 0,
            "client_blocks": 0,
        }
        
        # ВАЖНО: Удаляем все данные в правильном порядке (с учётом foreign keys)
        await db.execute(sa_delete(RatingRecord))
        await db.execute(sa_delete(MobileAppReview))
        await db.execute(sa_delete(MobileUserPackage))
        await db.execute(sa_delete(CertificateRequest))
        await db.execute(sa_delete(ArchivedLog))
        await db.execute(sa_delete(Event))
        await db.execute(sa_delete(InstructorRotation))
        await db.execute(sa_delete(ClientBlock))
        await db.execute(sa_delete(WaitingListEntry))
        await db.execute(sa_delete(AuditLog))
        await db.execute(sa_delete(NotificationSent))
        await db.execute(sa_delete(SupportMessage))
        await db.execute(sa_delete(ReferralRecord))
        await db.execute(sa_delete(ClientPackage))
        await db.execute(sa_delete(Booking))
        await db.execute(sa_delete(Vehicle))
        await db.execute(sa_delete(MobileBooking))
        await db.execute(sa_delete(Certificate))
        await db.execute(sa_delete(Package))
        await db.execute(sa_delete(FAQItem))
        await db.execute(sa_delete(Client))
        await db.execute(sa_delete(MobileUser))
        await db.execute(sa_delete(MobileSession))
        await db.execute(sa_delete(InstructorDailySchedule))
        await db.execute(sa_delete(InstructorDayOff))
        await db.execute(sa_delete(Instructor))
        await db.execute(sa_delete(AdminState))
        await db.execute(sa_delete(GenderAnalytics))
        # Журнал восстанавливается ниже из самого снимка.
        
        # Маппинг старых ID на новые (для сохранения связей)
        client_id_map = {}
        instructor_id_map = {}
        package_id_map = {}
        booking_id_map = {}
        certificate_id_map = {}
        vehicle_id_map = {}
        
        # Восстанавливаем инструкторов
        if backup_data.get('instructors'):
            for inst_data in backup_data['instructors']:
                old_id = inst_data['id']
                instructor = Instructor(
                    name=inst_data['name'],
                    phone=inst_data.get('phone'),
                    telegram_id=inst_data.get('telegram_id'),
                    telegram_username=inst_data.get('telegram_username'),
                    transmission=inst_data.get('transmission', 'both'),
                    lesson_type=inst_data.get('lesson_type', 'both'),
                    gender=inst_data.get('gender', 'any'),
                    experience_years=inst_data.get('experience_years', 0),
                    rating=inst_data.get('rating', 5.0),
                    is_active=inst_data.get('is_active', True),
                    is_duty=inst_data.get('is_duty', False),
                    is_lead=inst_data.get('is_lead', False),
                    working_hours_start=time.fromisoformat(inst_data['working_hours_start']) if inst_data.get('working_hours_start') else time(9, 0),
                    working_hours_end=time.fromisoformat(inst_data['working_hours_end']) if inst_data.get('working_hours_end') else time(19, 0),
                    lunch_start=time.fromisoformat(inst_data['lunch_start']) if inst_data.get('lunch_start') else None,
                    lunch_end=time.fromisoformat(inst_data['lunch_end']) if inst_data.get('lunch_end') else None,
                    days_off=inst_data.get('days_off', ''),
                    description=inst_data.get('description'),
                    avatar_url=inst_data.get('avatar_url'),
                    offline_operation_id=inst_data.get('offline_operation_id'),
                    created_at=datetime.fromisoformat(inst_data['created_at']) if inst_data.get('created_at') else now_kz(),
                )
                db.add(instructor)
                await db.flush()
                instructor_id_map[old_id] = instructor.id
                stats['instructors'] += 1

        # Backups before format 3 had no fleet. Restore the documented default
        # instead of leaving every later booking without a compatible car.
        vehicle_rows = backup_data.get('vehicles')
        if vehicle_rows is None:
            vehicle_rows = [
                {"id": 1, "name": "Машина 1", "transmission": "manual"},
                *[{"id": number, "name": f"Машина {number}", "transmission": "automatic"} for number in range(2, 7)],
            ]
        if not isinstance(vehicle_rows, list):
            raise HTTPException(status_code=400, detail="Поле vehicles должно быть списком")
        for vehicle_data in vehicle_rows:
            vehicle = Vehicle(
                name=vehicle_data['name'], transmission=vehicle_data['transmission'],
                is_under_repair=bool(vehicle_data.get('is_under_repair', False)),
                created_at=datetime.fromisoformat(vehicle_data['created_at']) if vehicle_data.get('created_at') else now_kz(),
            )
            db.add(vehicle)
            await db.flush()
            vehicle_id_map[vehicle_data.get('id')] = vehicle.id
            stats['vehicles'] += 1
        
        # Восстанавливаем клиентов
        if backup_data.get('clients'):
            for client_data in backup_data['clients']:
                old_id = client_data['id']
                
                # Обработка referred_by_client_id - может ссылаться на ещё не созданного клиента
                referred_by = None
                if client_data.get('referred_by_client_id'):
                    referred_by = client_id_map.get(client_data['referred_by_client_id'])
                
                client = Client(
                    name=client_data['name'],
                    phone=client_data.get('phone'),
                    password_hash=client_data.get('password_hash'),
                    avatar_url=client_data.get('avatar_url'),
                    is_deleted=bool(client_data.get('is_deleted', False)),
                    telegram_id=client_data.get('telegram_id'),
                    referral_code=client_data.get('referral_code', secrets.token_hex(4).upper()),
                    referral_discount_available=client_data.get('referral_discount_available', False),
                    referred_by_client_id=referred_by,
                    offline_operation_id=client_data.get('offline_operation_id'),
                    reschedule_count_24h=client_data.get('reschedule_count_24h', 0),
                    reschedule_window_started_at=datetime.fromisoformat(client_data['reschedule_window_started_at']) if client_data.get('reschedule_window_started_at') else None,
                    support_chat_opened_at=datetime.fromisoformat(client_data['support_chat_opened_at']) if client_data.get('support_chat_opened_at') else None,
                    support_chat_closed_at=datetime.fromisoformat(client_data['support_chat_closed_at']) if client_data.get('support_chat_closed_at') else None,
                    created_at=datetime.fromisoformat(client_data['created_at']) if client_data.get('created_at') else now_kz(),
                )
                db.add(client)
                await db.flush()
                client_id_map[old_id] = client.id
                stats['clients'] += 1
        
        # Второй проход для обновления referred_by_client_id
        if backup_data.get('clients'):
            for client_data in backup_data['clients']:
                if client_data.get('referred_by_client_id'):
                    old_referrer_id = client_data['referred_by_client_id']
                    if old_referrer_id in client_id_map:
                        new_client_id = client_id_map[client_data['id']]
                        new_referrer_id = client_id_map[old_referrer_id]
                        result = await db.execute(select(Client).where(Client.id == new_client_id))
                        client = result.scalar_one_or_none()
                        if client:
                            client.referred_by_client_id = new_referrer_id
        
        # Восстанавливаем пакеты
        if backup_data.get('packages'):
            for pkg_data in backup_data['packages']:
                old_id = pkg_data['id']
                package = Package(
                    name=pkg_data['name'],
                    sessions_count=pkg_data['sessions_count'],
                    price=pkg_data['price'],
                    description=pkg_data.get('description'),
                    validity_days=pkg_data.get('validity_days', 30),
                    bonus_exam=pkg_data.get('bonus_exam', False),
                    code=pkg_data.get('code'),
                    is_active=pkg_data.get('is_active', True),
                    offline_operation_id=pkg_data.get('offline_operation_id'),
                    created_at=datetime.fromisoformat(pkg_data['created_at']) if pkg_data.get('created_at') else now_kz(),
                )
                db.add(package)
                await db.flush()
                package_id_map[old_id] = package.id
                stats['packages'] += 1
        
        # Восстанавливаем сертификаты
        if backup_data.get('certificates'):
            for cert_data in backup_data['certificates']:
                activated_by = None
                if cert_data.get('activated_by_client_id'):
                    activated_by = client_id_map.get(cert_data['activated_by_client_id'])
                
                used_by = None
                if cert_data.get('used_by_user_id'):
                    used_by = client_id_map.get(cert_data['used_by_user_id'])
                
                certificate = Certificate(
                    code=cert_data['code'],
                    nominal=cert_data['nominal'],
                    remaining=cert_data['remaining'],
                    is_used=cert_data.get('is_used', False),
                    activated_by_client_id=activated_by,
                    used_by_user_id=used_by,
                    used_at=datetime.fromisoformat(cert_data['used_at']) if cert_data.get('used_at') else None,
                    offline_operation_id=cert_data.get('offline_operation_id'),
                    created_at=datetime.fromisoformat(cert_data['created_at']) if cert_data.get('created_at') else now_kz(),
                )
                db.add(certificate)
                await db.flush()
                certificate_id_map[cert_data['id']] = certificate.id
                stats['certificates'] += 1
        
        # Восстанавливаем записи (Bookings)
        if backup_data.get('bookings'):
            for booking_data in backup_data['bookings']:
                old_id = booking_data['id']
                
                client_id = client_id_map.get(booking_data['client_id'])
                instructor_id = instructor_id_map.get(booking_data['instructor_id'])
                
                if not client_id or not instructor_id:
                    continue  # Пропускаем если клиент или инструктор не найдены
                
                booking = Booking(
                    client_id=client_id,
                    instructor_id=instructor_id,
                    vehicle_id=vehicle_id_map.get(booking_data.get('vehicle_id')),
                    service_type=ServiceType(booking_data['service_type']) if booking_data.get('service_type') else ServiceType.TRAINING,
                    transmission=booking_data.get('transmission', 'automatic'),
                    location=booking_data.get('location', settings.LOCATION_MAIN),
                    booking_date=date.fromisoformat(booking_data['booking_date']),
                    start_time=time.fromisoformat(booking_data['start_time']),
                    end_time=time.fromisoformat(booking_data['end_time']),
                    status=booking_data.get('status', 'planned'),
                    price=booking_data.get('price', 0),
                    base_price=booking_data.get('base_price'),
                    certificate_amount=booking_data.get('certificate_amount'),
                    referral_discount_amount=booking_data.get('referral_discount_amount'),
                    payment_status=booking_data.get('payment_status', 'unpaid'),
                    paid_amount=booking_data.get('paid_amount'),
                    paid_at=datetime.fromisoformat(booking_data['paid_at']) if booking_data.get('paid_at') else None,
                    source=booking_data.get('source', 'manual'),
                    package_id=package_id_map.get(booking_data['package_id']) if booking_data.get('package_id') else None,
                    certificate_id=certificate_id_map.get(booking_data['certificate_id']) if booking_data.get('certificate_id') else None,
                    package_bonus_exam_used=booking_data.get('package_bonus_exam_used', False),
                    cancellation_previous_status=booking_data.get('cancellation_previous_status'),
                    confirmation_sent=booking_data.get('confirmation_sent', False),
                    confirmed_by_client=booking_data.get('confirmed_by_client', False),
                    admin_confirmed=booking_data.get('admin_confirmed', False),
                    admin_confirmed_at=datetime.fromisoformat(booking_data['admin_confirmed_at']) if booking_data.get('admin_confirmed_at') else None,
                    conflict_reason=booking_data.get('conflict_reason'),
                    rating_sent=booking_data.get('rating_sent', False),
                    reminder_24h_sent=booking_data.get('reminder_24h_sent', False),
                    reminder_1h_sent=booking_data.get('reminder_1h_sent', False),
                    reminder_10min_sent=booking_data.get('reminder_10min_sent', False),
                    admin_viewed=booking_data.get('admin_viewed', True),
                    booking_number=booking_data.get('booking_number'),
                    offline_operation_id=booking_data.get('offline_operation_id'),
                    reschedule_previous_status=booking_data.get('reschedule_previous_status'),
                    requested_reschedule_date=date.fromisoformat(booking_data['requested_reschedule_date']) if booking_data.get('requested_reschedule_date') else None,
                    requested_reschedule_start_time=time.fromisoformat(booking_data['requested_reschedule_start_time']) if booking_data.get('requested_reschedule_start_time') else None,
                    requested_reschedule_end_time=time.fromisoformat(booking_data['requested_reschedule_end_time']) if booking_data.get('requested_reschedule_end_time') else None,
                    reschedule_requested_at=datetime.fromisoformat(booking_data['reschedule_requested_at']) if booking_data.get('reschedule_requested_at') else None,
                    completed_at=datetime.fromisoformat(booking_data['completed_at']) if booking_data.get('completed_at') else None,
                    archived_at=datetime.fromisoformat(booking_data['archived_at']) if booking_data.get('archived_at') else None,
                    created_at=datetime.fromisoformat(booking_data['created_at']) if booking_data.get('created_at') else now_kz(),
                )
                db.add(booking)
                await db.flush()
                booking_id_map[old_id] = booking.id
                stats['bookings'] += 1
        
        # Восстанавливаем пакеты клиентов
        if backup_data.get('client_packages'):
            for cp_data in backup_data['client_packages']:
                client_id = client_id_map.get(cp_data['client_id'])
                package_id = package_id_map.get(cp_data['package_id'])
                
                if not client_id or not package_id:
                    continue
                
                client_package = ClientPackage(
                    client_id=client_id,
                    package_id=package_id,
                    remaining_sessions=cp_data['remaining_sessions'],
                    is_active=cp_data.get('is_active', True),
                    purchased_at=datetime.fromisoformat(cp_data['purchased_at']) if cp_data.get('purchased_at') else datetime.utcnow(),
                    expires_at=datetime.fromisoformat(cp_data['expires_at']) if cp_data.get('expires_at') else None,
                    remaining_bonus_exams=cp_data.get('remaining_bonus_exams', 0),
                )
                db.add(client_package)
                stats['client_packages'] += 1

        for item in backup_data.get('instructor_daily_schedules', []):
            instructor_id = instructor_id_map.get(item.get('instructor_id'))
            if instructor_id:
                db.add(InstructorDailySchedule(instructor_id=instructor_id, schedule_date=date.fromisoformat(item['schedule_date']),
                    is_day_off=item.get('is_day_off', False),
                    working_hours_start=time.fromisoformat(item['working_hours_start']) if item.get('working_hours_start') else None,
                    working_hours_end=time.fromisoformat(item['working_hours_end']) if item.get('working_hours_end') else None,
                    lunch_start=time.fromisoformat(item['lunch_start']) if item.get('lunch_start') else None,
                    lunch_end=time.fromisoformat(item['lunch_end']) if item.get('lunch_end') else None,
                    created_at=datetime.fromisoformat(item['created_at']) if item.get('created_at') else now_kz()))
        for item in backup_data.get('instructor_days_off', []):
            instructor_id = instructor_id_map.get(item.get('instructor_id'))
            if instructor_id:
                db.add(InstructorDayOff(instructor_id=instructor_id, day_off_date=date.fromisoformat(item['day_off_date']),
                    created_at=datetime.fromisoformat(item['created_at']) if item.get('created_at') else now_kz()))
        for item in backup_data.get('instructor_rotations', []):
            instructor_id = instructor_id_map.get(item.get('instructor_id'))
            if instructor_id:
                db.add(InstructorRotation(instructor_id=instructor_id,
                    last_booking_date=date.fromisoformat(item['last_booking_date']) if item.get('last_booking_date') else None,
                    last_booking_time=time.fromisoformat(item['last_booking_time']) if item.get('last_booking_time') else None,
                    rotation_count=item.get('rotation_count', 0),
                    updated_at=datetime.fromisoformat(item['updated_at']) if item.get('updated_at') else now_kz()))
        for item in backup_data.get('certificate_requests', []):
            client_id = client_id_map.get(item.get('client_id'))
            if client_id:
                db.add(CertificateRequest(client_id=client_id, code_entered=item['code_entered'],
                    matched_certificate_id=certificate_id_map.get(item.get('matched_certificate_id')),
                    booking_id=booking_id_map.get(item.get('booking_id')), status=item.get('status', 'pending'),
                    created_at=datetime.fromisoformat(item['created_at']) if item.get('created_at') else now_kz()))
        for item in backup_data.get('events', []):
            db.add(Event(event_type=item['event_type'], source=item['source'], message=item['message'],
                client_id=client_id_map.get(item.get('client_id')),
                instructor_id=instructor_id_map.get(item.get('instructor_id')),
                booking_id=booking_id_map.get(item.get('booking_id')),
                created_at=datetime.fromisoformat(item['created_at']) if item.get('created_at') else now_kz()))
        for item in backup_data.get('waiting_list', []):
            db.add(WaitingListEntry(
                name=item['name'], phone=item.get('phone'),
                desired_date=date.fromisoformat(item['desired_date']) if item.get('desired_date') else None,
                desired_time_start=time.fromisoformat(item['desired_time_start']) if item.get('desired_time_start') else None,
                desired_time_end=time.fromisoformat(item['desired_time_end']) if item.get('desired_time_end') else None,
                transmission=item.get('transmission'),
                instructor_id=instructor_id_map.get(item.get('instructor_id')),
                instructor_gender=item.get('instructor_gender'), status=item.get('status', 'waiting'),
                notes=item.get('notes'), offline_operation_id=item.get('offline_operation_id'),
                created_at=datetime.fromisoformat(item['created_at']) if item.get('created_at') else now_kz(),
            ))
            stats['waiting_list'] += 1
        for item in backup_data.get('client_blocks', []):
            client_id = client_id_map.get(item.get('client_id'))
            if client_id:
                db.add(ClientBlock(
                    client_id=client_id, blocked_until=datetime.fromisoformat(item['blocked_until']),
                    reason=item.get('reason'),
                    created_at=datetime.fromisoformat(item['created_at']) if item.get('created_at') else now_kz(),
                ))
                stats['client_blocks'] += 1
        
        # Восстанавливаем FAQ
        if backup_data.get('faq'):
            for faq_data in backup_data['faq']:
                faq = FAQItem(
                    question=faq_data['question'],
                    answer=faq_data['answer'],
                    sort_order=faq_data.get('sort_order', 0),
                    is_active=faq_data.get('is_active', True),
                    offline_operation_id=faq_data.get('offline_operation_id'),
                )
                db.add(faq)
                stats['faq'] += 1
        
        # Восстанавливаем рейтинги
        if backup_data.get('ratings'):
            for rating_data in backup_data['ratings']:
                booking_id = booking_id_map.get(rating_data['booking_id'])
                instructor_id = instructor_id_map.get(rating_data['instructor_id'])
                
                if not booking_id or not instructor_id:
                    continue
                
                rating = RatingRecord(
                    booking_id=booking_id,
                    instructor_id=instructor_id,
                    vote=rating_data['vote'],
                )
                db.add(rating)
                stats['ratings'] += 1
        
        # Восстанавливаем рефералов
        if backup_data.get('referrals'):
            for ref_data in backup_data['referrals']:
                referrer_id = client_id_map.get(ref_data['referrer_client_id'])
                referred_id = client_id_map.get(ref_data['referred_client_id'])
                
                if not referrer_id or not referred_id:
                    continue
                
                referral = ReferralRecord(
                    referrer_client_id=referrer_id,
                    referred_client_id=referred_id,
                    discount_applied=ref_data.get('discount_applied', False),
                )
                db.add(referral)
                stats['referrals'] += 1
        
        # Восстанавливаем мобильных пользователей
        mobile_user_id_map = {}
        if backup_data.get('mobile_users'):
            for mu_data in backup_data['mobile_users']:
                old_id = mu_data['id']
                mobile_user = MobileUser(
                    name=mu_data['name'],
                    phone=mu_data.get('phone'),
                    password_hash=mu_data.get('password_hash') or '',
                    referral_code=mu_data.get('referral_code'),
                    created_at=datetime.fromisoformat(mu_data['created_at']) if mu_data.get('created_at') else now_kz(),
                )
                db.add(mobile_user)
                await db.flush()
                mobile_user_id_map[old_id] = mobile_user.id
                stats['mobile_users'] += 1

        for item in backup_data.get('mobile_user_packages', []):
            user_id = mobile_user_id_map.get(item.get('user_id'))
            package_id = package_id_map.get(item.get('package_id'))
            if user_id and package_id:
                db.add(MobileUserPackage(
                    user_id=user_id, package_id=package_id,
                    remaining_sessions=item['remaining_sessions'], is_active=item.get('is_active', True),
                    purchased_at=datetime.fromisoformat(item['purchased_at']) if item.get('purchased_at') else now_kz(),
                ))
        for item in backup_data.get('mobile_app_reviews', []):
            db.add(MobileAppReview(
                user_id=mobile_user_id_map.get(item.get('user_id')),
                client_id=client_id_map.get(item.get('client_id')), stars=item['stars'],
                created_at=datetime.fromisoformat(item['created_at']) if item.get('created_at') else now_kz(),
            ))
        for item in backup_data.get('mobile_sessions', []):
            client_id = client_id_map.get(item.get('client_id'))
            if client_id:
                db.add(MobileSession(
                    id=item['id'], client_id=client_id, is_active=item.get('is_active', True),
                    created_at=datetime.fromisoformat(item['created_at']) if item.get('created_at') else now_kz(),
                    expires_at=datetime.fromisoformat(item['expires_at']),
                ))

        # Уведомления важны для корректной истории и повторных напоминаний.
        for notification_data in backup_data.get('notifications', []):
            instructor_id = instructor_id_map.get(notification_data.get('instructor_id'))
            if instructor_id:
                db.add(NotificationSent(instructor_id=instructor_id,
                    notification_type=notification_data['notification_type'],
                    sent_at=datetime.fromisoformat(notification_data['sent_at']) if notification_data.get('sent_at') else now_kz()))
        
        # Восстанавливаем мобильные записи
        if backup_data.get('mobile_bookings'):
            for mb_data in backup_data['mobile_bookings']:
                user_id = mobile_user_id_map.get(mb_data['user_id'])
                instructor_id = instructor_id_map.get(mb_data['instructor_id'])
                
                if not user_id or not instructor_id:
                    continue
                
                mobile_booking = MobileBooking(
                    user_id=user_id,
                    instructor_id=instructor_id,
                    booking_date=date.fromisoformat(mb_data['booking_date']),
                    start_time=time.fromisoformat(mb_data['start_time']),
                    end_time=time.fromisoformat(mb_data['end_time']) if mb_data.get('end_time') else None,
                    service_type=mb_data.get('service_type'),
                    transmission=mb_data.get('transmission'),
                    location=mb_data.get('location'),
                    status=mb_data.get('status', 'planned'),
                    price=mb_data.get('price', 0),
                    rating_vote=mb_data.get('rating_vote'),
                )
                db.add(mobile_booking)
                stats['mobile_bookings'] += 1
        
        # Восстанавливаем сообщения поддержки
        if backup_data.get('support_messages'):
            for sm_data in backup_data['support_messages']:
                # Используем новые ID
                user_id = mobile_user_id_map.get(sm_data['user_id']) if sm_data.get('user_id') else None
                client_id = client_id_map.get(sm_data['client_id']) if sm_data.get('client_id') else None
                
                support_message = SupportMessage(
                    user_id=user_id,
                    client_id=client_id,
                    instructor_id=instructor_id_map.get(sm_data.get('instructor_id')) if sm_data.get('instructor_id') else None,
                    channel=sm_data.get('channel', 'client'),
                    sender=sm_data['sender'],
                    text=sm_data['text'],
                    is_read=sm_data.get('is_read', False),
                    is_admin_read=sm_data.get('is_admin_read', False),
                    offline_operation_id=sm_data.get('offline_operation_id'),
                    created_at=datetime.fromisoformat(sm_data['created_at']) if sm_data.get('created_at') else now_kz(),
                )
                db.add(support_message)
                stats['support_messages'] += 1

        # Журнал действий является частью снимка: без него восстановление
        # показывало бы историю уже другой базы.
        for log_data in backup_data.get('audit_logs', []):
            db.add(AuditLog(
                admin_username=log_data.get('admin_username'), action=log_data['action'],
                details=log_data.get('details'),
                created_at=datetime.fromisoformat(log_data['created_at']) if log_data.get('created_at') else now_kz(),
            ))

        # Archived rows do not use foreign keys deliberately, but remapping
        # known identifiers keeps the exported history useful after restore.
        for log_data in backup_data.get('archived_logs', []):
            db.add(ArchivedLog(
                source_type=log_data['source_type'], source_log_id=log_data['source_log_id'],
                admin_username=log_data.get('admin_username'), action=log_data.get('action'),
                details=log_data.get('details'), event_type=log_data.get('event_type'),
                event_source=log_data.get('event_source'),
                client_id=client_id_map.get(log_data.get('client_id')),
                instructor_id=instructor_id_map.get(log_data.get('instructor_id')),
                booking_id=booking_id_map.get(log_data.get('booking_id')),
                message=log_data.get('message'),
                created_at=datetime.fromisoformat(log_data['created_at']) if log_data.get('created_at') else None,
                archived_at=datetime.fromisoformat(log_data['archived_at']) if log_data.get('archived_at') else now_kz(),
            ))

        admin_state_data = backup_data.get('admin_state')
        if isinstance(admin_state_data, dict):
            db.add(AdminState(
                id=1,
                notifications_viewed_at=datetime.fromisoformat(admin_state_data['notifications_viewed_at']) if admin_state_data.get('notifications_viewed_at') else None,
                clients_viewed_at=datetime.fromisoformat(admin_state_data['clients_viewed_at']) if admin_state_data.get('clients_viewed_at') else None,
                notifications_viewed_id=admin_state_data.get('notifications_viewed_id'),
                clients_viewed_id=admin_state_data.get('clients_viewed_id'),
            ))
        gender_analytics_data = backup_data.get('gender_analytics')
        if isinstance(gender_analytics_data, dict):
            db.add(GenderAnalytics(
                id=1, male_count=gender_analytics_data.get('male_count', 0),
                female_count=gender_analytics_data.get('female_count', 0),
                unknown_count=gender_analytics_data.get('unknown_count', 0),
                total_count=gender_analytics_data.get('total_count', 0),
                model=gender_analytics_data.get('model'),
                updated_at=datetime.fromisoformat(gender_analytics_data['updated_at']) if gender_analytics_data.get('updated_at') else None,
            ))
        
        await db.commit()
        
        # Записываем в аудит
        await _audit(db, username, "full_backup_restore", 
                    f"Восстановлена резервная копия от {backup_data.get('backup_date')}. "
                    f"Клиентов: {stats['clients']}, Записей: {stats['bookings']}, "
                    f"Инструкторов: {stats['instructors']}, Сертификатов: {stats['certificates']}")
        
        return {
            "ok": True,
            "message": "Резервная копия успешно восстановлена",
            "stats": stats,
            "backup_date": backup_data.get('backup_date'),
            "backup_by": backup_data.get('backup_by'),
        }
        
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Ошибка восстановления: {str(e)}")


BOT_ACTIONS = [
    "new_client", "new_booking", "booking_confirmed",
    "booking_rescheduled", "no_show", "rating_given", "rating_request_sent",
    "confirmation_sent", "client_arrived", "lesson_completed",
    "certificate_activated", "certificate_used",
]


@router.get("/clients/{client_id}/history")
async def get_client_history(
    request: Request, client_id: int, db: AsyncSession = Depends(get_db)
):
    """Полная история действий клиента: записи + события из audit_log + сертификаты."""
    _get_admin_username(request)

    # Записи клиента
    bookings_result = await db.execute(
        select(Booking)
        .options(selectinload(Booking.instructor), selectinload(Booking.certificate))
        .where(Booking.client_id == client_id)
        .order_by(Booking.booking_date.desc(), Booking.start_time.desc())
    )
    bookings = bookings_result.scalars().all()

    # Сертификаты клиента
    certs_result = await db.execute(
        select(Certificate).where(Certificate.activated_by_client_id == client_id)
    )
    certs = certs_result.scalars().all()

    # Аудит-события связанные с клиентом
    client_result = await db.execute(select(Client).where(Client.id == client_id))
    client = client_result.scalar_one_or_none()
    client_name = client.name if client else ""

    audit_result = await db.execute(
        select(AuditLog)
        .where(AuditLog.details.contains(client_name) if client_name else AuditLog.id == -1)
        .order_by(AuditLog.created_at.desc())
        .limit(50)
    )
    audit_events = audit_result.scalars().all()

    # Формируем единую ленту событий
    events = []

    for b in bookings:
        status_labels = {
            "planned": "Запланирована", "confirmed": "Подтверждена",
            "completed": "Завершена", "cancelled": "Отменена",
            "in_progress": "В процессе", "no_show": "Неявка",
        }
        service_label = "Обучение" if b.service_type == "training" else "Экзамен"
        trans_label = "Механика" if b.transmission == "manual" else "Автомат"
        description = (
            f"{service_label} ({trans_label}) · {b.booking_date} {str(b.start_time)[:5]} · "
            f"{b.location} · {b.instructor.name if b.instructor else '—'} · "
            f"{b.price}₸"
        )
        if b.certificate_amount and b.certificate_amount > 0:
            description += f" (сертификат −{b.certificate_amount}₸)"
        events.append({
            "type": "booking",
            "icon": "📅",
            "title": f"Запись: {status_labels.get(b.status, b.status)}",
            "description": description,
            "date": b.created_at.isoformat() if b.created_at else str(b.booking_date),
            "status": b.status,
        })

    for cert in certs:
        events.append({
            "type": "certificate",
            "icon": "🎟️",
            "title": f"Сертификат активирован",
            "description": f"Код: {cert.code} · Номинал: {cert.nominal}₸ · Остаток: {cert.remaining}₸",
            "date": cert.used_at.isoformat() if cert.used_at else "",
        })

    for ev in audit_events:
        action_labels = {
            "new_booking": "Новая запись",
            "booking_rescheduled": "Перенос записи",
            "booking_cancelled": "Отмена записи",
            "booking_confirmed": "Подтверждение записи",
            "certificate_activated": "Активация сертификата",
            "certificate_used": "Использование сертификата",
            "new_client": "Регистрация",
            "rating_given": "Оценка занятия",
        }
        label = action_labels.get(ev.action, ev.action)
        events.append({
            "type": "audit",
            "icon": "📝",
            "title": label,
            "description": ev.details or "",
            "date": ev.created_at.isoformat() if ev.created_at else "",
        })

    # Сортируем по дате убыванию
    events.sort(key=lambda x: x.get("date", ""), reverse=True)
    return events


@router.get("/clients/{client_id}/bookings")
async def get_client_bookings(
    request: Request, client_id: int, db: AsyncSession = Depends(get_db)
):
    _get_admin_username(request)
    result = await db.execute(
        select(Booking)
        .options(
            selectinload(Booking.instructor),
            selectinload(Booking.certificate)
        )
        .where(Booking.client_id == client_id)
        .order_by(Booking.booking_date.desc(), Booking.start_time.desc())
    )
    bookings = result.scalars().all()
    
    output = []
    for b in bookings:
        output.append({
            "id": b.id,
            "booking_date": str(b.booking_date),
            "start_time": str(b.start_time),
            "end_time": str(b.end_time),
            "service_type": b.service_type,
            "transmission": b.transmission,
            "location": b.location,
            "status": b.status,
            "instructor_name": b.instructor.name if b.instructor else None,
            "base_price": b.base_price,
            "price": b.price,
            "certificate_amount": b.certificate_amount,
            "referral_discount_amount": b.referral_discount_amount,
            "certificate_id": b.certificate_id,
            "certificate_code": b.certificate.code if b.certificate else None,
        })
    return output


@router.get("/clients")
async def list_clients(request: Request, db: AsyncSession = Depends(get_db)):
    _get_admin_username(request)
    result = await db.execute(
        select(Client).where(Client.is_deleted == False).order_by(Client.created_at.desc())
    )
    clients = result.scalars().all()

    output = []
    for c in clients:
        bookings_count_result = await db.execute(select(func.count()).select_from(Booking).where(Booking.client_id == c.id))
        bookings_count = bookings_count_result.scalar() or 0

        certs_result = await db.execute(select(Certificate).where(Certificate.activated_by_client_id == c.id))
        certs = certs_result.scalars().all()

        packages_result = await db.execute(select(ClientPackage).options(selectinload(ClientPackage.package)).where(ClientPackage.client_id == c.id))
        packages = packages_result.scalars().all()

        output.append({
            "id": c.id,
            "name": c.name,
            "phone": c.phone,
            "telegram_id": c.telegram_id,
            "referral_code": c.referral_code,
            "referral_discount_available": c.referral_discount_available,
            "avatar_url": c.avatar_url,
            "bookings_count": bookings_count,
            "certificates": [
                {"id": cert.id, "code": cert.code, "nominal": cert.nominal, "remaining": cert.remaining, "is_used": cert.is_used}
                for cert in certs
            ],
            "packages": [
                {
                    "id": cp.id,
                    "package_id": cp.package_id,
                    "name": cp.package.name if cp.package else "",
                    "sessions_count": cp.package.sessions_count if cp.package else 0,
                    "remaining_sessions": cp.remaining_sessions,
                    "is_active": cp.is_active
                    ,"expires_at": cp.expires_at.isoformat() if cp.expires_at else None
                    ,"bonus_exam": cp.package.bonus_exam if cp.package else False
                    ,"code": cp.package.code if cp.package else None
                    ,"remaining_bonus_exams": cp.remaining_bonus_exams
                }
                for cp in packages
            ],
            "created_at": str(c.created_at)
        })
    return output


@router.get("/clients/search")
async def search_clients(
    request: Request,
    q: str,
    db: AsyncSession = Depends(get_db),
):
    _get_admin_username(request)
    term = (q or "").strip()
    if len(term) < 2:
        return []
    like = f"%{term}%"
    # Нормализуем поиск по телефону: сравниваем последние 10 цифр
    normalized_term = ''.join(filter(str.isdigit, term))
    if len(normalized_term) >= 6:
        # Поиск по телефону через нормализацию
        result = await db.execute(
            select(Client)
            .where(Client.is_deleted == False, Client.name.ilike(like))
            .order_by(Client.created_at.desc())
            .limit(8)
        )
        clients = list(result.scalars().all())
        # Дополнительно ищем по телефону (нормализованное сравнение)
        all_clients_result = await db.execute(select(Client).where(
            Client.is_deleted == False,
            Client.phone.isnot(None),
        ))
        for c in all_clients_result.scalars().all():
            if c.phone and normalized_term[-10:] in ''.join(filter(str.isdigit, c.phone)):
                if c not in clients:
                    clients.append(c)
        clients = clients[:8]
    else:
        result = await db.execute(
            select(Client)
            .where(
                Client.is_deleted == False,
                (Client.name.ilike(like)) | (Client.phone.ilike(like)),
            )
            .order_by(Client.created_at.desc())
            .limit(8)
        )
        clients = result.scalars().all()
    output = []
    for client in clients:
        bookings_count_result = await db.execute(
            select(func.count()).select_from(Booking).where(Booking.client_id == client.id)
        )
        output.append({
            "id": client.id,
            "name": client.name,
            "phone": client.phone,
            "bookings_count": bookings_count_result.scalar() or 0,
        })
    return output


class ClientUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    password: Optional[str] = None
    referral_code: Optional[str] = None


class ClientCreate(BaseModel):
    name: str
    password: str
    phone: Optional[str] = None
    referral_code: Optional[str] = None   # собственный реф. код клиента (пусто = авто)
    referrer_code: Optional[str] = None   # код того, кто пригласил


@router.post("/clients")
async def create_client(request: Request, body: ClientCreate, db: AsyncSession = Depends(get_db)):
    username = _get_admin_username(request)
    operation_id = _idempotency_key(request)
    if operation_id:
        existing = (await db.execute(
            select(Client).where(Client.offline_operation_id == operation_id)
        )).scalar_one_or_none()
        if existing and not existing.is_deleted:
            return {"ok": True, "client_id": existing.id}
    if not body.name or not body.name.strip():
        raise HTTPException(status_code=400, detail="Имя обязательно")
    if not body.password or not body.password.strip():
        raise HTTPException(status_code=400, detail="Пароль обязателен — клиент не может быть создан без пароля")
    _validate_client_password(body.password)

    normalized_phone = normalize_phone(body.phone) if body.phone else None
    existing_client = None
    if normalized_phone:
        existing_client = await find_client_by_phone(
            db, normalized_phone, include_deleted=True, for_update=True,
        )
        if existing_client and not existing_client.is_deleted:
            # Old queued creations did not carry a key during their initial
            # request.  Bind their queue key to the already present client so
            # their retry is acknowledged instead of becoming a duplicate.
            if operation_id:
                if not existing_client.offline_operation_id:
                    existing_client.offline_operation_id = operation_id
                    await db.commit()
                return {"ok": True, "client_id": existing_client.id}
            raise HTTPException(status_code=400, detail="Клиент с таким телефоном уже существует")
    # Собственный реферальный код клиента
    own_referral_code = (body.referral_code or "").strip().upper()
    if not own_referral_code:
        own_referral_code = secrets.token_hex(4).upper()
    else:
        # Проверяем уникальность
        code_query = select(Client).where(Client.referral_code == own_referral_code)
        if existing_client:
            code_query = code_query.where(Client.id != existing_client.id)
        existing_code = await db.execute(code_query)
        if existing_code.scalars().first():
            own_referral_code = secrets.token_hex(4).upper()

    # Привязка к рефереру (по коду пригласившего)
    referred_by_client_id = None
    referral_discount_available = False
    if body.referrer_code:
        ref_result = await db.execute(
            select(Client).where(
                Client.referral_code == body.referrer_code.strip().upper(),
                Client.is_deleted == False,
            )
        )
        referrer = ref_result.scalar_one_or_none()
        if referrer:
            referred_by_client_id = referrer.id
            referral_discount_available = True
        else:
            raise HTTPException(status_code=400, detail="Реферальный код пригласившего не найден")

    reactivated = bool(existing_client and existing_client.is_deleted)
    if reactivated:
        client = existing_client
        await reactivate_deleted_client(
            db,
            client,
            name=body.name,
            phone=normalized_phone,
            password_hash=hash_password(body.password),
        )
        client.referral_code = own_referral_code
        client.referred_by_client_id = referred_by_client_id
        client.referral_discount_available = referral_discount_available
        client.offline_operation_id = operation_id
    else:
        client = Client(
            name=body.name.strip(),
            phone=normalized_phone,
            password_hash=hash_password(body.password),
            referral_code=own_referral_code,
            referred_by_client_id=referred_by_client_id,
            referral_discount_available=referral_discount_available,
            offline_operation_id=operation_id,
        )
        db.add(client)
    try:
        await db.flush()
        if referred_by_client_id:
            from app.models.models import ReferralRecord
            referral_exists = await db.scalar(select(func.count()).select_from(ReferralRecord).where(
                ReferralRecord.referrer_client_id == referred_by_client_id,
                ReferralRecord.referred_client_id == client.id,
            ))
            if not referral_exists:
                db.add(ReferralRecord(referrer_client_id=referred_by_client_id, referred_client_id=client.id))
        await db.commit()
    except IntegrityError:
        await db.rollback()
        if operation_id:
            existing = (await db.execute(
                select(Client).where(Client.offline_operation_id == operation_id)
            )).scalar_one_or_none()
            if existing:
                return {"ok": True, "client_id": existing.id}
        raise
    await db.refresh(client)

    action = "client_profile_reactivated" if reactivated else "create_client"
    details = (
        f"Администратор восстановил карточку клиента «{client.name}»."
        if reactivated else
        f"Администратор создал карточку клиента «{client.name}»."
    )
    await _audit(db, username, action, details)
    return {"ok": True, "client_id": client.id}


@router.delete("/clients/{client_id}")
async def delete_client(request: Request, client_id: int, db: AsyncSession = Depends(get_db)):
    """Hide the client and delete only customer-owned operational data."""
    username = _get_admin_username(request)
    result = await db.execute(
        select(Client).where(
            Client.id == client_id,
            Client.is_deleted == False,
        ).with_for_update()
    )
    client = result.scalar_one_or_none()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    name = client.name
    booking_ids = select(Booking.id).where(Booking.client_id == client_id)
    certificate_ids = select(Certificate.id).where(or_(
        Certificate.activated_by_client_id == client_id,
        Certificate.used_by_user_id == client_id,
    ))
    package_ids = list((await db.execute(
        select(ClientPackage.package_id).where(ClientPackage.client_id == client_id)
    )).scalars().all())

    # Events, restrictions and referral links intentionally remain. Detach
    # events from bookings before removing those bookings so this also works on
    # old databases where the SET NULL constraint may be missing.
    await db.execute(
        update(Event).where(Event.booking_id.in_(booking_ids)).values(booking_id=None)
    )
    await db.execute(sa_delete(CertificateRequest).where(or_(
        CertificateRequest.client_id == client_id,
        CertificateRequest.matched_certificate_id.in_(certificate_ids),
    )))
    await db.execute(sa_delete(SupportMessage).where(SupportMessage.client_id == client_id))
    await db.execute(sa_delete(MobileSession).where(MobileSession.client_id == client_id))
    await db.execute(sa_delete(ClientPackage).where(ClientPackage.client_id == client_id))
    await db.execute(sa_delete(Booking).where(Booking.client_id == client_id))
    await db.execute(sa_delete(Certificate).where(Certificate.id.in_(certificate_ids)))
    if package_ids:
        await db.execute(sa_delete(Package).where(Package.id.in_(package_ids)))

    # Keep this row only as a hidden foreign-key anchor for the preserved
    # history. Authentication code rejects deleted clients even though their
    # identity and referral data remain intact.
    client.is_deleted = True
    await db.commit()
    await _audit(db, username, "delete_client", f"Администратор удалил клиента «{name}».")
    return {"ok": True}


@router.put("/clients/{client_id}")
async def update_client(
    request: Request, client_id: int, body: ClientUpdate, db: AsyncSession = Depends(get_db)
):
    username = _get_admin_username(request)
    result = await db.execute(select(Client).where(
        Client.id == client_id,
        Client.is_deleted == False,
    ))
    client = result.scalar_one_or_none()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    if body.name is not None:
        client.name = body.name.strip() or client.name
    if body.phone is not None:
        normalized_phone = normalize_phone(body.phone)
        if normalized_phone:
            existing_phone_owner = (await db.execute(select(Client).where(and_(
                Client.phone == normalized_phone,
                Client.id != client_id,
            )))).scalar_one_or_none()
            if existing_phone_owner:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f'Телефон уже принадлежит клиенту «{existing_phone_owner.name}» '
                        f'(ID {existing_phone_owner.id}). Используйте объединение дублей, чтобы сохранить историю.'
                    ),
                )
        client.phone = normalized_phone
    if body.password:
        _validate_client_password(body.password)
        client.password_hash = hash_password(body.password)
    if body.referral_code is not None:
        referral_code = body.referral_code.strip().upper()
        if referral_code and referral_code != client.referral_code:
            existing_code_owner = (await db.execute(select(Client).where(and_(
                Client.referral_code == referral_code,
                Client.id != client_id,
            )))).scalar_one_or_none()
            if existing_code_owner:
                raise HTTPException(status_code=409, detail="Этот реферальный код уже используется")
            client.referral_code = referral_code

    await db.commit()
    action = "reset_client_password" if body.password else "update_client"
    detail = (
        f"Администратор назначил новый пароль клиенту «{client.name}»."
        if body.password else
        f"Администратор отредактировал карточку клиента «{client.name}»."
    )
    await _audit(db, username, action, detail)
    return {"ok": True}


class AssignPackageRequest(BaseModel):
    client_id: int
    package_id: int


@router.post("/clients/assign-package")
async def assign_package_to_client(
    request: Request, body: AssignPackageRequest, db: AsyncSession = Depends(get_db)
):
    username = _get_admin_username(request)
    client = await db.get(Client, body.client_id)
    if not client or client.is_deleted:
        raise HTTPException(status_code=404, detail="Client not found")
    package = (await db.execute(select(Package).where(
        Package.id == body.package_id
    ).with_for_update())).scalar_one_or_none()
    if not package or not package.is_active:
        raise HTTPException(status_code=404, detail="Package not found")

    # A Package is an issued offer with its own code, not a reusable template.
    # It can therefore belong to one client only.
    assigned = (await db.execute(
        select(ClientPackage).where(ClientPackage.package_id == package.id)
    )).scalars().first()
    if assigned:
        if assigned.client_id == client.id:
            return {"ok": True, "client_package_id": assigned.id, "already_assigned": True}
        raise HTTPException(status_code=400, detail="Этот пакет уже выдан клиенту и не может быть назначен повторно")

    client_package = ClientPackage(
        client_id=client.id,
        package_id=package.id,
        remaining_sessions=package.sessions_count,
        is_active=True,
        expires_at=now_kz() + timedelta(days=package.validity_days or 30),
        remaining_bonus_exams=1 if package.bonus_exam else 0,
    )
    db.add(client_package)
    await db.commit()
    await db.refresh(client_package)

    await _audit(
        db,
        username,
        "assign_package",
        f"Администратор применил пакет «{package.name}» ({package.code}) клиенту «{client.name}».",
    )
    return {"ok": True, "client_package_id": client_package.id}


class ActivateCertificateForClientRequest(BaseModel):
    client_id: int
    certificate_code: str


@router.post("/clients/activate-certificate")
async def activate_certificate_for_client(
    request: Request, body: ActivateCertificateForClientRequest, db: AsyncSession = Depends(get_db)
):
    username = _get_admin_username(request)
    client = await db.get(Client, body.client_id)
    if not client or client.is_deleted:
        raise HTTPException(status_code=404, detail="Client not found")

    certificate_code = body.certificate_code.strip().upper()
    if not certificate_code:
        raise HTTPException(status_code=400, detail="Введите код сертификата")

    cert_result = await db.execute(select(Certificate).where(
        Certificate.code == certificate_code
    ).with_for_update())
    cert = cert_result.scalar_one_or_none()
    if not cert:
        raise HTTPException(status_code=404, detail="Certificate not found")
    if cert.is_used or cert.remaining <= 0:
        raise HTTPException(status_code=400, detail="Certificate already used")

    # Активный сертификат закрепляется за одним клиентом. Повторное сохранение
    # того же клиента допустимо, но передавать остаток другому нельзя.
    if cert.activated_by_client_id and cert.activated_by_client_id != client.id:
        raise HTTPException(status_code=400, detail="Сертификат уже активирован для другого клиента")

    already_activated = cert.activated_by_client_id == client.id
    cert.activated_by_client_id = client.id
    cert.used_by_user_id = client.id
    if not cert.used_at:
        cert.used_at = now_kz()
    await db.commit()

    if not already_activated:
        await _audit(
            db,
            username,
            "activate_certificate_for_client",
            f"Администратор активировал сертификат «{certificate_code}» для клиента «{client.name}».",
        )
    return {"ok": True, "already_activated": already_activated}


@router.get("/notifications")
async def get_notifications(request: Request, db: AsyncSession = Depends(get_db)):
    """Вкладка «События»: успешные действия клиентов в Telegram и приложении."""
    _get_admin_username(request)
    await archive_previous_day_logs(db)

    # Системные напоминания и действия инструкторов могут храниться в той же
    # таблице для совместимости, но в этот журнал попадают только события,
    # привязанные к клиенту и созданные клиентскими каналами.
    events_result = await db.execute(
        select(Event).where(
            Event.client_id.isnot(None),
            Event.source.in_(("telegram", "mobile")),
        ).order_by(Event.created_at.desc())
    )
    return [
        {
            "type": event.event_type,
            "message": event.message,
            "source": event.source,
            "client_id": event.client_id,
            "booking_id": event.booking_id,
            "created_at": str(event.created_at),
        }
        for event in events_result.scalars().all()
    ]


# ==================== ЛИСТ ОЖИДАНИЯ ====================

class WaitingListCreate(BaseModel):
    name: str
    phone: Optional[str] = None
    desired_date: Optional[str] = None
    desired_time_start: Optional[str] = None
    desired_time_end: Optional[str] = None
    transmission: Optional[str] = None
    instructor_id: Optional[int] = None
    instructor_gender: Optional[str] = None
    notes: Optional[str] = None


def _waiting_entry_matches_booking(entry: WaitingListEntry, booking: Booking) -> bool:
    """Return whether an open waiting-list request fits a freed booking slot."""
    if entry.desired_date and entry.desired_date != booking.booking_date:
        return False
    if entry.desired_time_start and booking.start_time < entry.desired_time_start:
        return False
    if entry.desired_time_end and booking.start_time >= entry.desired_time_end:
        return False
    if entry.transmission and entry.transmission not in ("both", booking.transmission):
        return False
    if entry.instructor_id and entry.instructor_id != booking.instructor_id:
        return False
    if entry.instructor_gender and entry.instructor_gender not in ("any", "both"):
        instructor = booking.instructor
        if not instructor or str(instructor.gender).lower() != entry.instructor_gender.lower():
            return False
    return True


@router.get("/waiting-list")
async def get_waiting_list(request: Request, db: AsyncSession = Depends(get_db)):
    _get_admin_username(request)
    result = await db.execute(
        select(WaitingListEntry).options(selectinload(WaitingListEntry.instructor)).order_by(WaitingListEntry.created_at.asc())
    )
    entries = result.scalars().all()
    normalized_phones = {normalize_phone(entry.phone) for entry in entries if entry.phone}
    client_by_phone = {}
    if normalized_phones:
        latest_booking_source = (
            select(Booking.source)
            .where(Booking.client_id == Client.id)
            .order_by(Booking.created_at.desc())
            .limit(1)
            .scalar_subquery()
        )
        client_rows = await db.execute(
            select(
                Client.id,
                Client.phone,
                Client.telegram_id,
                latest_booking_source.label("latest_booking_source"),
            ).where(Client.phone.in_(normalized_phones))
        )
        client_by_phone = {row.phone: row for row in client_rows}

    # Only upcoming cancellations are actionable slots. Fetch them once for
    # the whole list instead of making the browser request a matcher per slot.
    current = datetime.now(KZ_TZ)
    actionable_entries = [entry for entry in entries if entry.status == "waiting"]
    cancelled_bookings = []
    if actionable_entries:
        cancelled_result = await db.execute(
            select(Booking)
            .options(selectinload(Booking.instructor))
            .where(
                Booking.status == "cancelled",
                or_(
                    Booking.booking_date > current.date(),
                    and_(
                        Booking.booking_date == current.date(),
                        Booking.start_time >= current.time().replace(tzinfo=None),
                    ),
                ),
            )
            .order_by(Booking.booking_date, Booking.start_time)
        )
        cancelled_bookings = cancelled_result.scalars().all()

    matches_cancelled_slot = {
        entry.id: any(_waiting_entry_matches_booking(entry, booking) for booking in cancelled_bookings)
        for entry in actionable_entries
    }
    items = []
    for e in entries:
        linked_client = client_by_phone.get(normalize_phone(e.phone)) if e.phone else None
        items.append({
            "id": e.id,
            "name": e.name,
            "phone": e.phone,
            "client_id": linked_client.id if linked_client else None,
            "client_source": (
                "telegram" if linked_client and linked_client.telegram_id
                else linked_client.latest_booking_source if linked_client else None
            ),
            "desired_date": e.desired_date.isoformat() if e.desired_date else None,
            "desired_time_start": e.desired_time_start.strftime("%H:%M") if e.desired_time_start else None,
            "desired_time_end": e.desired_time_end.strftime("%H:%M") if e.desired_time_end else None,
            "transmission": e.transmission,
            "instructor_id": e.instructor_id,
            "instructor_name": e.instructor.name if e.instructor else None,
            "instructor_gender": e.instructor_gender,
            "status": e.status,
            # A dated waiting-list request becomes actionable on its date.
            # Keep it flagged afterwards too, until the administrator marks
            # the outcome, so a missed morning does not hide the client.
            "requires_attention": bool(
                e.status == "waiting" and e.desired_date and e.desired_date <= today_kz()
            ),
            "matches_cancelled_slot": matches_cancelled_slot.get(e.id, False),
            "notes": e.notes,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        })
    return {"items": items}


@router.post("/waiting-list")
async def create_waiting_list_entry(
    request: Request, body: WaitingListCreate, db: AsyncSession = Depends(get_db)
):
    username = _get_admin_username(request)
    operation_id = _idempotency_key(request)
    if operation_id:
        existing = (await db.execute(select(WaitingListEntry).where(
            WaitingListEntry.offline_operation_id == operation_id
        ))).scalar_one_or_none()
        if existing:
            return {"ok": True, "id": existing.id}
    entry = WaitingListEntry(
        name=body.name,
        phone=normalize_phone(body.phone),
        desired_date=date.fromisoformat(body.desired_date) if body.desired_date else None,
        desired_time_start=time.fromisoformat(body.desired_time_start) if body.desired_time_start else None,
        desired_time_end=time.fromisoformat(body.desired_time_end) if body.desired_time_end else None,
        transmission=body.transmission,
        instructor_id=body.instructor_id,
        instructor_gender=body.instructor_gender,
        notes=body.notes,
        offline_operation_id=operation_id,
    )
    db.add(entry)
    existing = await _commit_idempotent_create(db, WaitingListEntry, operation_id)
    if existing:
        return {"ok": True, "id": existing.id}
    await db.refresh(entry)
    await _audit(db, username, "waiting_list_add", f"Добавлен в лист ожидания: {body.name}")
    return {"ok": True, "id": entry.id}


@router.put("/waiting-list/{entry_id}")
async def update_waiting_list_entry(
    request: Request, entry_id: int, body: WaitingListCreate, db: AsyncSession = Depends(get_db)
):
    username = _get_admin_username(request)
    result = await db.execute(select(WaitingListEntry).where(WaitingListEntry.id == entry_id))
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    entry.name = body.name
    entry.phone = normalize_phone(body.phone)
    entry.desired_date = date.fromisoformat(body.desired_date) if body.desired_date else None
    entry.desired_time_start = time.fromisoformat(body.desired_time_start) if body.desired_time_start else None
    entry.desired_time_end = time.fromisoformat(body.desired_time_end) if body.desired_time_end else None
    entry.transmission = body.transmission
    entry.instructor_id = body.instructor_id
    entry.instructor_gender = body.instructor_gender
    entry.notes = body.notes
    await db.commit()
    await _audit(db, username, "waiting_list_update", f"Обновлена запись #{entry_id} в листе ожидания")
    return {"ok": True}


@router.delete("/waiting-list/{entry_id}")
async def delete_waiting_list_entry(
    request: Request, entry_id: int, db: AsyncSession = Depends(get_db)
):
    username = _get_admin_username(request)
    result = await db.execute(select(WaitingListEntry).where(WaitingListEntry.id == entry_id))
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    await db.delete(entry)
    await db.commit()
    await _audit(db, username, "waiting_list_delete", f"Удалена запись #{entry_id} из листа ожидания")
    return {"ok": True}


@router.put("/waiting-list/{entry_id}/status")
async def update_waiting_list_status(
    request: Request, entry_id: int, body: ConfirmBookingRequest, db: AsyncSession = Depends(get_db)
):
    username = _get_admin_username(request)
    result = await db.execute(select(WaitingListEntry).where(WaitingListEntry.id == entry_id))
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    entry.status = body.action
    await db.commit()
    await _audit(db, username, "waiting_list_status", f"Статус записи #{entry_id}: {body.action}")
    return {"ok": True}


@router.get("/waiting-list/matching/{booking_id}")
async def get_matching_waiting_clients(
    request: Request, booking_id: int, db: AsyncSession = Depends(get_db)
):
    _get_admin_username(request)
    result = await db.execute(
        select(Booking).options(selectinload(Booking.instructor)).where(Booking.id == booking_id)
    )
    booking = result.scalar_one_or_none()
    if not booking:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    wl_result = await db.execute(
        select(WaitingListEntry)
        .where(WaitingListEntry.status == "waiting")
        .order_by(WaitingListEntry.created_at.asc())
    )
    entries = wl_result.scalars().all()
    matching = []
    for e in entries:
        if not _waiting_entry_matches_booking(e, booking):
            continue
        matching.append({
            "id": e.id,
            "name": e.name,
            "phone": e.phone,
            "status": e.status,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        })
    return {"items": matching}


@router.get("/bookings/{booking_id}/copy-text")
async def get_booking_copy_text(
    request: Request, booking_id: int, db: AsyncSession = Depends(get_db)
):
    _get_admin_username(request)
    result = await db.execute(
        select(Booking).options(selectinload(Booking.client), selectinload(Booking.instructor)).where(Booking.id == booking_id)
    )
    booking = result.scalar_one_or_none()
    if not booking:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    date_str = booking.booking_date.strftime("%d.%m.%Y")
    time_str = booking.start_time.strftime("%H:%M")
    number_line = f"\n📋 Номер записи: {booking.booking_number}" if booking.booking_number else ""
    text = (
        f"Здравствуйте. На {date_str} в {time_str} освободилась запись.{number_line} Желаете забронировать это время?"
    )
    return {"text": text}


@router.get("/bookings/{booking_id}/card-text")
async def get_booking_card_text(
    request: Request, booking_id: int, db: AsyncSession = Depends(get_db)
):
    _get_admin_username(request)
    result = await db.execute(
        select(Booking).options(selectinload(Booking.client), selectinload(Booking.instructor)).where(Booking.id == booking_id)
    )
    booking = result.scalar_one_or_none()
    if not booking:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    trans_label = "Механика" if booking.transmission == "manual" else "Автомат"
    service_label = "Обучение вождению" if booking.service_type == "training" else "Пробный экзамен"
    number_line = f"\n📋 Номер записи: <b>{booking.booking_number}</b>" if booking.booking_number else ""
    text = (
        f"✅ Вы записаны!\n\n"
        f"{number_line}\n"
        f"📍 {booking.location}\n"
        f"📅 {booking.booking_date.strftime('%d.%m.%Y')}\n"
        f"🕐 {booking.start_time.strftime('%H:%M')}\n"
        f"🚗 {service_label} ({trans_label})\n"
        f"👨‍🏫 Инструктор: {booking.instructor.name if booking.instructor else 'Не назначен'}\n\n"
        f"Мы напомним вам за час до начала занятия."
    )
    return {"text": text}


@router.get("/bookings/{booking_id}/reminder-text")
async def get_booking_reminder_text(
    request: Request, booking_id: int, db: AsyncSession = Depends(get_db)
):
    """Возвращает готовый текст напоминания, который админ может скопировать клиенту."""
    _get_admin_username(request)
    result = await db.execute(
        select(Booking).options(selectinload(Booking.instructor)).where(Booking.id == booking_id)
    )
    booking = result.scalar_one_or_none()
    if not booking:
        raise HTTPException(status_code=404, detail="Запись не найдена")

    trans_label = "Механика" if booking.transmission == "manual" else "Автомат"
    service_label = "Обучение вождению" if booking.service_type == "training" else "Пробный экзамен"
    text = (
        "🔔 Напоминание о записи!\n"
        "Ваше занятие уже через 1 час.\n"
        f"📋 Номер записи: {booking.booking_number or '—'}\n"
        f"📍 Адрес: {booking.location}\n"
        f"⏰ Время: {booking.start_time.strftime('%H:%M')}\n"
        f"🚗 Программа: {service_label} ({trans_label})\n"
        f"👨‍🏫 Инструктор: {booking.instructor.name if booking.instructor else 'Не назначен'}\n"
        "💵 Оплатить занятие можно наличными или через Kaspi QR.\n"
        "⏱️ Пожалуйста, не опаздывайте.\n"
        "🚦 Хорошего занятия!"
    )
    return {"text": text}


# ==================== БЛОКИРОВКИ КЛИЕНТОВ ====================

@router.get("/client-blocks/{client_id}")
async def get_client_blocks(
    request: Request, client_id: int, db: AsyncSession = Depends(get_db)
):
    _get_admin_username(request)
    now = now_kz()
    result = await db.execute(
        select(ClientBlock).where(
            and_(ClientBlock.client_id == client_id, ClientBlock.blocked_until > now)
        )
    )
    blocks = result.scalars().all()
    return {"blocked": len(blocks) > 0, "blocks": [{"until": b.blocked_until.isoformat(), "reason": b.reason} for b in blocks]}


# ==================== СЕРТИФИКАТЫ — ЗАЯВКИ ====================

@router.get("/certificate-requests")
async def get_certificate_requests(request: Request, db: AsyncSession = Depends(get_db)):
    _get_admin_username(request)
    result = await db.execute(
        select(CertificateRequest)
        .options(selectinload(CertificateRequest.client), selectinload(CertificateRequest.certificate))
        .where(CertificateRequest.status == "pending")
        .order_by(CertificateRequest.created_at.desc())
    )
    requests = result.scalars().all()
    items = []
    for r in requests:
        matched = r.certificate is not None
        items.append({
            "id": r.id,
            "client_name": r.client.name if r.client else "—",
            "client_phone": r.client.phone if r.client else "—",
            "code_entered": r.code_entered,
            "matched": matched,
            "certificate_nominal": r.certificate.nominal if r.certificate else None,
            "certificate_remaining": r.certificate.remaining if r.certificate else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })
    return {"items": items}


@router.post("/certificate-requests/{request_id}/confirm")
async def confirm_certificate_request(
    request: Request, request_id: int, body: ConfirmBookingRequest, db: AsyncSession = Depends(get_db)
):
    username = _get_admin_username(request)
    result = await db.execute(
        select(CertificateRequest).options(
            selectinload(CertificateRequest.certificate), selectinload(CertificateRequest.client)
        ).where(CertificateRequest.id == request_id)
    )
    cert_req = result.scalar_one_or_none()
    if not cert_req:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    if body.action == "confirm":
        if not cert_req.matched_certificate_id:
            raise HTTPException(status_code=400, detail="Код не соответствует ни одному сертификату")
        cert_req.status = "confirmed"
        cert_req.certificate.activated_by_client_id = cert_req.client_id
        cert_req.certificate.used_by_user_id = cert_req.client_id
        cert_req.certificate.used_at = now_kz()
        if cert_req.booking_id:
            booking = await db.get(Booking, cert_req.booking_id)
            if booking and booking.status not in ("cancelled", "no_show"):
                booking.certificate_id = cert_req.certificate.id
                booking.certificate_amount = min(cert_req.certificate.nominal, booking.base_price or booking.price)
                booking.price = max(0, booking.price - booking.certificate_amount)
                booking.payment_status = "paid" if booking.price == 0 else "partial"
                cert_req.certificate.remaining = max(0, cert_req.certificate.remaining - booking.certificate_amount)
                cert_req.certificate.is_used = cert_req.certificate.remaining == 0
        await db.commit()
        client_label = cert_req.client.name if cert_req.client else "—"
        await _audit(
            db, username, "certificate_confirmed",
            f"Сертификат подтверждён: код {cert_req.code_entered}, клиент {client_label} (id={cert_req.client_id})",
        )
        client = cert_req.client
        message = "✅ Ваш сертификат подтверждён и доступен для оплаты записи."
        if client and client.telegram_id and settings.BOT_TOKEN:
            try:
                async with httpx.AsyncClient(timeout=10) as tg_client:
                    await tg_client.post(
                        f"https://api.telegram.org/bot{settings.BOT_TOKEN}/sendMessage",
                        json={"chat_id": client.telegram_id.strip(), "text": message},
                    )
            except Exception:
                pass
        if client:
            await send_push_to_user(client.id, "Сертификат подтверждён", message,
                                    {"type": "certificate_confirmed", "request_id": request_id})
        return {"ok": True}
    elif body.action == "reject":
        cert_req.status = "rejected"
        await db.commit()
        client_label = cert_req.client.name if cert_req.client else "—"
        await _audit(
            db, username, "certificate_rejected",
            f"Сертификат отклонён: код {cert_req.code_entered}, клиент {client_label} (id={cert_req.client_id})",
        )
        return {"ok": True}
    raise HTTPException(status_code=400, detail="Некорректное действие")


# ==================== ГЛАВНЫЙ ИНСТРУКТОР ====================

@router.put("/instructors/{instructor_id}/set-lead")
async def set_lead_instructor(
    request: Request, instructor_id: int, db: AsyncSession = Depends(get_db)
):
    username = _get_admin_username(request)
    instructor = await db.get(Instructor, instructor_id)
    if not instructor:
        raise HTTPException(status_code=404, detail="Инструктор не найден")
    reset_result = await db.execute(update(Instructor).values(is_lead=False))
    instructor.is_lead = True
    await db.commit()
    await _audit(db, username, "set_lead_instructor", f"Главный инструктор: {instructor.name}")
    return {"ok": True}


# ==================== ПРОВЕРКА ОГРАНИЧЕНИЯ 2 ЗАПИСИ НА ДЕНЬ ====================

@router.get("/clients/{client_id}/daily-limit/{check_date}")
async def check_daily_booking_limit(
    request: Request, client_id: int, check_date: str, db: AsyncSession = Depends(get_db)
):
    _get_admin_username(request)
    target_date = date.fromisoformat(check_date)
    result = await db.execute(
        select(func.count()).select_from(Booking).where(
            and_(
                Booking.client_id == client_id,
                Booking.booking_date == target_date,
                Booking.status.in_(["pending", "reschedule_pending", "planned", "confirmed"]),
            )
        )
    )
    count = result.scalar() or 0
    return {"count": count, "limit": 2, "can_book": count < 2}


# ==================== БЛОКИРОВКА КЛИЕНТА (Задача 9) ====================

class ClientBlockCreate(BaseModel):
    client_id: int
    hours: int = 1
    reason: str = "Частое создание и отмена записей"


@router.post("/client-blocks")
async def create_client_block(
    request: Request, body: ClientBlockCreate, db: AsyncSession = Depends(get_db)
):
    username = _get_admin_username(request)
    blocked_until = now_kz() + timedelta(hours=body.hours)
    block = ClientBlock(
        client_id=body.client_id,
        blocked_until=blocked_until,
        reason=body.reason,
    )
    db.add(block)
    await db.commit()
    await _audit(db, username, "client_blocked", f"client_id={body.client_id}, until={blocked_until}, reason={body.reason}")
    return {"ok": True, "blocked_until": str(blocked_until)}


@router.delete("/client-blocks/{block_id}")
async def delete_client_block(
    request: Request, block_id: int, db: AsyncSession = Depends(get_db)
):
    username = _get_admin_username(request)
    block = await db.get(ClientBlock, block_id)
    if not block:
        raise HTTPException(status_code=404, detail="Блокировка не найдена")
    await db.delete(block)
    await db.commit()
    await _audit(db, username, "client_unblocked", f"block_id={block_id}")
    return {"ok": True}


# ==================== ОБЪЕДИНЕНИЕ ДУБЛИРУЮЩИХСЯ ЗАПИСЕЙ (Задача 5) ====================

class MergeConfirmRequest(BaseModel):
    action: str  # "confirm" или "reject"

@router.post("/bookings/merge-duplicates")
async def merge_duplicate_bookings(
    request: Request, db: AsyncSession = Depends(get_db)
):
    """Устаревший маршрут: не меняет уже существующие записи.

    Конфликт возможен только в момент синхронизации новой офлайн-записи, где
    известен точный снимок слотов. Задним числом искать и менять дубли нельзя.
    """
    username = _get_admin_username(request)
    await _audit(db, username, "check_pending_conflicts", "Автоматическое объединение отключено: записи не изменялись")
    return {"ok": True, "merged_count": 0, "conflicts_found": 0, "details": []}


@router.post("/bookings/{booking_id}/resolve-merge")
async def resolve_merge_conflict(
    request: Request, booking_id: int, body: MergeConfirmRequest, db: AsyncSession = Depends(get_db)
):
    """Подтверждает или отклоняет слияние спорной записи"""
    username = _get_admin_username(request)
    result = await db.execute(
        select(Booking).options(selectinload(Booking.client), selectinload(Booking.instructor))
        .where(Booking.id == booking_id)
    )
    booking = result.scalar_one_or_none()
    if not booking:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    if booking.status not in ("disputed", "conflict"):
        raise HTTPException(status_code=400, detail="Эта запись не является спорной")

    if body.action == "confirm":
        # Подтверждаем запись — отменяем конфликтующую
        conflict_result = await db.execute(
            select(Booking).where(
                and_(
                    Booking.instructor_id == booking.instructor_id,
                    Booking.booking_date == booking.booking_date,
                    Booking.start_time < booking.end_time,
                    Booking.end_time > booking.start_time,
                    Booking.id != booking.id,
                    Booking.status.in_(["planned", "confirmed", "in_progress"]),
                )
            )
        )
        if conflict_result.scalars().first():
            raise HTTPException(status_code=400, detail="Этот слот уже занят другой подтвержденной записью")

        booking.status = "confirmed"
        booking.admin_confirmed = True
        booking.admin_confirmed_at = now_kz()
        booking.conflict_reason = None
        booking.booking_number = await _generate_booking_number(db)
        await db.commit()
        await _audit(db, username, "merge_resolved_confirm", f"Спорная запись #{booking_id} подтверждена после слияния")

        # Уведомляем клиента
        client = booking.client
        if client and client.telegram_id and settings.BOT_TOKEN:
            try:
                trans_label = "Механика" if booking.transmission == "manual" else "Автомат"
                service_label = "Обучение вождению" if booking.service_type == "training" else "Пробный экзамен"
                msg = (
                    f"✅ <b>Ваша заявка подтверждена!</b>\n\n"
                    f"📋 Номер записи: <b>{booking.booking_number}</b>\n\n"
                    f"📍 {booking.location}\n"
                    f"📅 {booking.booking_date.strftime('%d.%m.%Y')}\n"
                    f"🕐 {booking.start_time.strftime('%H:%M')}\n"
                    f"🚗 {service_label} ({trans_label})\n"
                    f"👨‍🏫 Инструктор: {booking.instructor.name if booking.instructor else 'Не назначен'}\n\n"
                    f"Мы напомним вам за час до начала занятия."
                )
                async with httpx.AsyncClient(timeout=10) as tg_client:
                    await _send_confirmed_booking_messages(
                        tg_client, client.telegram_id.strip(), msg
                    )
            except Exception as e:
                print(f"[merge confirm notify] ERROR: {e}")
        return {"ok": True, "booking_number": booking.booking_number}

    elif body.action == "reject":
        # Отклоняем — обе записи помечаются как конфликтующие
        booking.status = "conflict"
        booking.conflict_reason = "Конфликт: админ отклонил слияние, требуется уточнение"
        await db.commit()
        await _audit(db, username, "merge_resolved_reject", f"Спорная запись #{booking_id} отклонена, конфликт помечен")

        # Помечаем конфликтующую запись
        conflict_result = await db.execute(
            select(Booking).where(
                and_(
                    Booking.instructor_id == booking.instructor_id,
                    Booking.booking_date == booking.booking_date,
                    Booking.start_time < booking.end_time,
                    Booking.end_time > booking.start_time,
                    Booking.id != booking.id,
                    Booking.status.in_(["planned", "confirmed"]),
                )
            )
        )
        for conflict_booking in conflict_result.scalars().all():
            conflict_booking.status = "conflict"
            conflict_booking.conflict_reason = "Конфликт: админ отклонил слияние, требуется уточнение"
        await db.commit()
        return {"ok": True}

    else:
        raise HTTPException(status_code=400, detail="Некорректное действие")


# ==================== СПОРНЫЕ ЗАПИСИ (Задачи 6-7) ====================

@router.post("/bookings/{booking_id}/resolve-dispute")
async def resolve_disputed_booking(
    request: Request, booking_id: int, body: ConfirmBookingRequest, db: AsyncSession = Depends(get_db)
):
    username = _get_admin_username(request)
    result = await db.execute(
        select(Booking).options(selectinload(Booking.client), selectinload(Booking.instructor))
        .where(Booking.id == booking_id)
    )
    booking = result.scalar_one_or_none()
    if not booking:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    if booking.status not in ("disputed", "conflict"):
        raise HTTPException(status_code=400, detail="Эта запись не является спорной")

    if body.action == "confirm":
        conflict_result = await db.execute(
            select(Booking).where(
                and_(
                    Booking.instructor_id == booking.instructor_id,
                    Booking.booking_date == booking.booking_date,
                    Booking.start_time < booking.end_time,
                    Booking.end_time > booking.start_time,
                    Booking.id != booking.id,
                    Booking.status.in_(["planned", "confirmed", "in_progress"]),
                )
            )
        )
        if conflict_result.scalars().first():
            raise HTTPException(status_code=400, detail="Этот слот уже занят другой подтвержденной записью")
        booking.status = "confirmed"
        booking.admin_confirmed = True
        booking.admin_confirmed_at = now_kz()
        booking.conflict_reason = None
        booking.booking_number = await _generate_booking_number(db)
        await db.commit()
        await _audit(db, username, "dispute_resolved_confirm", f"Спорная запись #{booking_id} подтверждена")
        client = booking.client
        if client and client.telegram_id and settings.BOT_TOKEN:
            try:
                trans_label = "Механика" if booking.transmission == "manual" else "Автомат"
                service_label = "Обучение вождению" if booking.service_type == "training" else "Пробный экзамен"
                msg = (
                    f"✅ <b>Ваша заявка подтверждена!</b>\n\n"
                    f"📋 Номер записи: <b>{booking.booking_number}</b>\n\n"
                    f"📍 {booking.location}\n"
                    f"📅 {booking.booking_date.strftime('%d.%m.%Y')}\n"
                    f"🕐 {booking.start_time.strftime('%H:%M')}\n"
                    f"🚗 {service_label} ({trans_label})\n"
                    f"👨‍🏫 Инструктор: {booking.instructor.name if booking.instructor else 'Не назначен'}\n\n"
                    f"Мы напомним вам за час до начала занятия."
                )
                async with httpx.AsyncClient(timeout=10) as tg_client:
                    await _send_confirmed_booking_messages(
                        tg_client, client.telegram_id.strip(), msg
                    )
            except Exception as e:
                print(f"[dispute confirm notify] ERROR: {e}")
        return {"ok": True, "booking_number": booking.booking_number}

    elif body.action == "reject":
        booking.status = "cancelled"
        booking.conflict_reason = None
        await db.commit()
        await _audit(db, username, "dispute_resolved_reject", f"Спорная запись #{booking_id} отклонена")
        client = booking.client
        if client and client.telegram_id and settings.BOT_TOKEN:
            try:
                msg = (
                    f"❌ <b>Ваша заявка отклонена.</b>\n\n"
                    f"Выбранное время больше недоступно. Пожалуйста, выберите другое время через «Записаться»."
                )
                async with httpx.AsyncClient(timeout=10) as tg_client:
                    await tg_client.post(
                        f"https://api.telegram.org/bot{settings.BOT_TOKEN}/sendMessage",
                        json={"chat_id": client.telegram_id.strip(), "text": msg, "parse_mode": "HTML"},
                    )
            except Exception as e:
                print(f"[dispute reject notify] ERROR: {e}")
        return {"ok": True}

    else:
        raise HTTPException(status_code=400, detail="Некорректное действие")


# ==================== МКПП ОГРАНИЧЕНИЕ (Задача 20) ====================

@router.get("/slots/manual-check")
async def check_manual_slot(
    request: Request, booking_date: str, start_time: str, service_type: str = "training",
    db: AsyncSession = Depends(get_db),
):
    """Returns whether at least one eligible manual instructor is free."""
    _get_admin_username(request)
    bdate = date.fromisoformat(booking_date)
    stime = time.fromisoformat(start_time)
    duration = settings.TRAINING_DURATION_MINUTES
    et_delta = timedelta(hours=stime.hour, minutes=stime.minute) + timedelta(minutes=duration)
    etime = time(int(et_delta.total_seconds() // 3600), int((et_delta.total_seconds() % 3600) // 60))

    busy_ids = await get_busy_instructor_ids(db, bdate, stime, etime)
    instructors = (await db.execute(select(Instructor).where(Instructor.is_active == True))).scalars().all()
    available = [
        instructor for instructor in instructors
        if await is_instructor_available(
            db, instructor, bdate, stime, etime, "manual", busy_ids,
            allow_duty=True, service_type=service_type,
        )
    ]
    return {"available": bool(available), "available_instructors_count": len(available)}


# ==================== ВЫХОДНЫЕ ИНСТРУКТОРОВ С ПРИОРИТЕТОМ (Задача 21) ====================

@router.get("/instructors/all-days-off")
async def get_all_instructors_days_off(
    request: Request, db: AsyncSession = Depends(get_db)
):
    """Возвращает все выходные всех инструкторов для визуализации занятых дней"""
    _get_admin_username(request)
    result = await db.execute(
        select(InstructorDayOff, Instructor.name, Instructor.is_lead)
        .join(Instructor, InstructorDayOff.instructor_id == Instructor.id)
        .order_by(InstructorDayOff.day_off_date)
    )
    rows = result.all()
    days_off_map = {}
    for day_off, instr_name, is_lead in rows:
        date_str = str(day_off.day_off_date)
        if date_str not in days_off_map:
            days_off_map[date_str] = []
        days_off_map[date_str].append({
            "instructor_id": day_off.instructor_id,
            "instructor_name": instr_name,
            "is_lead": is_lead,
            "day_off_id": day_off.id,
        })
    return {"days_off": days_off_map}


@router.put("/instructors/{instructor_id}/days-off-with-priority")
async def update_instructor_days_off_with_priority(
    request: Request,
    instructor_id: int,
    body: InstructorDaysOffUpdate,
    db: AsyncSession = Depends(get_db)
):
    """Обновить выходные с учётом приоритета главного инструктора.
    Если главный инструктор выбирает день, который уже выходной у другого —
    этот день освобождается у другого инструктора."""
    username = _get_admin_username(request)

    instructor = await db.get(Instructor, instructor_id)
    if not instructor:
        raise HTTPException(status_code=404, detail="Instructor not found")

    from sqlalchemy import delete as sa_delete

    if instructor.is_lead:
        new_dates = set()
        for date_str in body.days_off_dates:
            try:
                new_dates.add(date.fromisoformat(date_str))
            except ValueError:
                continue

        await _ensure_days_off_have_no_active_bookings(db, instructor, new_dates)

        other_result = await db.execute(
            select(InstructorDayOff).where(
                and_(
                    InstructorDayOff.instructor_id != instructor_id,
                    InstructorDayOff.day_off_date.in_(new_dates),
                )
            )
        )
        conflicting = other_result.scalars().all()
        removed_names = []
        for conflict in conflicting:
            other_instr = await db.get(Instructor, conflict.instructor_id)
            removed_names.append(other_instr.name if other_instr else str(conflict.instructor_id))
            await db.delete(conflict)

        await db.execute(sa_delete(InstructorDayOff).where(InstructorDayOff.instructor_id == instructor_id))

        for d in new_dates:
            db.add(InstructorDayOff(instructor_id=instructor_id, day_off_date=d))

        await db.commit()
        await _audit(db, username, "update_lead_days_off", f"instructor_id={instructor_id}, dates={len(new_dates)}, removed_from={removed_names}")
        return {"ok": True, "removed_from_others": removed_names}
    else:
        new_dates = []
        for date_str in body.days_off_dates:
            try:
                new_dates.append(date.fromisoformat(date_str))
            except ValueError:
                continue

        await _ensure_days_off_have_no_active_bookings(db, instructor, set(new_dates))

        lead_result = await db.execute(
            select(Instructor).where(Instructor.is_lead == True)
        )
        lead = lead_result.scalar_one_or_none()
        if lead:
            lead_days_result = await db.execute(
                select(InstructorDayOff.day_off_date).where(
                    InstructorDayOff.instructor_id == lead.id
                )
            )
            lead_days = {r[0] for r in lead_days_result.all()}
            conflicts = [d for d in new_dates if d in lead_days]
            if conflicts:
                conflict_strs = [str(d) for d in conflicts]
                raise HTTPException(
                    status_code=400,
                    detail=f"Эти дни являются выходными главного инструктора и не могут быть выбраны: {', '.join(conflict_strs)}"
                )

        other_result = await db.execute(
            select(InstructorDayOff).where(
                and_(
                    InstructorDayOff.instructor_id != instructor_id,
                    InstructorDayOff.day_off_date.in_(new_dates),
                )
            )
        )
        other_days_off = other_result.scalars().all()
        if other_days_off:
            taken_dates = set()
            for od in other_days_off:
                other_instr = await db.get(Instructor, od.instructor_id)
                taken_dates.add(f"{od.day_off_date} ({other_instr.name if other_instr else '?'})")
            raise HTTPException(
                status_code=400,
                detail=f"Эти дни уже выбраны как выходные другими инструкторами: {', '.join(taken_dates)}"
            )

        await db.execute(sa_delete(InstructorDayOff).where(InstructorDayOff.instructor_id == instructor_id))
        for d in new_dates:
            db.add(InstructorDayOff(instructor_id=instructor_id, day_off_date=d))

        await db.commit()
        await _audit(db, username, "update_instructor_days_off", f"instructor_id={instructor_id}, dates={len(new_dates)}")
        return {"ok": True}


# ==================== ПАКЕТЫ CRUD (Задача 17) ====================

PACKAGE_OFFERS = {
    (6, 55000),
    (10, 90000),
}


class PackageCreate(BaseModel):
    name: str = "Пакет 6 занятий"
    sessions_count: int = 6
    price: int = 55000
    description: Optional[str] = None
    validity_days: int = 30
    bonus_exam: bool = False


class PackageUpdate(BaseModel):
    name: Optional[str] = None
    sessions_count: Optional[int] = None
    price: Optional[int] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None
    validity_days: Optional[int] = None
    bonus_exam: Optional[bool] = None


@router.get("/packages")
async def list_packages(request: Request, db: AsyncSession = Depends(get_db)):
    _get_admin_username(request)
    result = await db.execute(select(Package).order_by(Package.id))
    packages = result.scalars().all()
    assigned_rows = await db.execute(
        select(ClientPackage.package_id, Client.id, Client.name, Client.phone)
        .join(Client, Client.id == ClientPackage.client_id)
        .order_by(ClientPackage.purchased_at.desc())
    )
    assigned_to = {}
    for package_id, client_id, client_name, client_phone in assigned_rows.all():
        assigned_to.setdefault(package_id, {"id": client_id, "name": client_name, "phone": client_phone})
    return [
        {
            "id": p.id,
            "name": p.name,
            "sessions_count": p.sessions_count,
            "price": p.price,
            "description": p.description,
            "is_active": p.is_active,
            "validity_days": p.validity_days,
            "bonus_exam": p.bonus_exam,
            "code": p.code,
            "assigned_client_id": assigned_to.get(p.id, {}).get("id"),
            "assigned_client_name": assigned_to.get(p.id, {}).get("name"),
            "assigned_client_phone": assigned_to.get(p.id, {}).get("phone"),
            "is_available": p.is_active and p.id not in assigned_to,
        }
        for p in packages
    ]


@router.post("/packages")
async def create_package(request: Request, body: PackageCreate, db: AsyncSession = Depends(get_db)):
    username = _get_admin_username(request)
    operation_id = _idempotency_key(request)
    if operation_id:
        existing = (await db.execute(select(Package).where(Package.offline_operation_id == operation_id))).scalar_one_or_none()
        if existing:
            return {"ok": True, "id": existing.id, "code": existing.code}
    # Keep the approved offers server-side so a stale/offline admin page cannot
    # issue a package with an arbitrary session count or price.
    if (body.sessions_count, body.price) not in PACKAGE_OFFERS:
        raise HTTPException(
            status_code=400,
            detail="Доступны только пакеты: 6 занятий за 55 000 ₸ или 10 занятий за 90 000 ₸",
        )
    package_code = f"PKG-{secrets.token_hex(3).upper()}"
    while (await db.execute(select(Package.id).where(Package.code == package_code))).scalar_one_or_none():
        package_code = f"PKG-{secrets.token_hex(3).upper()}"
    pkg = Package(
        name=body.name,
        sessions_count=body.sessions_count,
        price=body.price,
        description=body.description,
        validity_days=max(1, body.validity_days),
        bonus_exam=True,
        code=package_code,
        offline_operation_id=operation_id,
    )
    db.add(pkg)
    existing = await _commit_idempotent_create(db, Package, operation_id)
    if existing:
        return {"ok": True, "id": existing.id, "code": existing.code}
    await db.refresh(pkg)
    await _audit(
        db,
        username,
        "package_created",
        f"Администратор создал пакет «{pkg.name}» ({pkg.code}): {pkg.sessions_count} занятий за {pkg.price} ₸.",
    )
    return {"ok": True, "id": pkg.id, "code": pkg.code}


@router.put("/packages/{package_id}")
async def update_package(request: Request, package_id: int, body: PackageUpdate, db: AsyncSession = Depends(get_db)):
    username = _get_admin_username(request)
    pkg = await db.get(Package, package_id)
    if not pkg:
        raise HTTPException(status_code=404, detail="Пакет не найден")
    if body.name is not None:
        pkg.name = body.name
    if body.sessions_count is not None:
        pkg.sessions_count = body.sessions_count
    if body.price is not None:
        pkg.price = body.price
    if body.description is not None:
        pkg.description = body.description
    if body.is_active is not None:
        pkg.is_active = body.is_active
    if body.validity_days is not None:
        pkg.validity_days = max(1, body.validity_days)
    if body.bonus_exam is not None:
        pkg.bonus_exam = body.bonus_exam
    await db.commit()
    await _audit(db, username, "package_updated", f"Администратор изменил пакет «{pkg.name}» ({pkg.code}).")
    return {"ok": True}


@router.delete("/packages/{package_id}")
async def delete_package(request: Request, package_id: int, db: AsyncSession = Depends(get_db)):
    username = _get_admin_username(request)
    pkg = await db.get(Package, package_id)
    if not pkg:
        raise HTTPException(status_code=404, detail="Пакет не найден")
    package_label = f"«{pkg.name}» ({pkg.code})"
    await db.delete(pkg)
    await db.commit()
    await _audit(db, username, "package_deleted", f"Администратор удалил пакет {package_label}.")
    return {"ok": True}


# ==================== ПОДСЧЁТ ЗАНЯТИЙ ПО ПАКЕТУ (Задача 18) ====================

@router.get("/clients/{client_id}/package-sessions")
async def get_client_package_sessions(
    request: Request, client_id: int, db: AsyncSession = Depends(get_db)
):
    _get_admin_username(request)
    cp_result = await db.execute(
        select(ClientPackage).options(selectinload(ClientPackage.package))
        .where(and_(ClientPackage.client_id == client_id, ClientPackage.is_active == True))
    )
    client_packages = cp_result.scalars().all()
    result = []
    for cp in client_packages:
        # remaining_sessions is reserved when a package lesson is booked and
        # restored if that booking is cancelled. Do not subtract completed
        # lessons again: that was the source of incorrect 4/6 → 2/6 displays.
        remaining = max(0, cp.remaining_sessions)
        used = max(0, (cp.package.sessions_count if cp.package else 0) - remaining)
        result.append({
            "package_id": cp.package_id,
            "package_name": cp.package.name if cp.package else "Пакет",
            "total_sessions": cp.package.sessions_count if cp.package else 0,
            "remaining_sessions": remaining,
            "used_sessions": used,
            "code": cp.package.code if cp.package else None,
            "expires_at": cp.expires_at.isoformat() if cp.expires_at else None,
            "remaining_bonus_exams": cp.remaining_bonus_exams,
        })
    return result


# ==================== CONFLICT RESOLUTION (Задача 5) ====================

class ConflictResolveRequest(BaseModel):
    action: str  # "confirm" | "reject" ("delete" is kept for older clients)
    booking_ids: list[int]
    rejection_reason: Optional[str] = None


@router.get("/bookings/conflicts")
async def get_conflict_groups(request: Request, db: AsyncSession = Depends(get_db)):
    """Возвращает группы конфликтных записей, сгруппированных по слоту (инструктор + дата + время)."""
    _get_admin_username(request)

    result = await db.execute(
        select(Booking)
        .options(selectinload(Booking.client), selectinload(Booking.instructor))
        .where(Booking.status.in_(["conflict", "disputed"]))
        .order_by(Booking.booking_date.asc(), Booking.start_time.asc())
    )
    bookings = result.scalars().all()

    # Группируем по слоту: (instructor_id, booking_date, start_time)
    from itertools import groupby
    from operator import attrgetter

    def slot_key(b):
        return (b.instructor_id, b.booking_date.isoformat(), b.start_time.strftime("%H:%M"))

    bookings_sorted = sorted(bookings, key=slot_key)
    groups = []
    for key, group_iter in groupby(bookings_sorted, key=slot_key):
        items = list(group_iter)
        group_bookings = []
        for b in items:
            # Определяем тип: ручная или онлайн
            is_manual = b.source in ("admin", "admin_offline")
            group_bookings.append({
                "id": b.id,
                "client_name": b.client.name if b.client else "—",
                "client_phone": b.client.phone if b.client else "—",
                "instructor_name": b.instructor.name if b.instructor else "—",
                "instructor_id": b.instructor_id,
                "booking_date": b.booking_date.isoformat(),
                "start_time": b.start_time.strftime("%H:%M"),
                "end_time": b.end_time.strftime("%H:%M"),
                "service_type": b.service_type,
                "transmission": b.transmission,
                "location": b.location,
                "status": b.status,
                "source": b.source,
                "conflict_reason": b.conflict_reason,
                "is_manual": is_manual,
                "created_at": b.created_at.isoformat() if b.created_at else None,
            })
        instructor_name = items[0].instructor.name if items[0].instructor else "—"
        groups.append({
            "slot": {
                "instructor_id": key[0],
                "instructor_name": instructor_name,
                "date": key[1],
                "time": key[2],
            },
            "bookings": group_bookings,
            "count": len(group_bookings),
        })

    return {"groups": groups, "total": len(bookings)}


@router.post("/bookings/check-pending-conflicts")
async def check_pending_conflicts(request: Request, db: AsyncSession = Depends(get_db)):
    """Возвращает результат без изменения уже существующих записей.

    Конфликт создаётся только непосредственно во время синхронизации новой
    офлайн-записи с новой клиентской заявкой. Повторная проверка не имеет
    права задним числом менять записи, которые уже есть в админке.
    """
    username = _get_admin_username(request)
    await _audit(db, username, "check_pending_conflicts", "Повторная проверка: записи не изменялись")
    return {"ok": True, "merged_count": 0, "duplicates_count": 0, "conflicts_count": 0, "duplicates": [], "conflicts": []}


@router.post("/bookings/resolve-conflict")
async def resolve_conflict(
    request: Request, body: ConflictResolveRequest, db: AsyncSession = Depends(get_db)
):
    """Рассматривает одну конфликтную запись тем же процессом, что и заявку."""
    if len(body.booking_ids) != 1:
        raise HTTPException(status_code=400, detail="Для рассмотрения выберите одну конфликтную запись")

    action = "reject" if body.action == "delete" else body.action
    if action not in ("confirm", "reject"):
        raise HTTPException(status_code=400, detail="action должен быть 'confirm' или 'reject'")

    booking = await db.get(Booking, body.booking_ids[0])
    if not booking or booking.status not in ("conflict", "disputed"):
        raise HTTPException(status_code=400, detail="Не найдена конфликтная запись")

    return await confirm_booking(
        request,
        body.booking_ids[0],
        ConfirmBookingRequest(action=action, rejection_reason=body.rejection_reason),
        db,
    )


# ==================== OFFLINE SYNC (Задача 3) ====================
@router.post("/offline-sync")
async def offline_sync(
    request: Request,
    operations: list = Body(...),
    db: AsyncSession = Depends(get_db)
):
    """Принимает очередь офлайн-операций и выполняет их последовательно"""
    username = _get_admin_username(request)
    results = []
    local_id_map: dict[int, int] = {}
    local_value_map: dict[str, str] = {}
    for op in operations:
        method = op.get("method", "POST")
        path = op.get("path", "")
        body = op.get("body")
        op_id = op.get("id", "unknown")
        for local_id, server_id in local_id_map.items():
            path = path.replace(f"/{local_id}", f"/{server_id}")
        if isinstance(body, dict):
            body = {
                key: (
                    local_id_map.get(value, value)
                    if key.endswith("_id") and isinstance(value, int)
                    else local_value_map.get(value, value) if isinstance(value, str) else value
                )
                for key, value in body.items()
            }
        try:
            # A manual form contains a client name/phone, not a database
            # client_id. Reuse the normal validated route so an offline entry
            # gets the same availability, MKPP and two-bookings-per-day checks.
            if method == "POST" and path.rstrip("/") == "/bookings/manual" and body:
                # This rule is intentionally evaluated only while replaying a
                # manual entry that was made offline.  Two ordinary online
                # applications are never converted into a dispute merely
                # because they belong to the same client and date.
                offline_date = date.fromisoformat(body["booking_date"])
                offline_start = time.fromisoformat(body["start_time"])
                pending_rows = await db.execute(
                    select(Booking).options(selectinload(Booking.client)).where(and_(
                        Booking.booking_date == offline_date,
                        Booking.status == "pending",
                    ))
                )
                pending_bookings = pending_rows.scalars().all()
                # Same phone + exact start time means the client and the
                # offline administrator made the very same appointment while
                # the connection was absent.  This is a duplicate, not a
                # dispute: keep the confirmed offline record as the single
                # booking and retain the name entered by the administrator.
                duplicate_pending_bookings = [
                    pending for pending in pending_bookings
                    if (
                        body.get("client_phone")
                        and pending.client
                        and phones_match(pending.client.phone, body["client_phone"])
                        and pending.start_time == offline_start
                    )
                ]
                for duplicate in duplicate_pending_bookings:
                    await _restore_booking_package_if_needed(db, duplicate)
                    await db.delete(duplicate)

                # A request by the same client for a different time is not a
                # slot collision.  It can be a real second lesson or a call
                # made from an outdated application screen, so keep it for an
                # explicit admin decision after the offline record is saved.
                disputed_pending_ids = [
                    pending.id
                    for pending in pending_bookings
                    if (
                        body.get("client_phone")
                        and pending.client
                        and phones_match(pending.client.phone, body["client_phone"])
                        and pending.start_time != offline_start
                    )
                ]
                created = await create_manual_booking(
                    request, ManualBookingCreate(**body, offline_operation_id=str(op_id)), db
                )
                # Только эта запись помечается как офлайн. Обычные записи,
                # уже существовавшие в админке, никогда не участвуют в
                # последующей проверке конфликтов.
                created_booking = await db.get(Booking, created["booking_id"])
                if created_booking:
                    created_booking.source = "admin_offline"
                    if duplicate_pending_bookings and body.get("client_name"):
                        offline_client = await db.get(Client, created_booking.client_id)
                        if offline_client:
                            offline_client.name = body["client_name"].strip()
                    await db.commit()
                if disputed_pending_ids:
                    disputed_rows = (await db.execute(
                        select(Booking).options(selectinload(Booking.client)).where(and_(
                            Booking.id.in_(disputed_pending_ids),
                            Booking.status.in_(["pending", "conflict"]),
                        ))
                    )).scalars().all()
                    for pending in disputed_rows:
                        pending.status = "disputed"
                        pending.conflict_reason = (
                            "У клиента уже есть ручная запись на "
                            f"{offline_date.strftime('%d.%m.%Y')} в {offline_start.strftime('%H:%M')}; "
                            f"онлайн-заявка создана на {pending.start_time.strftime('%H:%M')}. "
                            "Уточните, нужна ли клиенту вторая запись."
                        )
                    if disputed_rows:
                        await db.commit()
                        await _audit(
                            db,
                            username,
                            "offline_same_client_dispute",
                            "Во время офлайн-синхронизации спорные онлайн-заявки того же клиента: "
                            + "; ".join(
                                f"#{pending.id} ({pending.start_time.strftime('%H:%M')})"
                                for pending in disputed_rows
                            ),
                        )
                for duplicate in duplicate_pending_bookings:
                    await _audit(
                        db,
                        username,
                        "booking_merged",
                        f"Онлайн-заявка #{duplicate.id} объединена с офлайн-записью #{created['booking_id']}",
                    )
                local_client_id = op.get("local_client_id")
                if local_client_id is not None and created.get("client_id") is not None:
                    local_id_map[int(local_client_id)] = int(created["client_id"])
                # A client may have cancelled their online request while the
                # administrator was offline and then been entered manually.
                # Never let that delayed cancellation silently decide the
                # fate of the administrator's record: mark it for a call.
                if body.get("client_phone"):
                    cancelled_rows = await db.execute(
                        select(Booking).options(selectinload(Booking.client)).where(and_(
                            Booking.booking_date == offline_date,
                            Booking.status == "cancelled",
                            Booking.source.in_(["telegram", "mobile"]),
                        ))
                    )
                    if any(b.client and phones_match(b.client.phone, body.get("client_phone")) for b in cancelled_rows.scalars()):
                        manual_booking = await db.get(Booking, created["booking_id"])
                        manual_booking.status = "disputed"
                        manual_booking.conflict_reason = "Клиент отменил онлайн-заявку во время отсутствия связи; уточните актуальность ручной записи"
                        await db.commit()
                results.append({"id": op_id, "status": "ok", **created})
                continue
            if method == "PUT" and path.endswith("/edit") and "/bookings/" in path and body:
                parts = path.strip("/").split("/")
                booking_id = int(parts[-2]) if len(parts) >= 3 and parts[-2].isdigit() else None
                booking = await db.get(Booking, booking_id) if booking_id else None
                if not booking:
                    results.append({"id": op_id, "status": "error", "detail": "Запись уже отсутствует на сервере"})
                    continue
                target_date = date.fromisoformat(body["new_date"]) if body.get("new_date") else booking.booking_date
                target_start = time.fromisoformat(body["new_start_time"]) if body.get("new_start_time") else booking.start_time
                target_instructor = body.get("new_instructor_id") or booking.instructor_id
                duration = settings.TRAINING_DURATION_MINUTES if booking.service_type == ServiceType.TRAINING else settings.EXAM_DURATION_MINUTES
                target_end = (datetime.combine(target_date, target_start) + timedelta(minutes=duration)).time()
                pending = (await db.execute(select(Booking).where(and_(
                    Booking.id != booking.id, Booking.status == "pending", Booking.booking_date == target_date,
                    Booking.instructor_id == target_instructor, Booking.start_time < target_end, Booking.end_time > target_start,
                )))).scalars().all()
                for pending_booking in pending:
                    pending_booking.status = "conflict"
                    pending_booking.conflict_reason = "Пересекается с изменением записи администратора, сделанным офлайн"
                if pending:
                    await db.commit()
                updated = await edit_booking(request, booking_id, EditBookingRequest(**body), db)
                results.append({"id": op_id, "status": "ok", **updated})
                continue
            if method == "PUT" and path.endswith("/status") and "/bookings/" in path and body:
                parts = path.strip("/").split("/")
                booking_id = int(parts[-2]) if len(parts) >= 3 and parts[-2].isdigit() else None
                new_status = body.get("status")
                if not booking_id or new_status not in {"planned", "confirmed", "completed", "cancelled", "no_show"}:
                    results.append({"id": op_id, "status": "error", "detail": "Некорректная отмена офлайн-записи"})
                    continue
                booking = await db.get(Booking, booking_id)
                if not booking:
                    results.append({"id": op_id, "status": "error", "detail": "Запись уже отсутствует на сервере"})
                    continue
                booking.status = new_status
                booking.completed_at = now_kz() if new_status == "completed" else None
                booking.archived_at = None
                await db.commit()
                results.append({"id": op_id, "status": "ok"})
                continue
            if method == "DELETE" and "/bookings/" in path:
                parts = path.strip("/").split("/")
                booking_id = int(parts[-1]) if parts[-1].isdigit() else None
                if not booking_id:
                    results.append({"id": op_id, "status": "error", "detail": "Invalid path"})
                    continue
                booking = await db.get(Booking, booking_id)
                if not booking:
                    results.append({"id": op_id, "status": "ok", "already_absent": True})
                    continue
                booking.status = "cancelled"
                await db.commit()
                results.append({"id": op_id, "status": "ok"})
                continue
            else:
                # Replay every other permitted admin mutation through the real
                # FastAPI route. This keeps validation and audit behavior
                # identical to an online click instead of maintaining a
                # second, incomplete set of CRUD rules here.
                cookie = request.headers.get("cookie", "")
                replay_headers = {"cookie": cookie} if cookie else {}
                # The local queue id is also the idempotency key used by the
                # initial browser request.  A replay after a lost response
                # therefore resolves to the same newly created entity.
                if op_id and op_id != "unknown":
                    replay_headers["x-idempotency-key"] = str(op_id)
                transport = httpx.ASGITransport(app=request.app)
                async with httpx.AsyncClient(transport=transport, base_url="http://offline-replay") as replay:
                    replay_response = await replay.request(
                        method,
                        f"/api/admin{path}",
                        headers=replay_headers,
                        json=body if body is not None else None,
                    )
                if replay_response.is_success:
                    payload = replay_response.json() if replay_response.content else {}
                    local_id = op.get("local_id")
                    server_id = next((payload.get(key) for key in ("id", "client_id", "booking_id", "entry_id") if payload.get(key)), None) if isinstance(payload, dict) else None
                    if local_id is not None and server_id is not None:
                        local_id_map[int(local_id)] = int(server_id)
                    local_code = op.get("local_code")
                    if local_code and isinstance(payload, dict) and payload.get("code"):
                        local_value_map[str(local_code)] = str(payload["code"])
                    results.append({"id": op_id, "status": "ok", **(payload if isinstance(payload, dict) else {})})
                elif method == "DELETE" and replay_response.status_code == 404:
                    # DELETE is idempotent: if a record was removed before an
                    # offline client reconnects, the requested end state has
                    # already been reached. Remove this stale queue item
                    # instead of leaving the administrator permanently in a
                    # false "offline" state.
                    results.append({"id": op_id, "status": "ok", "already_absent": True})
                else:
                    detail = replay_response.json().get("detail", replay_response.text)
                    results.append({"id": op_id, "status": "error", "detail": detail})
        except Exception as e:
            await db.rollback()
            results.append({"id": op_id, "status": "error", "detail": str(e)})
    return {
        "results": results,
        "local_id_map": {str(key): value for key, value in local_id_map.items()},
        "local_value_map": local_value_map,
    }
