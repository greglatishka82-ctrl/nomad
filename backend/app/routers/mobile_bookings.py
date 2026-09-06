"""
Мобильное API - Бронирование занятий
GET    /api/mobile/bookings?filter=upcoming|history
GET    /api/mobile/bookings/history?page=1
GET    /api/mobile/bookings/{id}
POST   /api/mobile/bookings
DELETE /api/mobile/bookings/{id}
PUT    /api/mobile/bookings/{id}/reschedule
POST   /api/mobile/bookings/{id}/confirm
POST   /api/mobile/bookings/{id}/rate
GET    /api/mobile/slots
GET    /api/mobile/instructors
"""
from datetime import date, datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

KZ_TZ = ZoneInfo("Asia/Almaty")

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, and_, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings, TIMEZONE
from app.database import get_db
from app.models.models import (
    Booking, Certificate, Client, Instructor, RatingRecord,
    ServiceType, TransmissionType, RatingVote, InstructorGender, ReferralRecord,
    AuditLog, NotificationSent, MobileBooking, Event, ClientPackage
)
from app.services.booking_service import (
    _is_instructor_available, get_available_slots, get_available_slots_for_instructor,
    find_best_instructor, find_best_instructor_with_location, reserve_available_vehicle
)
from app.routers.mobile_auth import get_current_user

router = APIRouter(prefix="/api/mobile", tags=["mobile-bookings"])
HISTORY_PAGE_SIZE = 7


async def _block_after_repeated_cancellations(db: AsyncSession, client_id: int) -> None:
    """Compatibility check: the final block is created by the admin decision."""
    from app.models.models import ClientBlock
    now = datetime.now(KZ_TZ).replace(tzinfo=None)
    since = now - timedelta(hours=24)
    cancellations = (await db.execute(select(func.count()).select_from(AuditLog).where(
        AuditLog.action == "booking_cancelled",
        AuditLog.created_at >= since,
        AuditLog.details.contains(f"(id={client_id})"),
    ))).scalar() or 0
    # Older audit messages contain the client name, not ID. Count the current
    # cancellation as well; a block never happens before the third one.
    if cancellations < 5:
        return
    active = (await db.execute(select(ClientBlock).where(
        ClientBlock.client_id == client_id, ClientBlock.blocked_until > now,
    ))).scalar_one_or_none()
    if not active:
        db.add(ClientBlock(client_id=client_id, blocked_until=now + timedelta(hours=24),
                           reason="Пять отмен записей за последние 24 часа"))


async def _ensure_client_is_not_blocked(db: AsyncSession, client_id: int) -> None:
    from app.models.models import ClientBlock
    now = datetime.now(KZ_TZ).replace(tzinfo=None)
    block = (await db.execute(select(ClientBlock).where(
        ClientBlock.client_id == client_id, ClientBlock.blocked_until > now,
    ))).scalars().first()
    if block:
        raise HTTPException(
            status_code=403,
            detail="Для вашего аккаунта временно ограничены создание, отмена и перенос записей. "
                   f"Ограничение действует до {block.blocked_until.strftime('%d.%m.%Y %H:%M')}.",
        )


async def _add_support_notice(db: AsyncSession, client_id: int, text: str) -> None:
    from app.models.models import SupportMessage
    db.add(SupportMessage(
        client_id=client_id,
        channel="client",
        sender="admin",
        text=text,
        is_read=False,
        is_admin_read=True,
    ))


async def _consume_client_reschedule_slot(db: AsyncSession, client_id: int) -> tuple[Client, int]:
    """Atomically apply one global self-service reschedule slot to a client."""
    now = datetime.now(KZ_TZ).replace(tzinfo=None)
    client = (await db.execute(
        select(Client).where(Client.id == client_id).with_for_update()
    )).scalar_one()
    window_started = client.reschedule_window_started_at

    if not window_started:
        since = now - timedelta(hours=24)
        audit_filters = (
            AuditLog.action.in_(["booking_reschedule_requested", "booking_rescheduled"]),
            AuditLog.created_at >= since,
            AuditLog.details.contains(f"(id={client_id})"),
        )
        event_filters = (
            Event.event_type == "booking_rescheduled",
            Event.client_id == client_id,
            Event.source.in_(["telegram", "mobile"]),
            Event.created_at >= since,
        )
        audit_count = (await db.execute(select(func.count()).select_from(AuditLog).where(*audit_filters))).scalar() or 0
        legacy_event_count = (await db.execute(select(func.count()).select_from(Event).where(*event_filters))).scalar() or 0
        audit_first = (await db.execute(select(func.min(AuditLog.created_at)).where(*audit_filters))).scalar_one_or_none()
        event_first = (await db.execute(select(func.min(Event.created_at)).where(*event_filters))).scalar_one_or_none()
        client.reschedule_count_24h = min(3, audit_count + legacy_event_count)
        client.reschedule_window_started_at = min(
            (value for value in (audit_first, event_first) if value is not None),
            default=now,
        )
    elif now - window_started >= timedelta(hours=24):
        client.reschedule_count_24h = 0
        client.reschedule_window_started_at = now

    if client.reschedule_count_24h >= 3:
        raise HTTPException(
            status_code=429,
            detail=(
                "Лимит переносов исчерпан: за последние 24 часа можно самостоятельно "
                "перенести запись не более 3 раз. Следующий перенос будет доступен позже."
            ),
        )

    client.reschedule_count_24h += 1
    return client, client.reschedule_count_24h


async def _restore_package_session(db: AsyncSession, booking: Booking) -> None:
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


class CreateBookingRequest(BaseModel):
    instructor_id: Optional[int] = None
    booking_date: date
    start_time: str  # "HH:MM"
    service_type: str
    location: Optional[str] = None
    transmission: str = "both"
    instructor_gender: str = "any"
    certificate_code: Optional[str] = None


class RateBookingRequest(BaseModel):
    vote: str  # "good", "normal", "bad"


class ConfirmBookingRequest(BaseModel):
    coming: bool = True  # True — придёт, False — не придёт (отмена записи)


class RescheduleBookingRequest(BaseModel):
    new_date: str  # "YYYY-MM-DD"
    new_start_time: str  # "HH:MM"


def _value(v):
    return v.value if hasattr(v, "value") else v


def _booking_date_window() -> tuple[date, date]:
    now = datetime.now(TIMEZONE)
    start = now.date()
    if now.time() >= time(settings.WORKING_HOURS_END, 0):
        start = start + timedelta(days=1)
    return start, start + timedelta(days=6)


def _is_date_in_booking_window(target_date: date) -> bool:
    first_day, last_day = _booking_date_window()
    return first_day <= target_date <= last_day


async def _serialize_booking(b, db, instructor=None, rating_vote=None):
    package_purchase = None
    if b.package_id:
        package_purchase = (await db.execute(select(ClientPackage).where(
            ClientPackage.client_id == b.client_id,
            ClientPackage.package_id == b.package_id,
        ).order_by(ClientPackage.purchased_at.desc()))).scalars().first()
    result = {
        "id": b.id,
        "service_type": _value(b.service_type),
        "transmission": _value(b.transmission) or "both",
        "location": b.location,
        "booking_date": b.booking_date.isoformat(),
        "start_time": b.start_time.strftime("%H:%M"),
        "end_time": b.end_time.strftime("%H:%M") if b.end_time else "",
        "status": _value(b.status),
        "price": int(b.price),
        "base_price": int(b.base_price) if b.base_price is not None else int(b.price),
        "certificate_amount": int(b.certificate_amount or 0),
        "referral_discount_amount": int(b.referral_discount_amount or 0),
        "payment_status": b.payment_status or "unpaid",
        "paid_amount": int(b.paid_amount or 0),
        "rating_vote": rating_vote,
        "confirmed_by_client": bool(b.confirmed_by_client),
        "booking_number": b.booking_number,
        "package_sessions_used": (
            max(0, (package_purchase.package.sessions_count if package_purchase and package_purchase.package else 0)
                - package_purchase.remaining_sessions)
            if package_purchase and not b.package_bonus_exam_used else None
        ),
        "package_sessions_total": package_purchase.package.sessions_count if package_purchase and package_purchase.package else None,
        "package_remaining_sessions": package_purchase.remaining_sessions if package_purchase else None,
        "package_code": package_purchase.package.code if package_purchase and package_purchase.package else None,
        "created_at": b.created_at.isoformat(),
        "instructor": {
            "id": instructor.id,
            "name": instructor.name,
            "transmission": instructor.transmission if hasattr(instructor.transmission, 'value') else str(instructor.transmission),
            "experience_years": instructor.experience_years,
            "rating": instructor.rating,
            "description": instructor.description or "",
            "avatar_url": instructor.avatar_url,
        } if instructor else None,
    }

    return result


def _history_filter(now: datetime):
    return or_(
        Booking.booking_date < now.date(),
        and_(Booking.booking_date == now.date(), Booking.start_time < now.time()),
        Booking.status.in_(["completed", "cancelled", "no_show"]),
    )


async def _history_page(db: AsyncSession, client_id: int, page: int):
    safe_page = max(1, page)
    rows = (await db.execute(
        select(Booking)
        .options(selectinload(Booking.instructor))
        .where(
            Booking.client_id == client_id,
            _history_filter(datetime.now(TIMEZONE).replace(tzinfo=None)),
        )
        .order_by(Booking.booking_date.desc(), Booking.start_time.desc(), Booking.id.desc())
        .offset((safe_page - 1) * HISTORY_PAGE_SIZE)
        .limit(HISTORY_PAGE_SIZE + 1)
    )).scalars().all()
    has_more = len(rows) > HISTORY_PAGE_SIZE
    items = []
    for booking in rows[:HISTORY_PAGE_SIZE]:
        rating = (await db.execute(
            select(RatingRecord).where(RatingRecord.booking_id == booking.id)
        )).scalar_one_or_none()
        items.append(await _serialize_booking(booking, db, booking.instructor, rating.vote if rating else None))
    return {
        "items": items,
        "page": safe_page,
        "page_size": HISTORY_PAGE_SIZE,
        "has_more": has_more,
    }


@router.get("/bookings")
async def get_bookings(
    filter: Optional[str] = None,
    user: Client = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(Booking).where(Booking.client_id == user.id)

    now = datetime.now(TIMEZONE).replace(tzinfo=None)
    today = now.date()
    now_time = now.time()

    if filter == "upcoming":
        query = query.where(
            (Booking.booking_date > today) |
            ((Booking.booking_date == today) & (Booking.start_time >= now_time))
        )
        query = query.where(
            Booking.status.in_(["pending", "cancellation_pending", "reschedule_pending", "planned", "confirmed", "in_progress"])
        )
    elif filter == "history":
        query = query.where(_history_filter(now))

    query = query.order_by(Booking.booking_date.desc(), Booking.start_time.desc())
    result = await db.execute(query)
    bookings = result.scalars().all()

    items = []
    for b in bookings:
        instructor = await db.get(Instructor, b.instructor_id)
        rating_result = await db.execute(select(RatingRecord).where(RatingRecord.booking_id == b.id))
        rating = rating_result.scalar_one_or_none()
        items.append(await _serialize_booking(b, db, instructor, rating.vote if rating else None))
    return items


@router.get("/bookings/my")
async def get_my_bookings(
    user: Client = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await get_bookings(filter=None, user=user, db=db)


@router.get("/bookings/history")
async def get_booking_history_page(
    page: int = 1,
    user: Client = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return one seven-item page of the authenticated client's history."""
    return await _history_page(db, user.id, page)


@router.get("/bookings/{booking_id:int}")
async def get_booking_detail(
    booking_id: int,
    user: Client = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    booking = await db.get(Booking, booking_id)
    if not booking or booking.client_id != user.id:
        raise HTTPException(status_code=404, detail="Booking not found")
    instructor = await db.get(Instructor, booking.instructor_id)
    rating_result = await db.execute(select(RatingRecord).where(RatingRecord.booking_id == booking.id))
    rating = rating_result.scalar_one_or_none()
    return await _serialize_booking(booking, db, instructor, rating.vote if rating else None)


@router.post("/bookings", status_code=201)
async def create_booking(
    body: CreateBookingRequest,
    user: Client = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user = (await db.execute(
        select(Client).where(Client.id == user.id).with_for_update()
    )).scalar_one()
    instructor_id = body.instructor_id
    location = settings.LOCATION_EXAM

    if not _is_date_in_booking_window(body.booking_date):
        raise HTTPException(status_code=400, detail="Запись доступна только на ближайшие 7 дней")
    
    start_time_obj = datetime.strptime(body.start_time, "%H:%M").time()
    try:
        service_type_enum = ServiceType(body.service_type)
    except ValueError:
        raise HTTPException(status_code=400, detail="Неизвестный тип урока")
    duration_minutes = settings.TRAINING_DURATION_MINUTES if service_type_enum == ServiceType.TRAINING else settings.EXAM_DURATION_MINUTES
    duration = timedelta(minutes=duration_minutes)
    end_dt = datetime.combine(body.booking_date, start_time_obj) + duration
    end_time_obj = end_dt.time()
    
    transmission_enum = "both"
    try:
        transmission_enum = TransmissionType(body.transmission)
    except ValueError:
        pass

    gender_enum = "any"
    try:
        gender_enum = InstructorGender(body.instructor_gender)
    except ValueError:
        pass

    if instructor_id:
        instructor = await db.get(Instructor, instructor_id)
        if not instructor or not instructor.is_active:
            raise HTTPException(status_code=404, detail="Instructor not found or inactive")
        if not await _is_instructor_available(
            db, instructor, body.booking_date, start_time_obj, end_time_obj,
            transmission_enum, service_type=service_type_enum,
        ):
            raise HTTPException(status_code=400, detail="Инструктор не ведёт этот тип урока, занят или не работает в выбранное время")
    else:
        instructor, location = await find_best_instructor_with_location(
            db, body.booking_date, start_time_obj, end_time_obj, transmission_enum,
            service_type_enum, gender_enum
        )
        if not instructor:
            raise HTTPException(status_code=400, detail="No available instructors for this time and criteria")
        instructor_id = instructor.id

    start_time_obj = datetime.strptime(body.start_time, "%H:%M").time()

    duration = timedelta(minutes=duration_minutes)
    end_dt = datetime.combine(body.booking_date, start_time_obj) + duration
    end_time_obj = end_dt.time()

    # Check if slot is already booked (user duplicate check)
    result = await db.execute(
        select(Booking).where(
            and_(
                Booking.client_id == user.id,
                Booking.booking_date == body.booking_date,
                Booking.start_time == start_time_obj,
                Booking.status.in_(["pending", "cancellation_pending", "reschedule_pending", "planned", "confirmed"]),
            )
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="You already have a booking at this time")

    daily_count_result = await db.execute(
        select(func.count()).select_from(Booking).where(
            and_(
                Booking.client_id == user.id,
                Booking.booking_date == body.booking_date,
                Booking.status.in_(["pending", "cancellation_pending", "reschedule_pending", "planned", "confirmed"]),
            )
        )
    )
    daily_count = daily_count_result.scalar() or 0
    if daily_count >= 2:
        raise HTTPException(status_code=400, detail="Максимум 2 записи на один день")

    from app.models.models import ClientBlock
    now_check = datetime.now(KZ_TZ).replace(tzinfo=None)
    block_result = await db.execute(
        select(ClientBlock).where(
            and_(ClientBlock.client_id == user.id, ClientBlock.blocked_until > now_check)
        )
    )
    if block_result.scalars().first():
        raise HTTPException(status_code=403, detail="Вы слишком часто создавали и отменяли записи. Дождитесь окончания блокировки и выберите подходящее время обдуманно.")

    instructor_conflict_result = await db.execute(
        select(Booking).where(
            and_(
                Booking.instructor_id == instructor_id,
                Booking.booking_date == body.booking_date,
                Booking.start_time < end_time_obj,
                Booking.end_time > start_time_obj,
                Booking.status.in_(["pending", "cancellation_pending", "reschedule_pending", "planned", "confirmed", "in_progress"]),
            )
        )
    )
    instructor_conflict = instructor_conflict_result.scalar_one_or_none()
    if not instructor_conflict:
        mobile_conflict_result = await db.execute(
            select(MobileBooking).where(
                and_(
                    MobileBooking.instructor_id == instructor_id,
                    MobileBooking.booking_date == body.booking_date,
                    MobileBooking.start_time < end_time_obj,
                    MobileBooking.end_time > start_time_obj,
                    MobileBooking.status.in_(["pending", "planned", "confirmed"]),
                )
            )
        )
        instructor_conflict = mobile_conflict_result.scalar_one_or_none()
    if instructor_conflict:
        raise HTTPException(status_code=400, detail="Инструктор уже занят в выбранное время")

    # Если инструктор выбран вручную — определяем площадку сейчас
    if body.instructor_id:
        from app.services.booking_service import get_training_location
        if service_type_enum == ServiceType.TRAINING:
            location = await get_training_location(db, body.booking_date, start_time_obj, end_time_obj)
            if not location:
                raise HTTPException(status_code=400, detail="Нет свободных площадок на это время")
        else:
            location = settings.LOCATION_EXAM

    vehicle = await reserve_available_vehicle(
        db, body.booking_date, start_time_obj, end_time_obj, transmission_enum
    )
    if not vehicle:
        raise HTTPException(status_code=409, detail="Подходящая машина уже занята. Обновите список и выберите другое время")

    base_price = settings.PRICE_EXAM if service_type_enum == ServiceType.EXAM else settings.PRICE_TRAINING_NEW
    price = base_price
    # A code entered with an APK booking is a request, never an immediate
    # payment.  The administrator decides it in the same queue as Telegram.
    certificate_code = body.certificate_code.strip().upper() if body.certificate_code else None
    certificate_id = None
    certificate_amount = 0

    referral_discount_amount = 0
    if price > 0 and user.referral_discount_available:
        existing_referral_booking = (await db.execute(
            select(func.count()).select_from(Booking).where(
                Booking.client_id == user.id,
                Booking.referral_discount_amount > 0,
                Booking.status.in_(["pending", "reschedule_pending", "planned", "confirmed", "in_progress", "completed"]),
            )
        )).scalar() or 0
        referral_eligible = False
        if user.referred_by_client_id:
            referral_eligible = bool((await db.execute(
                select(func.count()).select_from(Booking).where(
                    Booking.client_id == user.referred_by_client_id,
                    Booking.status == "completed",
                )
            )).scalar() or 0)
        else:
            referral_eligible = True
        if referral_eligible and not existing_referral_booking:
            referral_discount_amount = min(1000, price)
            price -= referral_discount_amount

    package_purchase = None
    package_bonus_exam_used = False
    if service_type_enum in (ServiceType.TRAINING, ServiceType.EXAM):
        now_naive = datetime.now(KZ_TZ).replace(tzinfo=None)
        package_purchase = (await db.execute(select(ClientPackage).where(
            ClientPackage.client_id == user.id, ClientPackage.is_active == True,
            ((ClientPackage.remaining_sessions > 0) if service_type_enum == ServiceType.TRAINING else
             (ClientPackage.remaining_sessions <= 0) & (ClientPackage.remaining_bonus_exams > 0)),
            (ClientPackage.expires_at.is_(None)) | (ClientPackage.expires_at >= now_naive),
        ).order_by(ClientPackage.expires_at).with_for_update())).scalars().first()
        if package_purchase:
            if service_type_enum == ServiceType.TRAINING:
                package_purchase.remaining_sessions -= 1
            else:
                package_purchase.remaining_bonus_exams -= 1
                package_bonus_exam_used = True
            if package_purchase.remaining_sessions == 0 and package_purchase.remaining_bonus_exams == 0:
                package_purchase.is_active = False
            # Do not consume the one-time referral reward when this lesson is
            # already fully paid by a package.
            referral_discount_amount = 0
            price = 0

    booking = Booking(
        client_id=user.id,
        instructor_id=instructor_id,
        vehicle_id=vehicle.id,
        booking_date=body.booking_date,
        start_time=start_time_obj,
        end_time=end_time_obj,
        service_type=service_type_enum,
        transmission=transmission_enum,
        location=location,
        status="pending",
        price=price,
        base_price=base_price,
        certificate_amount=certificate_amount,
        referral_discount_amount=referral_discount_amount,
        payment_status="paid" if price == 0 else "unpaid",
        paid_amount=base_price if package_purchase else 0,
        source="mobile",
        package_id=package_purchase.package_id if package_purchase else None,
        package_bonus_exam_used=package_bonus_exam_used,
        certificate_id=certificate_id,
        admin_viewed=False,
        admin_confirmed=False,
    )
    db.add(booking)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Это время уже занято. Обновите список и выберите другое.")
    await db.refresh(booking)

    if certificate_code:
        from app.models.models import CertificateRequest
        cert = (await db.execute(select(Certificate).where(Certificate.code == certificate_code))).scalar_one_or_none()
        db.add(CertificateRequest(
            client_id=user.id,
            booking_id=booking.id,
            code_entered=certificate_code,
            matched_certificate_id=(cert.id if cert and not cert.is_used and cert.remaining > 0 and cert.nominal == base_price else None),
            status="pending",
        ))
        db.add(Event(
            event_type="certificate_activation_requested",
            source="mobile",
            client_id=user.id,
            booking_id=booking.id,
            message=f"Клиент {user.name} подал заявку на подтверждение сертификата. Код: {certificate_code}",
        ))

    service_label = "Пробный экзамен" if service_type_enum == ServiceType.EXAM else "Урок вождения"
    db.add(Event(
        event_type="new_booking",
        source="mobile",
        client_id=user.id,
        instructor_id=instructor_id,
        booking_id=booking.id,
        message=(f"Клиент «{user.name}» записался на {service_label.lower()}: "
                 f"{body.booking_date.strftime('%d.%m.%Y')} в {body.start_time}."),
    ))
    await _block_after_repeated_cancellations(db, user.id)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Это время уже занято. Обновите список и выберите другое.")

    # Уведомление инструктору в Telegram
    instr_obj = await db.get(Instructor, instructor_id)
    if instr_obj and instr_obj.telegram_id:
        trans_label = "Механика" if str(transmission_enum).endswith("manual") else ("Оба" if str(transmission_enum).endswith("both") else "Автомат")
        # Определяем формат даты в зависимости от удалённости
        _RU_DAYS_SHORT = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
        from datetime import date as _date
        _booking_date = _date.fromisoformat(str(body.booking_date))
        _delta = (_booking_date - _date.today()).days
        if _delta > 7:
            _date_str = f"{body.booking_date} ({_RU_DAYS_SHORT[_booking_date.weekday()]})"
        else:
            _date_str = str(body.booking_date)
        if package_purchase:
            payment_line = "📦 ОПЛАЧЕНО ПАКЕТОМ — деньги НЕ брать!"
        elif certificate_id:
            payment_line = "🎟️ ОПЛАЧЕНО СЕРТИФИКАТОМ — деньги НЕ брать!"
        elif referral_discount_amount:
            payment_line = f"🎁 Скидка по реферальному коду: {int(referral_discount_amount)} ₸\n💰 К оплате: {int(price)} ₸"
        else:
            payment_line = f"💰 К оплате: {int(price)} ₸"
        instr_text = (
            "📌 Новая запись (приложение)!\n"
            f"📅 {_date_str} в {body.start_time}\n"
            f"Клиент: {user.name}\n"
            f"Площадка: {location}\n"
            f"Услуга: {service_label} ({trans_label})\n"
            f"{payment_line}"
        )
        try:
            from app.bot.handlers import instructor_bot
            if instructor_bot:
                await instructor_bot.send_message(int(instr_obj.telegram_id), instr_text)
        except Exception as e:
            import logging as _logging
            _logging.getLogger(__name__).error(f"Failed to notify instructor {instructor_id} via bot: {e}")

    return await _serialize_booking(booking, db, instructor)


@router.delete("/bookings/{booking_id}")
async def cancel_booking(
    booking_id: int,
    user: Client = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    booking = await db.get(Booking, booking_id)
    if not booking or booking.client_id != user.id:
        raise HTTPException(status_code=404, detail="Booking not found")
    if booking.status not in ["planned", "confirmed"]:
        raise HTTPException(status_code=400, detail="Cannot cancel this booking")
    await _ensure_client_is_not_blocked(db, user.id)
    
    # Клиент не может отменить запись с сертификатом
    if booking.certificate_id or (booking.certificate_amount or 0) > 0:
        raise HTTPException(
            status_code=400, 
            detail="Запись с сертификатом нельзя отменить. Свяжитесь с поддержкой для переноса."
        )

    booking.cancellation_previous_status = booking.status
    booking.status = "cancellation_pending"
    await db.commit()

    db.add(Event(
        event_type="booking_cancellation_requested",
        source="mobile",
        client_id=user.id,
        instructor_id=booking.instructor_id,
        booking_id=booking.id,
        message=(f"Клиент «{user.name}» запросил отмену записи на "
                 f"{booking.booking_date.strftime('%d.%m.%Y')} в {booking.start_time.strftime('%H:%M')}."),
    ))
    await db.commit()

    # Уведомление инструктору в Telegram
    instructor = await db.get(Instructor, booking.instructor_id)
    if instructor and instructor.telegram_id and settings.INSTRUCTOR_BOT_TOKEN:
        try:
            trans_label = "Механика" if booking.transmission == "manual" else "Автомат"
            instr_text = (
                f"⏳ Клиент запросил отмену записи.\n\n"
                f"{instructor.name}, ожидается подтверждение администратора.\n\n"
                f"📅 {booking.booking_date.strftime('%d.%m.%Y')} в {booking.start_time.strftime('%H:%M')}\n"
                f"📍 {booking.location}\n"
                f"Коробка: {trans_label}"
            )
            import httpx
            async with httpx.AsyncClient(timeout=10) as http_client:
                await http_client.post(
                    f"https://api.telegram.org/bot{settings.INSTRUCTOR_BOT_TOKEN}/sendMessage",
                    json={"chat_id": instructor.telegram_id.strip(), "text": instr_text},
                )
        except Exception as e:
            import logging as _logging
            _logging.getLogger(__name__).error(f"Failed to notify instructor {booking.instructor_id} about cancellation: {e}")

    return {"message": "Ваша заявка на отмену находится в обработке.", "status": "cancellation_pending"}


@router.post("/bookings/{booking_id}/cancel-request/revoke")
async def revoke_cancellation_request(
    booking_id: int,
    user: Client = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    booking = await db.get(Booking, booking_id)
    if not booking or booking.client_id != user.id:
        raise HTTPException(status_code=404, detail="Booking not found")
    if booking.status != "cancellation_pending":
        raise HTTPException(status_code=400, detail="Нет заявки на отмену")
    booking.status = booking.cancellation_previous_status or "confirmed"
    booking.cancellation_previous_status = None
    db.add(Event(
        event_type="booking_cancellation_revoked",
        source="mobile",
        client_id=user.id,
        instructor_id=booking.instructor_id,
        booking_id=booking.id,
        message=f"Клиент «{user.name}» отозвал заявку на отмену записи.",
    ))
    await db.commit()
    return {"message": "Заявка на отмену отозвана", "status": booking.status}


@router.put("/bookings/{booking_id}/reschedule")
async def reschedule_booking(
    booking_id: int,
    body: RescheduleBookingRequest,
    user: Client = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Клиент переносит запись на новую дату/время из доступных слотов."""
    booking = await db.get(Booking, booking_id)
    if not booking or booking.client_id != user.id:
        raise HTTPException(status_code=404, detail="Booking not found")
    if booking.status not in ["planned", "confirmed"]:
        raise HTTPException(status_code=400, detail="Cannot reschedule this booking")

    new_date = date.fromisoformat(body.new_date)
    new_start = time.fromisoformat(body.new_start_time)

    if not _is_date_in_booking_window(new_date):
        raise HTTPException(status_code=400, detail="Перенос доступен только на ближайшие 7 дней")

    # Вычисляем новое end_time
    duration_minutes = settings.TRAINING_DURATION_MINUTES if booking.service_type == ServiceType.TRAINING else settings.EXAM_DURATION_MINUTES
    et = timedelta(hours=new_start.hour, minutes=new_start.minute) + timedelta(minutes=duration_minutes)
    new_end = time(int(et.total_seconds() // 3600), int((et.total_seconds() % 3600) // 60))

    instructor = await db.get(Instructor, booking.instructor_id)
    booking_service = booking.service_type if isinstance(booking.service_type, ServiceType) else ServiceType(booking.service_type)
    if not instructor or not await _is_instructor_available(
        db, instructor, new_date, new_start, new_end, booking.transmission,
        service_type=booking_service,
        preserve_existing_assignment=True,
    ):
        raise HTTPException(
            status_code=400,
            detail="Текущий инструктор недоступен в выбранное время. Выберите другой слот или обратитесь к администратору",
        )

    # Проверяем конфликты у текущего инструктора (Booking + MobileBooking)
    conflict_result = await db.execute(
        select(Booking).where(
            and_(
                Booking.instructor_id == booking.instructor_id,
                Booking.booking_date == new_date,
                Booking.id != booking.id,
                Booking.status.in_(["pending", "cancellation_pending", "reschedule_pending", "planned", "confirmed", "in_progress"]),
                Booking.start_time < new_end,
                Booking.end_time > new_start,
            )
        )
    )
    conflict = conflict_result.scalar_one_or_none()
    if not conflict:
        mobile_conflict_result = await db.execute(
            select(MobileBooking).where(
                and_(
                    MobileBooking.instructor_id == booking.instructor_id,
                    MobileBooking.booking_date == new_date,
                    MobileBooking.status.in_(["pending", "planned", "confirmed"]),
                    MobileBooking.start_time < new_end,
                    MobileBooking.end_time > new_start,
                )
            )
        )
        conflict = mobile_conflict_result.scalar_one_or_none()
    if conflict:
        raise HTTPException(status_code=409, detail="Инструктор занят в это время. Выберите другое время из доступных слотов")

    # Проверяем что слот реально есть в доступных (по рабочим часам конкретного инструктора)
    service_enum = booking.service_type if isinstance(booking.service_type, ServiceType) else ServiceType(booking.service_type)
    trans_enum = booking.transmission if isinstance(booking.transmission, TransmissionType) else TransmissionType(booking.transmission)
    available_slots = await get_available_slots_for_instructor(
        db, new_date, service_enum, trans_enum, booking.location, booking.instructor_id,
        preserve_existing_assignment=True,
    )
    if new_start not in available_slots:
        raise HTTPException(status_code=409, detail="Это время уже занято. Выберите другое время из доступных слотов")

    await _ensure_client_is_not_blocked(db, user.id)
    _, reschedule_count = await _consume_client_reschedule_slot(db, user.id)

    # Выбор слота создаёт заявку: текущая запись не меняется, пока её не
    # рассмотрит администратор.
    booking.reschedule_previous_status = booking.status
    booking.requested_reschedule_date = new_date
    booking.requested_reschedule_start_time = new_start
    booking.requested_reschedule_end_time = new_end
    booking.reschedule_requested_at = datetime.now(KZ_TZ).replace(tzinfo=None)
    booking.status = "reschedule_pending"

    db.add(Event(
        event_type="booking_reschedule_requested",
        source="mobile",
        client_id=user.id,
        instructor_id=booking.instructor_id,
        booking_id=booking.id,
        message=(f"Клиент «{user.name}» запросил перенос записи на "
                 f"{new_date.strftime('%d.%m.%Y')} в {body.new_start_time}."),
    ))
    await db.commit()
    if reschedule_count == 2:
        await _add_support_notice(
            db, user.id,
            "Внимание: это второй перенос за последние 24 часа. Вы можете отправить ещё одну заявку; "
            "после третьего переноса новые самостоятельные переносы будут недоступны до окончания 24-часового периода.",
        )
        await db.commit()

    return {
        "message": "Заявка на перенос отправлена администратору.",
        "status": "reschedule_pending",
    }


@router.post("/bookings/{booking_id}/confirm")
async def confirm_booking(
    booking_id: int,
    body: ConfirmBookingRequest,
    user: Client = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Клиент подтверждает посещение или запрашивает отмену.

    Запрос клиента никогда не отменяет занятие сам: окончательное решение
    принимает администратор в админке.  Это защищает запись от случайного
    нажатия «Не приду» в приложении.
    """
    booking = await db.get(Booking, booking_id)
    if not booking or booking.client_id != user.id:
        raise HTTPException(status_code=404, detail="Booking not found")
    if booking.status not in ["planned", "confirmed"]:
        raise HTTPException(status_code=400, detail="Нельзя изменить эту запись")
    if not body.coming:
        await _ensure_client_is_not_blocked(db, user.id)

    now = datetime.now(KZ_TZ).replace(tzinfo=None)
    starts_at = datetime.combine(booking.booking_date, booking.start_time)
    minutes_until_start = (starts_at - now).total_seconds() / 60
    if minutes_until_start < 0 or minutes_until_start > 60:
        raise HTTPException(
            status_code=400,
            detail="Подтвердить или отменить посещение можно только за час до занятия.",
        )

    booking.confirmed_by_client = body.coming
    if not body.coming:
        booking.cancellation_previous_status = booking.status
        booking.status = "cancellation_pending"

    db.add(Event(
        event_type="booking_attendance_confirmed" if body.coming else "booking_cancellation_requested",
        source="mobile",
        client_id=user.id,
        instructor_id=booking.instructor_id,
        booking_id=booking.id,
        message=(
            f"Клиент «{user.name}» подтвердил, что придёт на занятие."
            if body.coming else
            f"Клиент «{user.name}» сообщил, что не придёт, и запросил отмену записи."
        ),
    ))

    await db.commit()

    # Клиентская отмена — только заявка, не финальная отмена.
    if not body.coming:
        instructor = await db.get(Instructor, booking.instructor_id)
        if instructor and instructor.telegram_id and settings.INSTRUCTOR_BOT_TOKEN:
            try:
                trans_label = "Механика" if booking.transmission == "manual" else "Автомат"
                instr_text = (
                    f"⏳ Клиент запросил отмену записи.\n\n"
                    f"{instructor.name}, ожидается решение администратора.\n\n"
                    f"📅 {booking.booking_date.strftime('%d.%m.%Y')} в {booking.start_time.strftime('%H:%M')}\n"
                    f"📍 {booking.location}\n"
                    f"Коробка: {trans_label}"
                )
                import httpx
                async with httpx.AsyncClient(timeout=10) as http_client:
                    await http_client.post(
                        f"https://api.telegram.org/bot{settings.INSTRUCTOR_BOT_TOKEN}/sendMessage",
                        json={"chat_id": instructor.telegram_id.strip(), "text": instr_text},
                    )
            except Exception as e:
                import logging as _logging
                _logging.getLogger(__name__).error(f"Failed to notify instructor {booking.instructor_id} about cancellation: {e}")

    return {
        "message": "ok",
        "confirmed_by_client": booking.confirmed_by_client,
        "status": _value(booking.status),
    }


@router.post("/bookings/{booking_id}/rate")
async def rate_booking(
    booking_id: int,
    body: RateBookingRequest,
    user: Client = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    booking = await db.get(Booking, booking_id)
    if not booking or booking.client_id != user.id:
        raise HTTPException(status_code=404, detail="Booking not found")
    if booking.status != "completed":
        raise HTTPException(status_code=400, detail="Can only rate completed bookings")
    existing = await db.execute(select(RatingRecord).where(RatingRecord.booking_id == booking_id))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=400, detail="Already rated")

    try:
        vote_enum = RatingVote(body.vote)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid vote value")

    db.add(RatingRecord(booking_id=booking.id, instructor_id=booking.instructor_id, vote=vote_enum.value))
    vote_labels = {"good": "хорошо", "normal": "нормально", "bad": "плохо"}
    db.add(Event(
        event_type="rating_given",
        source="mobile",
        client_id=user.id,
        instructor_id=booking.instructor_id,
        booking_id=booking.id,
        message=f"Клиент «{user.name}» оценил занятие: {vote_labels[body.vote]}.",
    ))

    # Обновляем рейтинг инструктора (как в Telegram-боте)
    inst_result = await db.execute(select(Instructor).where(Instructor.id == booking.instructor_id))
    instructor = inst_result.scalar_one_or_none()
    if instructor:
        if vote_enum == RatingVote.GOOD:
            instructor.rating = round(instructor.rating + settings.RATING_STEP, 1)
        elif vote_enum == RatingVote.BAD:
            instructor.rating = round(max(settings.MIN_RATING, instructor.rating - settings.RATING_STEP), 1)

        # Критический минимум — красное уведомление админу
        if instructor.rating <= settings.MIN_RATING:
            already = await db.execute(
                select(NotificationSent).where(
                    and_(
                        NotificationSent.instructor_id == instructor.id,
                        NotificationSent.notification_type == "low_rating",
                    )
                )
            )
            if not already.scalar_one_or_none():
                db.add(NotificationSent(instructor_id=instructor.id, notification_type="low_rating"))

    await db.commit()
    return {"message": "Rating saved"}


@router.get("/slots")
async def get_slots(
    instructor_id: Optional[int] = None,
    booking_date: Optional[str] = None,
    service_type: str = "training",
    transmission: str = "both",
    instructor_gender: str = "any",
    location_preference: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    if not booking_date:
        return {"slots": []}

    target_date = datetime.strptime(booking_date, "%Y-%m-%d").date()
    now = datetime.now(TIMEZONE)
    today_kz = now.date()
    current_time_kz = now.time()

    if not _is_date_in_booking_window(target_date):
        return {"slots": []}

    try:
        service_enum = ServiceType(service_type)
    except ValueError:
        service_enum = ServiceType.TRAINING

    try:
        trans_enum = TransmissionType(transmission)
    except ValueError:
        trans_enum = "both"
        
    try:
        gender_enum = InstructorGender(instructor_gender)
    except ValueError:
        gender_enum = "any"

    location = settings.LOCATION_EXAM
    location_preference = settings.LOCATION_EXAM

    # Если передан instructor_id — показываем слоты только для этого инструктора
    if instructor_id:
        slots = await get_available_slots_for_instructor(
            db, target_date, service_enum, trans_enum, location, instructor_id,
            # This endpoint's explicit-instructor mode is used by installed app
            # versions for rescheduling an existing assignment. Creating a new
            # booking is still revalidated against the current instructor card.
            preserve_existing_assignment=True,
        )
    else:
        slots = await get_available_slots(db, target_date, service_enum, trans_enum, location, gender_enum, location_preference=location_preference)
    
    # Если это сегодняшний день, фильтруем слоты которые уже прошли
    if target_date == today_kz:
        slots = [s for s in slots if s > current_time_kz]
    
    available_slots = [s.strftime("%H:%M") for s in slots]
    return {"slots": available_slots}


@router.get("/bookings/available-slots")
async def get_available_slots_endpoint(
    instructor_id: Optional[int] = None,
    date_str: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    return await get_slots(
        instructor_id=instructor_id,
        booking_date=date_str,
        db=db,
    )


@router.get("/instructors")
async def get_instructors(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Instructor).where(Instructor.is_active == True).order_by(Instructor.name)
    )
    instructors = result.scalars().all()

    return [
        {
            "id": i.id,
            "name": i.name,
            "transmission": i.transmission if hasattr(i.transmission, 'value') else str(i.transmission),
            "lesson_type": i.lesson_type or "both",
            "experience_years": i.experience_years,
            "rating": i.rating,
            "description": i.description or "",
            "avatar_url": i.avatar_url,
        }
        for i in instructors
    ]
