from datetime import date, time, timedelta, datetime
from typing import List, Optional

from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings, TIMEZONE
from app.models.models import (
    Booking, MobileBooking, Vehicle, Instructor, InstructorDayOff, InstructorDailySchedule, InstructorRotation, BookingStatus, ServiceType, TransmissionType, InstructorGender
)

RUSSIAN_DAY_NAMES = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
ACTIVE_BOOKING_STATUSES = ("pending", "cancellation_pending", "reschedule_pending", "planned", "confirmed", "in_progress")
ACTIVE_MOBILE_BOOKING_STATUSES = ("pending", "planned", "confirmed", "in_progress")


def _value(value):
    return value.value if hasattr(value, "value") else str(value)


def _get_day_name(d: date) -> str:
    return RUSSIAN_DAY_NAMES[d.weekday()]


def _is_in_lunch(start_time: time, end_time: time, lunch_start: time, lunch_end: time) -> bool:
    return start_time < lunch_end and end_time > lunch_start


def _overlaps_global_lunch(start_time: time, end_time: time) -> bool:
    return False


def _is_empty_lunch(lunch_start: Optional[time], lunch_end: Optional[time]) -> bool:
    return not lunch_start or not lunch_end or lunch_start == lunch_end or (
        lunch_start.hour == 0 and lunch_start.minute == 0 and lunch_end.hour == 0 and lunch_end.minute == 0
    )


def _teaches_service(instructor: Instructor, service_type: Optional[ServiceType]) -> bool:
    """Whether an instructor's card allows the requested lesson type."""
    if service_type is None:
        return True
    lesson_type = str(getattr(instructor, "lesson_type", "both") or "both").lower()
    requested = service_type.value if hasattr(service_type, "value") else str(service_type).lower()
    return lesson_type in ("both", requested)


async def _get_daily_schedule(db: AsyncSession, instructor_id: int, schedule_date: date) -> Optional[InstructorDailySchedule]:
    result = await db.execute(
        select(InstructorDailySchedule).where(
            and_(
                InstructorDailySchedule.instructor_id == instructor_id,
                InstructorDailySchedule.schedule_date == schedule_date,
            )
        )
    )
    return result.scalar_one_or_none()


async def _has_date_day_off(db: AsyncSession, instructor_id: int, day_off_date: date) -> bool:
    result = await db.execute(
        select(InstructorDayOff.id).where(
            and_(
                InstructorDayOff.instructor_id == instructor_id,
                InstructorDayOff.day_off_date == day_off_date,
            )
        )
    )
    return result.scalar_one_or_none() is not None


async def _get_effective_schedule(db: AsyncSession, instructor: Instructor, booking_date: date):
    daily = await _get_daily_schedule(db, instructor.id, booking_date)
    if daily and daily.is_day_off:
        return None
    if not daily and await _has_date_day_off(db, instructor.id, booking_date):
        return None
    if daily:
        start = daily.working_hours_start or instructor.working_hours_start
        end = daily.working_hours_end or instructor.working_hours_end
        lunch_start = daily.lunch_start
        lunch_end = daily.lunch_end
    else:
        start = instructor.working_hours_start
        end = instructor.working_hours_end
        lunch_start = instructor.lunch_start
        lunch_end = instructor.lunch_end
    if not start or not end:
        return None
    return start, end, lunch_start, lunch_end


async def _is_instructor_available(
    db: AsyncSession,
    instructor: Instructor,
    booking_date: date,
    start_time: time,
    end_time: time,
    transmission: TransmissionType,
    instructor_gender: InstructorGender = "any",
    busy_ids: Optional[set[int]] = None,
    allow_duty: bool = False,
    service_type: Optional[ServiceType] = None,
    preserve_existing_assignment: bool = False,
) -> bool:
    if not instructor.is_active or (
        instructor.is_duty and not allow_duty and not preserve_existing_assignment
    ):
        return False
    if busy_ids is not None and instructor.id in busy_ids:
        return False
    # A later profile edit must not invalidate a booking that already owns this
    # instructor. New bookings still pass the current lesson/transmission rules.
    if not preserve_existing_assignment:
        if not _teaches_service(instructor, service_type):
            return False
        if transmission == "manual" and instructor.transmission not in ("manual", "both"):
            return False
        if transmission == "automatic" and instructor.transmission not in ("automatic", "both"):
            return False
    inst_gender = (instructor.gender.lower() if instructor.gender else "any") if isinstance(instructor.gender, str) else (instructor.gender.value if hasattr(instructor.gender, "value") else "any")
    if instructor_gender != "any" and inst_gender != "any" and inst_gender != instructor_gender:
        return False
    day_name = _get_day_name(booking_date)
    days_off_list = [d.strip() for d in (instructor.days_off or "").split(",") if d.strip()]
    if day_name in days_off_list:
        return False
    schedule = await _get_effective_schedule(db, instructor, booking_date)
    if not schedule:
        return False
    work_start, work_end, lunch_start, lunch_end = schedule
    # working_hours_end — последний допустимый СТАРТ записи, а не время,
    # к которому занятие должно завершиться. Например, при окончании в 19:00
    # слот 19:00 должен быть доступен и для часа вождения, и для экзамена.
    if work_start > start_time or work_end < start_time:
        return False
    if not _is_empty_lunch(lunch_start, lunch_end) and _is_in_lunch(start_time, end_time, lunch_start, lunch_end):
        return False
    return True


async def _get_active_instructors(db: AsyncSession) -> List[Instructor]:
    result = await db.execute(select(Instructor).where(Instructor.is_active == True))
    return result.scalars().all()


async def _count_vehicle_usage(
    db: AsyncSession,
    booking_date: date,
    start_time: time,
    end_time: time,
    transmission: TransmissionType,
    exclude_booking_id: Optional[int] = None,
) -> int:
    """Count all active requests that consume a car of this gearbox type.

    Legacy records may not have a `vehicle_id`. They still consume capacity,
    therefore capacity is never accidentally released during the rollout.
    """
    conditions = [
        Booking.booking_date == booking_date,
        Booking.start_time < end_time,
        Booking.end_time > start_time,
        Booking.transmission == _value(transmission),
        Booking.status.in_(ACTIVE_BOOKING_STATUSES),
    ]
    if exclude_booking_id is not None:
        conditions.append(Booking.id != exclude_booking_id)
    bookings = await db.execute(select(func.count()).select_from(Booking).where(and_(*conditions)))
    mobile_bookings = await db.execute(
        select(func.count()).select_from(MobileBooking).where(and_(
            MobileBooking.booking_date == booking_date,
            MobileBooking.start_time < end_time,
            MobileBooking.end_time > start_time,
            MobileBooking.transmission == _value(transmission),
            MobileBooking.status.in_(ACTIVE_MOBILE_BOOKING_STATUSES),
        ))
    )
    return (bookings.scalar() or 0) + (mobile_bookings.scalar() or 0)


async def has_available_vehicle(
    db: AsyncSession,
    booking_date: date,
    start_time: time,
    end_time: time,
    transmission: TransmissionType,
    exclude_booking_id: Optional[int] = None,
) -> bool:
    """Return whether at least one physical compatible car is free."""
    vehicles_count = (await db.execute(
        select(func.count()).select_from(Vehicle).where(
            Vehicle.transmission == _value(transmission),
            Vehicle.is_under_repair == False,
        )
    )).scalar() or 0
    if vehicles_count == 0:
        return False
    usage = await _count_vehicle_usage(
        db, booking_date, start_time, end_time, transmission, exclude_booking_id
    )
    return usage < vehicles_count


async def reserve_available_vehicle(
    db: AsyncSession,
    booking_date: date,
    start_time: time,
    end_time: time,
    transmission: TransmissionType,
    exclude_booking_id: Optional[int] = None,
) -> Optional[Vehicle]:
    """Lock and select a compatible car for the surrounding booking transaction.

    Every writer locks the same ordered set of vehicles before it checks
    availability. Together with the PostgreSQL exclusion constraint this
    prevents two simultaneous requests from taking the last compatible car.
    """
    vehicles = (await db.execute(
        select(Vehicle).where(
            Vehicle.transmission == _value(transmission),
            Vehicle.is_under_repair == False,
        )
        .order_by(Vehicle.id).with_for_update()
    )).scalars().all()
    if not vehicles or not await has_available_vehicle(
        db, booking_date, start_time, end_time, transmission, exclude_booking_id
    ):
        return None

    used_conditions = [
        Booking.booking_date == booking_date,
        Booking.start_time < end_time,
        Booking.end_time > start_time,
        Booking.vehicle_id.isnot(None),
        Booking.status.in_(ACTIVE_BOOKING_STATUSES),
    ]
    if exclude_booking_id is not None:
        used_conditions.append(Booking.id != exclude_booking_id)
    used_ids = set((await db.execute(
        select(Booking.vehicle_id).where(and_(*used_conditions))
    )).scalars().all())
    return next((vehicle for vehicle in vehicles if vehicle.id not in used_ids), None)


async def _count_booked_at_location(
    db: AsyncSession,
    booking_date: date,
    start_time: time,
    end_time: time,
    location: str,
) -> int:
    """Считает количество записей на конкретной площадке в конкретный слот."""
    r1 = await db.execute(
        select(func.count()).select_from(Booking).where(
            and_(
                Booking.booking_date == booking_date,
                Booking.start_time < end_time,
                Booking.end_time > start_time,
                Booking.location == location,
                Booking.status.in_(ACTIVE_BOOKING_STATUSES),
            )
        )
    )
    r2 = await db.execute(
        select(func.count()).select_from(MobileBooking).where(
            and_(
                MobileBooking.booking_date == booking_date,
                MobileBooking.start_time < end_time,
                MobileBooking.end_time > start_time,
                MobileBooking.location == location,
                MobileBooking.status.in_(ACTIVE_MOBILE_BOOKING_STATUSES),
            )
        )
    )
    return (r1.scalar() or 0) + (r2.scalar() or 0)


async def get_training_location(
    db: AsyncSession,
    booking_date: date,
    start_time: time,
    end_time: time,
) -> Optional[str]:
    loc = settings.LOCATION_EXAM
    count = await _count_booked_at_location(db, booking_date, start_time, end_time, loc)
    return loc if count < settings.MAX_CARS_EXAM_LOCATION else None


async def _count_available_instructors(
    db: AsyncSession,
    booking_date: date,
    start_time: time,
    end_time: time,
    transmission: TransmissionType,
    location: str,
    instructor_gender: InstructorGender = "any",
    service_type: Optional[ServiceType] = None,
) -> int:
    result = await db.execute(select(Instructor).where(Instructor.is_active == True))
    instructors = result.scalars().all()

    matching = []
    for inst in instructors:
        if await _is_instructor_available(db, inst, booking_date, start_time, end_time, transmission, instructor_gender, service_type=service_type):
            matching.append(inst)

    if not matching:
        return 0

    # Проверяем лимит машин на площадке
    booked_at_location = await _count_booked_at_location(db, booking_date, start_time, end_time, location)
    if booked_at_location >= settings.MAX_CARS_EXAM_LOCATION:
        return 0
    if not await has_available_vehicle(
        db, booking_date, start_time, end_time, transmission
    ):
        return 0

    # Проверяем кто из инструкторов занят
    busy_ids_result = await db.execute(
        select(Booking.instructor_id).where(
            and_(
                Booking.booking_date == booking_date,
                Booking.start_time < end_time,
                Booking.end_time > start_time,
                Booking.status.in_(ACTIVE_BOOKING_STATUSES),
            )
        )
    )
    busy_ids = set(row[0] for row in busy_ids_result.all())

    mobile_busy_result = await db.execute(
        select(MobileBooking.instructor_id).where(
            and_(
                MobileBooking.booking_date == booking_date,
                MobileBooking.start_time < end_time,
                MobileBooking.end_time > start_time,
                MobileBooking.status.in_(ACTIVE_MOBILE_BOOKING_STATUSES),
            )
        )
    )
    busy_ids.update(row[0] for row in mobile_busy_result.all())

    free = [inst for inst in matching if inst.id not in busy_ids]
    if not free:
        duty_result = await db.execute(select(Instructor).where(and_(Instructor.is_active == True, Instructor.is_duty == True)))
        duty = duty_result.scalar_one_or_none()
        if duty and await _is_instructor_available(db, duty, booking_date, start_time, end_time, transmission, instructor_gender, busy_ids, allow_duty=True, service_type=service_type):
            return 1
    return len(free)


async def get_available_slots(
    db: AsyncSession,
    booking_date: date,
    service_type: ServiceType,
    transmission: TransmissionType,
    location: str,
    instructor_gender: InstructorGender = "any",
    location_preference: Optional[str] = None,
    *,
    stop_after_first: bool = False,
) -> List[time]:
    duration_minutes = settings.TRAINING_DURATION_MINUTES if service_type == ServiceType.TRAINING else settings.EXAM_DURATION_MINUTES

    now = datetime.now(TIMEZONE)
    is_today = booking_date == now.date()
    current_time = now.time()

    # Если сегодня и уже 21:00 или позже, день недоступен для записи
    if is_today and current_time >= time(21, 0):
        return []

    all_instructors = await _get_active_instructors(db)
    if not all_instructors:
        return []

    schedules = [await _get_effective_schedule(db, inst, booking_date) for inst in all_instructors]
    schedules = [s for s in schedules if s]
    if not schedules:
        return []
    earliest_start_minutes = min(s[0].hour * 60 + s[0].minute for s in schedules)
    
    # working_hours_end — последний допустимый старт. Поэтому при графике
    # до 19:00 слот 19:00 включается независимо от длительности услуги.
    absolute_max_start_minutes = max(s[1].hour * 60 + s[1].minute for s in schedules)

    absolute_max_start_minutes = min(absolute_max_start_minutes, 21 * 60)

    if is_today:
        after_cutoff = now.hour * 60 + now.minute >= absolute_max_start_minutes
        if after_cutoff:
            return []

    slots = []

    if is_today:
        # Слоты всегда начинаются с границы длительности услуги. Для экзамена
        # это :00, :20 и :40, а не только начало каждого часа.
        current_minutes = max(earliest_start_minutes, ((now.hour * 60 + now.minute) // duration_minutes + 1) * duration_minutes)
    else:
        current_minutes = earliest_start_minutes

    while current_minutes <= absolute_max_start_minutes:
        current_t = time(current_minutes // 60, current_minutes % 60)
        end_minutes = current_minutes + duration_minutes
        end_t = time(end_minutes // 60, end_minutes % 60)

        # Если сегодняшний день, пропускаем слоты которые уже прошли
        if is_today and current_t <= current_time:
            current_minutes += duration_minutes
            continue

        if service_type == ServiceType.TRAINING:
            # Для тренировки: если клиент указал площадку — используем её,
            # иначе определяем автоматически по загруженности
            if location_preference:
                training_loc = location_preference
            else:
                training_loc = await get_training_location(db, booking_date, current_t, end_t)
            if training_loc is not None:
                # Проверяем есть ли хоть один доступный инструктор
                available = await _count_available_instructors(
                    db, booking_date, current_t, end_t, transmission, training_loc, instructor_gender, service_type
                )
                if available > 0:
                    slots.append(current_t)
                    if stop_after_first:
                        return slots
        else:
            available = await _count_available_instructors(
                db, booking_date, current_t, end_t, transmission, location, instructor_gender, service_type
            )
            if available > 0:
                slots.append(current_t)
                if stop_after_first:
                    return slots

        current_minutes += duration_minutes

    return slots


async def get_available_slots_for_instructor(
    db: AsyncSession,
    booking_date: date,
    service_type: ServiceType,
    transmission: TransmissionType,
    location: str,
    instructor_id: int,
    preserve_existing_assignment: bool = False,
) -> List[time]:
    """
    Возвращает доступные слоты для КОНКРЕТНОГО инструктора.
    Используется при переносе записи — клиент не может сменить инструктора,
    поэтому слоты ограничены рабочими часами этого инструктора и его занятостью.
    """
    duration_minutes = settings.TRAINING_DURATION_MINUTES if service_type == ServiceType.TRAINING else settings.EXAM_DURATION_MINUTES

    now = datetime.now(TIMEZONE)
    is_today = booking_date == now.date()
    current_time = now.time()

    # Получаем конкретного инструктора
    result = await db.execute(select(Instructor).where(Instructor.id == instructor_id))
    instructor = result.scalar_one_or_none()
    if not instructor or not instructor.is_active:
        return []
    if not preserve_existing_assignment and not _teaches_service(instructor, service_type):
        return []

    schedule = await _get_effective_schedule(db, instructor, booking_date)
    if not schedule:
        return []
    work_start, work_end, lunch_start, lunch_end = schedule
    inst_start_minutes = work_start.hour * 60 + work_start.minute
    inst_end_minutes = work_end.hour * 60 + work_end.minute

    if is_today and current_time >= work_end:
        return []

    day_name = _get_day_name(booking_date)
    days_off_list = [d.strip() for d in (instructor.days_off or "").split(",") if d.strip()]
    if day_name in days_off_list:
        return []

    # Собираем реальные интервалы занятости инструктора. Почасовая проверка
    # здесь неверна для 20-минутных экзаменов: она блокирует весь час.
    busy_intervals = []

    busy_bookings = await db.execute(
        select(Booking.start_time, Booking.end_time).where(
            and_(
                Booking.instructor_id == instructor_id,
                Booking.booking_date == booking_date,
                Booking.status.in_(["pending", "cancellation_pending", "reschedule_pending", "planned", "confirmed", "in_progress"]),
            )
        )
    )
    for row in busy_bookings.all():
        busy_intervals.append(row)

    busy_mobile = await db.execute(
        select(MobileBooking.start_time, MobileBooking.end_time).where(
            and_(
                MobileBooking.instructor_id == instructor_id,
                MobileBooking.booking_date == booking_date,
                MobileBooking.status.in_(["pending", "planned", "confirmed"]),
            )
        )
    )
    for row in busy_mobile.all():
        busy_intervals.append(row)

    slots = []
    if is_today:
        current_minutes = max(inst_start_minutes, ((now.hour * 60 + now.minute) // duration_minutes + 1) * duration_minutes)
    else:
        current_minutes = inst_start_minutes

    while current_minutes <= inst_end_minutes:
        current_t = time(current_minutes // 60, current_minutes % 60)
        end_minutes = current_minutes + duration_minutes
        end_t = time(end_minutes // 60, end_minutes % 60)

        if is_today and current_t <= current_time:
            current_minutes += duration_minutes
            continue

        # Проверяем 개인ный обед инструктора
        if work_start > current_t or work_end < current_t:
            current_minutes += duration_minutes
            continue

        if not _is_empty_lunch(lunch_start, lunch_end) and _is_in_lunch(current_t, end_t, lunch_start, lunch_end):
            current_minutes += duration_minutes
            continue

        # Проверяем что инструктор свободен в этот слот
        if any(current_t < busy_end and end_t > busy_start for busy_start, busy_end in busy_intervals):
            current_minutes += duration_minutes
            continue

        # Проверяем лимит машин на площадке (для тренировок)
        if service_type == ServiceType.TRAINING:
            training_loc = await get_training_location(db, booking_date, current_t, end_t)
            if training_loc is None:
                current_minutes += duration_minutes
                continue

        if not await has_available_vehicle(
            db, booking_date, current_t, end_t, transmission
        ):
            current_minutes += duration_minutes
            continue

        slots.append(current_t)
        current_minutes += duration_minutes

    return slots


async def find_best_instructor(
    db: AsyncSession,
    booking_date: date,
    start_time: time,
    end_time: time,
    transmission: TransmissionType,
    location: str,
    instructor_gender: InstructorGender = "any",
    service_type: Optional[ServiceType] = None,
) -> Optional[Instructor]:
    from app.models.models import InstructorRotation

    result = await db.execute(select(Instructor).where(Instructor.is_active == True))
    all_instructors = result.scalars().all()

    busy_result = await db.execute(
        select(Booking.instructor_id).where(
            and_(
                Booking.booking_date == booking_date,
                Booking.start_time < end_time,
                Booking.end_time > start_time,
                Booking.status.in_(["pending", "cancellation_pending", "reschedule_pending", "planned", "confirmed"]),
            )
        )
    )
    busy_ids = set(row[0] for row in busy_result.all())

    mobile_busy_result = await db.execute(
        select(MobileBooking.instructor_id).where(
            and_(
                MobileBooking.booking_date == booking_date,
                MobileBooking.start_time < end_time,
                MobileBooking.end_time > start_time,
                MobileBooking.status.in_(["pending", "planned", "confirmed"]),
            )
        )
    )
    busy_ids.update(row[0] for row in mobile_busy_result.all())

    suitable = []
    for inst in all_instructors:
        if await _is_instructor_available(db, inst, booking_date, start_time, end_time, transmission, instructor_gender, busy_ids, service_type=service_type):
            suitable.append(inst)

    if not suitable:
        duty_result = await db.execute(
            select(Instructor).where(
                and_(
                    Instructor.is_active == True,
                    Instructor.is_duty == True,
                )
            )
        )
        duty_instructor = duty_result.scalar_one_or_none()
        if duty_instructor:
            if not _teaches_service(duty_instructor, service_type):
                return None
            # Проверяем КПП дежурного
            if transmission == "manual" and duty_instructor.transmission not in ("manual", "both"):
                return None
            if transmission == "automatic" and duty_instructor.transmission not in ("automatic", "both"):
                return None
            # Проверяем занятость дежурного
            if duty_instructor.id in busy_ids:
                return None
            # Проверяем рабочие часы дежурного
            duty_schedule = await _get_effective_schedule(db, duty_instructor, booking_date)
            if not duty_schedule:
                return None
            duty_start, duty_end, duty_lunch_start, duty_lunch_end = duty_schedule
            if duty_start <= start_time and duty_end >= start_time and (
                _is_empty_lunch(duty_lunch_start, duty_lunch_end)
                or not _is_in_lunch(start_time, end_time, duty_lunch_start, duty_lunch_end)
            ):
                return duty_instructor
        return None

    rotation_result = await db.execute(
        select(InstructorRotation).where(
            InstructorRotation.instructor_id.in_([i.id for i in suitable])
        )
    )
    rotations = {r.instructor_id: r for r in rotation_result.scalars().all()}

    suitable.sort(key=lambda inst: rotations[inst.id].rotation_count if inst.id in rotations else 0)

    chosen = suitable[0]

    if chosen.id in rotations:
        rot = rotations[chosen.id]
        rot.rotation_count += 1
        rot.last_booking_date = booking_date
        rot.last_booking_time = start_time
        rot.updated_at = datetime.utcnow()
    else:
        new_rot = InstructorRotation(
            instructor_id=chosen.id,
            last_booking_date=booking_date,
            last_booking_time=start_time,
            rotation_count=1,
        )
        db.add(new_rot)

    await db.commit()
    return chosen


async def find_best_instructor_with_location(
    db: AsyncSession,
    booking_date: date,
    start_time: time,
    end_time: time,
    transmission: TransmissionType,
    service_type: ServiceType,
    instructor_gender: InstructorGender = "any",
) -> tuple[Optional[Instructor], Optional[str]]:
    """Выбирает инструктора и единственную актуальную площадку Циолковского 30."""
    location = settings.LOCATION_EXAM
    if service_type == ServiceType.TRAINING:
        location = await get_training_location(db, booking_date, start_time, end_time)
        if not location:
            return None, None

    if not await has_available_vehicle(db, booking_date, start_time, end_time, transmission):
        return None, None

    instructor = await find_best_instructor(
        db, booking_date, start_time, end_time, transmission, location, instructor_gender, service_type
    )
    if instructor:
        return instructor, location
    if not location:
        return None, None
    return None, None


async def has_available_instructors(
    db: AsyncSession,
    booking_date: date,
    service_type: ServiceType,
    transmission: TransmissionType,
    instructor_gender: InstructorGender = "any",
) -> bool:
    # Date buttons in Telegram must not lead to an empty time list. Reuse the
    # canonical slot calculation so the instructor and vehicle rules match.
    return bool(await get_available_slots(
        db, booking_date, service_type, transmission, settings.LOCATION_EXAM,
        instructor_gender,
        stop_after_first=True,
    ))
