from datetime import date, time, timedelta, datetime
from typing import List, Optional

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.models import (
    Booking, MobileBooking, Instructor, BookingStatus, ServiceType, TransmissionType
)

RUSSIAN_DAY_NAMES = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]

# Обед: 13:00 – 14:00 (жёсткое глобальное правило)
LUNCH_START = time(13, 0)
LUNCH_END = time(14, 0)


def _get_day_name(d: date) -> str:
    return RUSSIAN_DAY_NAMES[d.weekday()]


def _slot_overlaps_lunch(start_t: time, end_t: time) -> bool:
    """Слот пересекается с обедом 13:00–14:00."""
    return start_t < LUNCH_END and end_t > LUNCH_START


def _instructor_matches_transmission(inst: Instructor, transmission: TransmissionType) -> bool:
    if transmission == TransmissionType.MANUAL:
        return inst.transmission in (TransmissionType.MANUAL, TransmissionType.BOTH)
    if transmission == TransmissionType.AUTOMATIC:
        return inst.transmission in (TransmissionType.AUTOMATIC, TransmissionType.BOTH)
    return True


def _instructor_works_on(inst: Instructor, d: date) -> bool:
    day_name = _get_day_name(d)
    days_off_list = [x.strip() for x in (inst.days_off or "").split(",") if x.strip()]
    return day_name not in days_off_list


def _instructor_available_at(inst: Instructor, start_t: time, end_t: time) -> bool:
    """Инструктор работает в это время (рабочие часы + персональный обед)."""
    if inst.working_hours_start > start_t or inst.working_hours_end < end_t:
        return False
    # Персональный обед инструктора (если задан отдельно от глобального)
    if inst.lunch_start and inst.lunch_end:
        if start_t < inst.lunch_end and end_t > inst.lunch_start:
            return False
    return True


async def _get_busy_instructor_ids(
    db: AsyncSession,
    booking_date: date,
    start_t: time,
    end_t: time,
) -> set:
    """Возвращает множество ID инструкторов, уже занятых в данный временной слот."""
    tg_result = await db.execute(
        select(Booking.instructor_id).where(
            and_(
                Booking.booking_date == booking_date,
                Booking.start_time < end_t,
                Booking.end_time > start_t,
                Booking.status.in_([BookingStatus.PLANNED, BookingStatus.CONFIRMED]),
            )
        )
    )
    mob_result = await db.execute(
        select(MobileBooking.instructor_id).where(
            and_(
                MobileBooking.booking_date == booking_date,
                MobileBooking.start_time < end_t,
                MobileBooking.end_time > start_t,
                MobileBooking.status.in_([BookingStatus.PLANNED, BookingStatus.CONFIRMED]),
            )
        )
    )
    return (
        {row[0] for row in tg_result.all()} |
        {row[0] for row in mob_result.all()}
    )


async def _get_free_instructors(
    db: AsyncSession,
    booking_date: date,
    start_t: time,
    end_t: time,
    transmission: TransmissionType,
) -> List[Instructor]:
    """
    Возвращает список свободных инструкторов для данного слота с учётом:
    - типа коробки передач
    - рабочего дня / выходных
    - рабочих часов
    - занятости (уже назначены на другую запись)
    - глобального обеда 13:00–14:00
    """
    # Глобальный обед — слот недоступен в принципе
    if _slot_overlaps_lunch(start_t, end_t):
        return []

    result = await db.execute(
        select(Instructor).where(Instructor.is_active == True)
    )
    all_instructors = result.scalars().all()

    busy_ids = await _get_busy_instructor_ids(db, booking_date, start_t, end_t)

    free = []
    for inst in all_instructors:
        if inst.id in busy_ids:
            continue
        if not _instructor_matches_transmission(inst, transmission):
            continue
        if not _instructor_works_on(inst, booking_date):
            continue
        if not _instructor_available_at(inst, start_t, end_t):
            continue
        free.append(inst)

    return free


async def get_available_slots(
    db: AsyncSession,
    booking_date: date,
    service_type: ServiceType,
    transmission: TransmissionType,
    location: str,
) -> List[time]:
    duration_min = (
        settings.TRAINING_DURATION_MINUTES if service_type == ServiceType.TRAINING
        else settings.EXAM_DURATION_MINUTES
    )
    duration = timedelta(minutes=duration_min)
    start_hour = settings.WORKING_HOURS_START
    end_hour = settings.WORKING_HOURS_END

    now = datetime.now()
    is_today = booking_date == now.date()
    # Минимальное время: текущее + 30 минут (округлено вверх до часа)
    if is_today:
        min_minutes = now.hour * 60 + now.minute + 30
    else:
        min_minutes = 0

    slots = []
    current_minutes = max(start_hour * 60, min_minutes)
    end_minutes = end_hour * 60

    while current_minutes <= end_minutes:
        slot_end_minutes = current_minutes + duration_min
        start_t = time(current_minutes // 60, current_minutes % 60)
        end_t = time(slot_end_minutes // 60, slot_end_minutes % 60)

        # Пропускаем обед жёстко
        if not _slot_overlaps_lunch(start_t, end_t):
            free = await _get_free_instructors(db, booking_date, start_t, end_t, transmission)
            if free:
                slots.append(start_t)

        current_minutes += duration_min

    return slots


async def find_best_instructor(
    db: AsyncSession,
    booking_date: date,
    start_time: time,
    end_time: time,
    transmission: TransmissionType,
    location: str,
) -> Optional[Instructor]:
    """
    Находит лучшего (наивысший рейтинг) свободного инструктора для слота.
    Учитывает тип коробки, день недели, рабочие часы и занятость.
    """
    free = await _get_free_instructors(db, booking_date, start_time, end_time, transmission)
    if not free:
        return None
    # Сортируем по рейтингу убывая
    free.sort(key=lambda i: i.rating, reverse=True)
    return free[0]


async def has_available_instructors(
    db: AsyncSession,
    booking_date: date,
    service_type: ServiceType,
    transmission: TransmissionType,
) -> bool:
    """Есть ли хоть один инструктор нужного типа в этот день (без учёта занятости)."""
    day_name = _get_day_name(booking_date)
    result = await db.execute(
        select(Instructor).where(Instructor.is_active == True)
    )
    for inst in result.scalars().all():
        if not _instructor_matches_transmission(inst, transmission):
            continue
        days_off_list = [x.strip() for x in (inst.days_off or "").split(",") if x.strip()]
        if day_name in days_off_list:
            continue
        return True
    return False
