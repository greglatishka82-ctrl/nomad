"""
Записи мобильного приложения.
GET    /api/mobile/slots
GET    /api/mobile/bookings
POST   /api/mobile/bookings
GET    /api/mobile/bookings/{id}
DELETE /api/mobile/bookings/{id}
POST   /api/mobile/bookings/{id}/confirm
POST   /api/mobile/bookings/{id}/rate
"""
from datetime import date, time, timedelta, datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.models import (
    MobileBooking, MobileUser, MobileUserPackage, MobileUserCertificate,
    Instructor, Certificate, BookingStatus, ServiceType, TransmissionType, RatingVote
)
from app.services.booking_service import get_available_slots, find_best_instructor
from app.services.mobile_auth import get_current_user_id

router = APIRouter(prefix="/api/mobile", tags=["mobile-bookings"])


# ── Схемы ──────────────────────────────────────────────────────────────────────

class SlotItem(BaseModel):
    time: str  # "09:00"


class CreateBookingRequest(BaseModel):
    service_type: str     # "training" | "exam"
    transmission: str     # "manual" | "automatic"
    booking_date: str     # "2026-08-01"
    start_time: str       # "10:00"
    certificate_code: Optional[str] = None
    use_package_id: Optional[int] = None   # ID MobileUserPackage


class RateRequest(BaseModel):
    vote: str  # "good" | "normal" | "bad"


def _booking_to_dict(b: MobileBooking) -> dict:
    return {
        "id": b.id,
        "service_type": b.service_type.value,
        "transmission": b.transmission.value,
        "location": b.location,
        "booking_date": b.booking_date.isoformat(),
        "start_time": b.start_time.strftime("%H:%M"),
        "end_time": b.end_time.strftime("%H:%M"),
        "status": b.status.value,
        "price": b.price,
        "instructor": {
            "id": b.instructor.id,
            "name": b.instructor.name,
            "avatar_url": b.instructor.avatar_url,
            "rating": b.instructor.rating,
        } if b.instructor else None,
        "rating_vote": b.rating_vote.value if b.rating_vote else None,
        "created_at": b.created_at.isoformat(),
    }


# ── Слоты ─────────────────────────────────────────────────────────────────────

@router.get("/slots")
async def available_slots(
    booking_date: str = Query(..., description="YYYY-MM-DD"),
    service_type: str = Query(..., description="training | exam"),
    transmission: str = Query(..., description="manual | automatic"),
    db: AsyncSession = Depends(get_db),
):
    try:
        d = date.fromisoformat(booking_date)
        stype = ServiceType(service_type)
        trans = TransmissionType(transmission)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Неверные параметры: {e}")

    location = (
        settings.LOCATION_MAIN if stype == ServiceType.TRAINING else settings.LOCATION_EXAM
    )
    slots = await get_available_slots(db, d, stype, trans, location)
    return {"slots": [s.strftime("%H:%M") for s in slots]}


# ── Записи ────────────────────────────────────────────────────────────────────

@router.get("/bookings")
async def list_bookings(
    filter: str = Query("upcoming", description="upcoming | history | all"),
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(MobileBooking)
        .where(MobileBooking.user_id == user_id)
        .order_by(MobileBooking.booking_date.desc(), MobileBooking.start_time.desc())
    )

    if filter == "upcoming":
        query = query.where(
            MobileBooking.status.in_([BookingStatus.PLANNED, BookingStatus.CONFIRMED])
        )
    elif filter == "history":
        query = query.where(
            MobileBooking.status.in_([
                BookingStatus.COMPLETED, BookingStatus.CANCELLED, BookingStatus.NO_SHOW
            ])
        )

    result = await db.execute(query)
    bookings = result.scalars().all()

    # Подгружаем инструкторов
    for b in bookings:
        if b.instructor_id:
            inst = await db.get(Instructor, b.instructor_id)
            b.instructor = inst

    return [_booking_to_dict(b) for b in bookings]


@router.post("/bookings", status_code=status.HTTP_201_CREATED)
async def create_booking(
    body: CreateBookingRequest,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    # Проверяем лимит активных записей
    active_count_result = await db.execute(
        select(func.count()).select_from(MobileBooking).where(
            and_(
                MobileBooking.user_id == user_id,
                MobileBooking.status.in_([BookingStatus.PLANNED, BookingStatus.CONFIRMED]),
            )
        )
    )
    if (active_count_result.scalar() or 0) >= settings.MAX_ACTIVE_BOOKINGS:
        raise HTTPException(
            status_code=400,
            detail=f"Нельзя иметь более {settings.MAX_ACTIVE_BOOKINGS} активных записей одновременно",
        )

    try:
        booking_date = date.fromisoformat(body.booking_date)
        stype = ServiceType(body.service_type)
        trans = TransmissionType(body.transmission)
        start_t = time.fromisoformat(body.start_time)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Неверные параметры: {e}")

    # Нельзя записаться менее чем за 30 минут
    now = datetime.now()
    slot_dt = datetime.combine(booking_date, start_t)
    if slot_dt - now < timedelta(minutes=30):
        raise HTTPException(status_code=400, detail="Нельзя записаться менее чем за 30 минут до занятия")

    location = (
        settings.LOCATION_MAIN if stype == ServiceType.TRAINING else settings.LOCATION_EXAM
    )
    duration_min = (
        settings.TRAINING_DURATION_MINUTES if stype == ServiceType.TRAINING
        else settings.EXAM_DURATION_MINUTES
    )
    end_t = (datetime.combine(booking_date, start_t) + timedelta(minutes=duration_min)).time()

    # Ищем лучшего свободного инструктора
    instructor = await find_best_instructor(db, booking_date, start_t, end_t, trans, location)
    if not instructor:
        raise HTTPException(status_code=409, detail="На выбранное время нет свободных инструкторов")

    price = settings.PRICE_TRAINING if stype == ServiceType.TRAINING else settings.PRICE_EXAM

    # Проверяем сертификат
    cert_usage: Optional[MobileUserCertificate] = None
    if body.certificate_code:
        cert_result = await db.execute(
            select(Certificate).where(
                Certificate.code == body.certificate_code.upper(),
                Certificate.remaining > 0,
            )
        )
        cert = cert_result.scalar_one_or_none()
        if not cert:
            raise HTTPException(status_code=400, detail="Сертификат не найден или исчерпан")
        # Проверяем что именно этот пользователь активировал его
        uc_result = await db.execute(
            select(MobileUserCertificate).where(
                MobileUserCertificate.user_id == user_id,
                MobileUserCertificate.certificate_id == cert.id,
            )
        )
        cert_usage = uc_result.scalar_one_or_none()
        if not cert_usage:
            raise HTTPException(status_code=400, detail="Этот сертификат не привязан к вашему аккаунту")
        discount = min(cert.remaining, price)
        price = max(0, price - discount)
        cert.remaining -= discount
        await db.flush()

    # Проверяем пакет
    pkg_usage: Optional[MobileUserPackage] = None
    if body.use_package_id:
        pkg_result = await db.execute(
            select(MobileUserPackage).where(
                MobileUserPackage.id == body.use_package_id,
                MobileUserPackage.user_id == user_id,
                MobileUserPackage.is_active == True,
                MobileUserPackage.remaining_sessions > 0,
            )
        )
        pkg_usage = pkg_result.scalar_one_or_none()
        if not pkg_usage:
            raise HTTPException(status_code=400, detail="Пакет не найден, неактивен или исчерпан")
        pkg_usage.remaining_sessions -= 1
        price = 0  # пакет покрывает занятие полностью
        await db.flush()

    booking = MobileBooking(
        user_id=user_id,
        instructor_id=instructor.id,
        service_type=stype,
        transmission=trans,
        location=location,
        booking_date=booking_date,
        start_time=start_t,
        end_time=end_t,
        price=price,
        package_usage_id=pkg_usage.id if pkg_usage else None,
        certificate_id=cert_usage.id if cert_usage else None,
    )
    db.add(booking)
    await db.commit()
    await db.refresh(booking)
    booking.instructor = instructor
    return _booking_to_dict(booking)


@router.get("/bookings/{booking_id}")
async def get_booking(
    booking_id: int,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(MobileBooking).where(
            MobileBooking.id == booking_id,
            MobileBooking.user_id == user_id,
        )
    )
    b = result.scalar_one_or_none()
    if not b:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    b.instructor = await db.get(Instructor, b.instructor_id)
    return _booking_to_dict(b)


@router.delete("/bookings/{booking_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_booking(
    booking_id: int,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(MobileBooking).where(
            MobileBooking.id == booking_id,
            MobileBooking.user_id == user_id,
        )
    )
    b = result.scalar_one_or_none()
    if not b:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    if b.status not in (BookingStatus.PLANNED, BookingStatus.CONFIRMED):
        raise HTTPException(status_code=400, detail="Нельзя отменить завершённую или уже отменённую запись")

    slot_dt = datetime.combine(b.booking_date, b.start_time)
    if datetime.now() > slot_dt - timedelta(hours=2):
        raise HTTPException(
            status_code=400,
            detail="Отмена возможна не позже чем за 2 часа до занятия",
        )

    # Возвращаем занятие в пакет если использовался
    if b.package_usage_id:
        pkg = await db.get(MobileUserPackage, b.package_usage_id)
        if pkg:
            pkg.remaining_sessions += 1

    b.status = BookingStatus.CANCELLED
    await db.commit()


@router.post("/bookings/{booking_id}/confirm")
async def confirm_booking(
    booking_id: int,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(MobileBooking).where(
            MobileBooking.id == booking_id,
            MobileBooking.user_id == user_id,
        )
    )
    b = result.scalar_one_or_none()
    if not b:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    if b.status != BookingStatus.PLANNED:
        raise HTTPException(status_code=400, detail="Запись уже подтверждена или завершена")
    b.status = BookingStatus.CONFIRMED
    await db.commit()
    return {"message": "Запись подтверждена"}


@router.post("/bookings/{booking_id}/rate")
async def rate_booking(
    booking_id: int,
    body: RateRequest,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        vote = RatingVote(body.vote)
    except ValueError:
        raise HTTPException(status_code=400, detail="Допустимые значения: good, normal, bad")

    result = await db.execute(
        select(MobileBooking).where(
            MobileBooking.id == booking_id,
            MobileBooking.user_id == user_id,
        )
    )
    b = result.scalar_one_or_none()
    if not b:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    if b.status != BookingStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Оценить можно только завершённое занятие")
    if b.rating_vote is not None:
        raise HTTPException(status_code=400, detail="Вы уже оставили оценку")

    b.rating_vote = vote
    b.rating_sent = True

    # Обновляем рейтинг инструктора
    instructor = await db.get(Instructor, b.instructor_id)
    if instructor:
        delta = {RatingVote.GOOD: 0.1, RatingVote.NORMAL: 0.0, RatingVote.BAD: -0.1}[vote]
        instructor.rating = max(1.0, min(5.0, round(instructor.rating + delta, 1)))

    await db.commit()
    return {"message": "Спасибо за оценку!"}
