from datetime import date, time, timedelta, datetime
from typing import List, Optional

from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings, TIMEZONE
from app.models.models import (
    Booking, BookingResource, MobileBooking, Vehicle, Instructor, InstructorDayOff, InstructorDailySchedule, InstructorRotation, BookingStatus, ServiceType, TransmissionType, InstructorGender
)

RUSSIAN_DAY_NAMES = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
ACTIVE_BOOKING_STATUSES = ("pending", "cancellation_pending", "reschedule_pending", "planned", "confirmed", "in_progress")
ACTIVE_MOBILE_BOOKING_STATUSES = ("pending", "planned", "confirmed", "in_progress")
EXAM_SENSOR_RESOURCE_KEY = "exam_sensor_kits"
DAILY_LOAD_BOOKING_STATUSES = ACTIVE_BOOKING_STATUSES + ("completed", "no_show")
DAILY_LOAD_MOBILE_BOOKING_STATUSES = ACTIVE_MOBILE_BOOKING_STATUSES + ("completed", "no_show")
ONSITE_BOOKING_STATUSES = ("cancellation_pending", "reschedule_pending", "planned", "confirmed", "in_progress")
ONSITE_MOBILE_BOOKING_STATUSES = ("planned", "confirmed", "in_progress")
OFFSITE_ARRIVAL_TIME = timedelta(minutes=30)
ONSITE_DAILY_PRIORITY_LIMIT = 2

# Минуты в сутках. time(24, 0) в Python не существует, поэтому занятие,
# начавшееся в 23:00, заканчивается в 00:00 и хранится как 23:59.
DAY_MINUTES = 24 * 60
LAST_MINUTE_OF_DAY = DAY_MINUTES - 1


def _value(value):
    return value.value if hasattr(value, "value") else str(value)


def time_to_minutes(value: time) -> int:
    return value.hour * 60 + value.minute


def schedule_end_minutes(end: Optional[time]) -> int:
    """График «до 00:00» означает конец суток, а не начало нового дня.

    Админ выставляет конец рабочего дня обычным полем времени, и полночь
    приходит как 00:00 — без этой нормализации инструктор считался бы
    неработающим весь день.
    """
    if not end:
        return 0
    minutes = time_to_minutes(end)
    return DAY_MINUTES if minutes == 0 else minutes


def add_minutes(start_time: time, minutes: int) -> time:
    """Конец занятия от его начала.

    Занятие, уходящее за полночь, храним как 23:59: time(24, 0) не
    существует, а все проверки пересечений сравнивают end_time > start_time.
    """
    total = time_to_minutes(start_time) + minutes
    if total >= DAY_MINUTES:
        return time(23, 59)
    return time(total // 60, total % 60)


def service_duration_minutes(service_type: Optional[ServiceType]) -> int:
    if getattr(service_type, "value", service_type) == ServiceType.EXAM.value:
        return settings.EXAM_DURATION_MINUTES
    return settings.TRAINING_DURATION_MINUTES


def last_start_within_day(duration_minutes: int) -> int:
    """Последний старт, при котором занятие ещё укладывается в текущие сутки.

    time(24, 0) не существует, поэтому занятие не может начинаться позже
    DAY_MINUTES - duration_minutes. Это единственная физическая граница.
    """
    return max(0, DAY_MINUTES - duration_minutes)


def next_client_slot_start_minutes(
    service_type: Optional[ServiceType],
    now: Optional[datetime] = None,
) -> int:
    """First client-visible slot for today in the Pavlodar time zone.

    At 20:30 the 21:00 driving lesson can no longer be requested: the first
    evening slot becomes 22:00.  At 20:29 and earlier 21:00 remains available.
    Exam slots keep their separate 20:00 business limit.
    """
    local_now = now or datetime.now(TIMEZONE)
    if local_now.tzinfo:
        local_now = local_now.astimezone(TIMEZONE)
    now_minutes = local_now.hour * 60 + local_now.minute
    duration_minutes = service_duration_minutes(service_type)
    next_slot = ((now_minutes // duration_minutes) + 1) * duration_minutes
    if (
        getattr(service_type, "value", service_type) == ServiceType.TRAINING.value
        and 20 * 60 + 30 <= now_minutes < 21 * 60
    ):
        next_slot = max(next_slot, 22 * 60)
    return next_slot


def is_client_slot_start_allowed(
    booking_date: date,
    start_time: time,
    service_type: Optional[ServiceType],
    now: Optional[datetime] = None,
) -> bool:
    """Reject stale client slot submissions using the same rule as availability."""
    local_now = now or datetime.now(TIMEZONE)
    if local_now.tzinfo:
        local_now = local_now.astimezone(TIMEZONE)
    start_minutes = time_to_minutes(start_time)
    if start_minutes > client_last_start_minutes(service_type):
        return False
    if booking_date != local_now.date():
        return True
    return start_minutes >= next_client_slot_start_minutes(service_type, local_now)


def client_last_start_minutes(service_type: Optional[ServiceType]) -> int:
    """Верхняя граница старта для клиента, в минутах от начала суток.

    Жёстких часов школы здесь нет: вождение ограничено только графиком
    инструктора и тем, что занятие должно уложиться в сутки. Экзамен
    дополнительно ограничен бизнес-правилом EXAM_LAST_SLOT_HOUR; если оно
    выключено, экзамен идёт по тому же графику, что и вождение.
    """
    limit = last_start_within_day(service_duration_minutes(service_type))
    exam_limit = getattr(settings, "EXAM_LAST_SLOT_HOUR", None)
    if exam_limit and getattr(service_type, "value", service_type) == ServiceType.EXAM.value:
        limit = min(limit, int(exam_limit) * 60)
    return limit


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


async def _get_on_site_instructor_ids(
    db: AsyncSession,
    reference_now: datetime,
    location: str,
) -> set[int]:
    """Return instructors whose active lesson proves they are at the location now."""
    current_time = reference_now.time().replace(tzinfo=None)
    common_conditions = (
        Booking.booking_date == reference_now.date(),
        Booking.start_time <= current_time,
        Booking.end_time > current_time,
        Booking.location == location,
        Booking.instructor_id.isnot(None),
        Booking.status.in_(ONSITE_BOOKING_STATUSES),
    )
    booking_ids = set((await db.execute(
        select(Booking.instructor_id).where(and_(*common_conditions))
    )).scalars().all())

    mobile_ids = set((await db.execute(
        select(MobileBooking.instructor_id).where(and_(
            MobileBooking.booking_date == reference_now.date(),
            MobileBooking.start_time <= current_time,
            MobileBooking.end_time > current_time,
            MobileBooking.location == location,
            MobileBooking.instructor_id.isnot(None),
            MobileBooking.status.in_(ONSITE_MOBILE_BOOKING_STATUSES),
        ))
    )).scalars().all())
    return booking_ids | mobile_ids


async def _get_daily_instructor_loads(
    db: AsyncSession,
    booking_date: date,
    instructor_ids: set[int],
) -> dict[int, int]:
    """Count assignments that contribute to the selected day's workload."""
    if not instructor_ids:
        return {}

    booking_rows = (await db.execute(
        select(Booking.instructor_id, func.count()).where(and_(
            Booking.booking_date == booking_date,
            Booking.instructor_id.in_(instructor_ids),
            Booking.status.in_(DAILY_LOAD_BOOKING_STATUSES),
        )).group_by(Booking.instructor_id)
    )).all()
    mobile_rows = (await db.execute(
        select(MobileBooking.instructor_id, func.count()).where(and_(
            MobileBooking.booking_date == booking_date,
            MobileBooking.instructor_id.in_(instructor_ids),
            MobileBooking.status.in_(DAILY_LOAD_MOBILE_BOOKING_STATUSES),
        )).group_by(MobileBooking.instructor_id)
    )).all()

    loads = {instructor_id: count for instructor_id, count in booking_rows}
    for instructor_id, count in mobile_rows:
        loads[instructor_id] = loads.get(instructor_id, 0) + count
    return loads


def _meets_arrival_requirement(
    instructor: Instructor,
    booking_date: date,
    start_time: time,
    reference_now: datetime,
    on_site_instructor_ids: set[int],
) -> bool:
    """Apply the 30-minute arrival rule only to today's off-site instructors."""
    if booking_date != reference_now.date():
        return True
    if instructor.is_duty or instructor.id in on_site_instructor_ids:
        return True
    slot_start = datetime.combine(booking_date, start_time, tzinfo=TIMEZONE)
    local_now = reference_now.astimezone(TIMEZONE) if reference_now.tzinfo else reference_now.replace(tzinfo=TIMEZONE)
    return slot_start - local_now >= OFFSITE_ARRIVAL_TIME


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
    reference_now: Optional[datetime] = None,
    on_site_instructor_ids: Optional[set[int]] = None,
    location: Optional[str] = None,
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
    # График «до 00:00» приходит как 00:00 и означает конец суток.
    if work_start > start_time or schedule_end_minutes(work_end) < time_to_minutes(start_time):
        return False
    # Пробный экзамен клиент начинает не позже 20:00, вождение — до закрытия.
    if time_to_minutes(start_time) > client_last_start_minutes(service_type):
        return False
    if not _is_empty_lunch(lunch_start, lunch_end) and _is_in_lunch(start_time, end_time, lunch_start, lunch_end):
        return False
    reference_now = reference_now or datetime.now(TIMEZONE)
    if booking_date == reference_now.date():
        arrival_location = location or settings.LOCATION_EXAM
        if on_site_instructor_ids is None:
            on_site_instructor_ids = await _get_on_site_instructor_ids(
                db, reference_now, arrival_location
            )
        if not _meets_arrival_requirement(
            instructor, booking_date, start_time, reference_now, on_site_instructor_ids
        ):
            return False
    return True


async def _get_active_instructors(db: AsyncSession) -> List[Instructor]:
    result = await db.execute(select(Instructor).where(Instructor.is_active == True))
    return result.scalars().all()


def _peak_occupancy(intervals, start_time: time, end_time: time) -> int:
    """Maximum simultaneous lessons in [start_time, end_time), not row count."""
    events = []
    for busy_start, busy_end in intervals:
        start, end = max(start_time, busy_start), min(end_time, busy_end)
        if start < end:
            events.extend(((start, 1), (end, -1)))
    current = peak = 0
    # Endings precede starts at the same instant: 11:20 is available after 11:00-11:20.
    for _, delta in sorted(events):
        current += delta
        peak = max(peak, current)
    return peak


async def _peak_booking_usage(
    db: AsyncSession, booking_date: date, start_time: time, end_time: time,
    *, transmission=None, location=None, service_type=None,
    exclude_booking_id: Optional[int] = None,
) -> int:
    intervals = []
    for model, statuses in ((Booking, ACTIVE_BOOKING_STATUSES),
                            (MobileBooking, ACTIVE_MOBILE_BOOKING_STATUSES)):
        conditions = [model.booking_date == booking_date, model.start_time < end_time,
                      model.end_time > start_time, model.status.in_(statuses)]
        if transmission is not None:
            conditions.append(model.transmission == getattr(transmission, "value", transmission))
        if location is not None:
            conditions.append(model.location == location)
        if service_type is not None:
            conditions.append(model.service_type == getattr(service_type, "value", service_type))
        if model is Booking and exclude_booking_id is not None:
            conditions.append(model.id != exclude_booking_id)
        intervals.extend((await db.execute(
            select(model.start_time, model.end_time).where(*conditions)
        )).all())
    return _peak_occupancy(intervals, start_time, end_time)


async def has_available_vehicle(
    db: AsyncSession, booking_date: date, start_time: time, end_time: time,
    transmission, service_type="training", exclude_booking_id: Optional[int] = None,
) -> bool:
    """Training and exams share the gearbox pool, regardless of car usage labels."""
    transmission = getattr(transmission, "value", transmission)
    service_type = getattr(service_type, "value", service_type)
    if transmission not in ("manual", "automatic") or start_time >= end_time:
        return False
    if service_type == "exam" and transmission != "automatic":
        return False
    total = (await db.execute(select(func.count()).select_from(Vehicle).where(
        Vehicle.transmission == transmission, Vehicle.is_under_repair == False,
    ))).scalar() or 0
    if not total:
        return False
    return await _peak_booking_usage(
        db, booking_date, start_time, end_time, transmission=transmission,
        exclude_booking_id=exclude_booking_id,
    ) < total


async def get_vehicle_capacity(db: AsyncSession, transmission) -> int:
    """Number of usable physical cars for the requested gearbox."""
    transmission = getattr(transmission, "value", transmission)
    return (await db.execute(select(func.count()).select_from(Vehicle).where(
        Vehicle.transmission == transmission, Vehicle.is_under_repair == False,
    ))).scalar() or 0


async def count_booked_vehicle_capacity(
    db: AsyncSession, booking_date: date, start_time: time, end_time: time,
    transmission, exclude_booking_id: Optional[int] = None,
) -> int:
    """Peak number of cars of one gearbox occupied during the interval."""
    return await _peak_booking_usage(
        db, booking_date, start_time, end_time,
        transmission=getattr(transmission, "value", transmission),
        exclude_booking_id=exclude_booking_id,
    )


async def get_exam_sensor_capacity(db: AsyncSession) -> int:
    resource = await db.get(BookingResource, EXAM_SENSOR_RESOURCE_KEY)
    return max(0, resource.capacity) if resource else 0


async def count_booked_exam_sensor_capacity(
    db: AsyncSession, booking_date: date, start_time: time, end_time: time,
    exclude_booking_id: Optional[int] = None,
) -> int:
    return await _peak_booking_usage(
        db, booking_date, start_time, end_time, service_type="exam",
        exclude_booking_id=exclude_booking_id,
    )


async def has_available_exam_sensor(
    db: AsyncSession, booking_date: date, start_time: time, end_time: time,
    exclude_booking_id: Optional[int] = None,
) -> bool:
    capacity = await get_exam_sensor_capacity(db)
    return capacity > 0 and await count_booked_exam_sensor_capacity(
        db, booking_date, start_time, end_time, exclude_booking_id
    ) < capacity


async def has_booking_capacity(
    db: AsyncSession, booking_date: date, start_time: time, end_time: time,
    transmission, service_type="training", exclude_booking_id: Optional[int] = None,
) -> bool:
    service_type = getattr(service_type, "value", service_type)
    if not await has_available_vehicle(
        db, booking_date, start_time, end_time, transmission, service_type,
        exclude_booking_id,
    ):
        return False
    return service_type != "exam" or await has_available_exam_sensor(
        db, booking_date, start_time, end_time, exclude_booking_id
    )


async def reserve_vehicle_capacity(
    db: AsyncSession, booking_date: date, start_time: time, end_time: time,
    transmission, service_type="training", exclude_booking_id: Optional[int] = None,
) -> bool:
    """Reserve pool capacity until the caller commits its booking transaction.

    Both backends lock the exam-sensor row first (for exams), then the same
    fleet rows in the same order, and re-read occupancy. No car is assigned
    to a new booking (vehicle_id stays NULL).
    Keep these locks until commit/rollback; never commit between this call
    and writing the booking. Old physical assignments remain historical data.
    """
    service_type = getattr(service_type, "value", service_type)
    if service_type == "exam":
        resource = (await db.execute(
            select(BookingResource).where(
                BookingResource.key == EXAM_SENSOR_RESOURCE_KEY
            ).with_for_update()
        )).scalar_one_or_none()
        if not resource or resource.capacity <= 0:
            return False
    await db.execute(select(Vehicle.id).order_by(Vehicle.id).with_for_update())
    # Машины блокируются до повторного подсчёта, поэтому параллельные записи
    # сериализуются здесь, а не только в отображении слотов. Лимит площадки
    # считает фактические пересечения: экзамен занимает свои 20 минут, а
    # вождение — полный час.
    if await _count_booked_at_location(
        db, booking_date, start_time, end_time, settings.LOCATION_EXAM,
        exclude_booking_id=exclude_booking_id,
    ) >= settings.MAX_CARS_EXAM_LOCATION:
        return False
    return await has_booking_capacity(
        db, booking_date, start_time, end_time, transmission, service_type, exclude_booking_id,
    )


async def _count_booked_at_location(
    db: AsyncSession, booking_date: date, start_time: time, end_time: time,
    location: str, exclude_booking_id: Optional[int] = None,
) -> int:
    return await _peak_booking_usage(
        db, booking_date, start_time, end_time, location=location,
        exclude_booking_id=exclude_booking_id,
    )


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
    reference_now: Optional[datetime] = None,
) -> int:
    reference_now = reference_now or datetime.now(TIMEZONE)
    on_site_ids = await _get_on_site_instructor_ids(db, reference_now, location) \
        if booking_date == reference_now.date() else set()
    result = await db.execute(select(Instructor).where(Instructor.is_active == True))
    instructors = result.scalars().all()

    # Проверяем лимит машин на площадке
    booked_at_location = await _count_booked_at_location(db, booking_date, start_time, end_time, location)
    if booked_at_location >= settings.MAX_CARS_EXAM_LOCATION:
        return 0
    if not await has_booking_capacity(
        db, booking_date, start_time, end_time, transmission, service_type
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

    suitable = []
    for inst in instructors:
        if await _is_instructor_available(
            db, inst, booking_date, start_time, end_time, transmission,
            instructor_gender, busy_ids, service_type=service_type,
            reference_now=reference_now, on_site_instructor_ids=on_site_ids,
            location=location,
        ):
            suitable.append(inst)

    on_site = [inst for inst in suitable if inst.id in on_site_ids]
    if booking_date != reference_now.date():
        if suitable:
            return len(suitable)
    elif on_site:
        return len(on_site)

    # When an ordinary instructor has the full 30 minutes to arrive, keep the
    # duty instructor in reserve. For urgent slots only on-site ordinary
    # instructors reach this point; duty then remains the fallback.
    if suitable:
        return len(suitable)

    duty = next((inst for inst in instructors if inst.is_duty), None)
    if duty and await _is_instructor_available(
        db, duty, booking_date, start_time, end_time, transmission,
        instructor_gender, busy_ids, allow_duty=True, service_type=service_type,
        reference_now=reference_now, on_site_instructor_ids=on_site_ids,
        location=location,
    ):
        return 1

    return 0


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
    # Границу задают график инструктора и (для экзамена) EXAM_LAST_SLOT_HOUR.
    # Часов школы здесь нет: при 24-часовой работе список слотов не режется.
    last_start_minutes = client_last_start_minutes(service_type)

    if is_today and time_to_minutes(current_time) > last_start_minutes:
        return []

    all_instructors = await _get_active_instructors(db)
    if not all_instructors:
        return []

    schedules = [await _get_effective_schedule(db, inst, booking_date) for inst in all_instructors]
    schedules = [s for s in schedules if s]
    if not schedules:
        return []
    earliest_start_minutes = min(time_to_minutes(s[0]) for s in schedules)

    # working_hours_end — последний допустимый старт. Поэтому при графике
    # до 19:00 слот 19:00 включается независимо от длительности услуги.
    absolute_max_start_minutes = max(schedule_end_minutes(s[1]) for s in schedules)

    absolute_max_start_minutes = min(absolute_max_start_minutes, last_start_minutes)

    if is_today:
        after_cutoff = time_to_minutes(now.time()) > absolute_max_start_minutes
        if after_cutoff:
            return []

    slots = []

    if is_today:
        # Слоты всегда начинаются с границы длительности услуги. Для экзамена
        # это :00, :20 и :40, а не только начало каждого часа.
        current_minutes = max(
            earliest_start_minutes,
            next_client_slot_start_minutes(service_type, now),
        )
    else:
        current_minutes = earliest_start_minutes

    while current_minutes <= absolute_max_start_minutes:
        current_t = time(current_minutes // 60, current_minutes % 60)
        end_t = add_minutes(current_t, duration_minutes)

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
                    db, booking_date, current_t, end_t, transmission, training_loc,
                    instructor_gender, service_type, reference_now=now,
                )
                if available > 0:
                    slots.append(current_t)
                    if stop_after_first:
                        return slots
        else:
            available = await _count_available_instructors(
                db, booking_date, current_t, end_t, transmission, location,
                instructor_gender, service_type, reference_now=now,
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

    on_site_ids = await _get_on_site_instructor_ids(db, now, location) if is_today else set()

    schedule = await _get_effective_schedule(db, instructor, booking_date)
    if not schedule:
        return []
    work_start, work_end, lunch_start, lunch_end = schedule
    inst_start_minutes = time_to_minutes(work_start)
    # График «до 00:00» приходит как 00:00 — это конец суток. Сверху — только
    # бизнес-ограничение экзамена и физическая граница суток.
    inst_end_minutes = min(
        schedule_end_minutes(work_end), client_last_start_minutes(service_type)
    )

    if is_today and time_to_minutes(current_time) > inst_end_minutes:
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
        current_minutes = max(
            inst_start_minutes,
            next_client_slot_start_minutes(service_type, now),
        )
    else:
        current_minutes = inst_start_minutes

    while current_minutes <= inst_end_minutes:
        current_t = time(current_minutes // 60, current_minutes % 60)
        end_t = add_minutes(current_t, duration_minutes)

        if is_today and current_t <= current_time:
            current_minutes += duration_minutes
            continue

        # Проверяем личный обед инструктора
        if work_start > current_t or schedule_end_minutes(work_end) < time_to_minutes(current_t):
            current_minutes += duration_minutes
            continue

        if not _is_empty_lunch(lunch_start, lunch_end) and _is_in_lunch(current_t, end_t, lunch_start, lunch_end):
            current_minutes += duration_minutes
            continue

        # Проверяем что инструктор свободен в этот слот
        if any(current_t < busy_end and end_t > busy_start for busy_start, busy_end in busy_intervals):
            current_minutes += duration_minutes
            continue

        if not _meets_arrival_requirement(
            instructor, booking_date, current_t, now, on_site_ids
        ):
            current_minutes += duration_minutes
            continue

        # Проверяем лимит машин на площадке (для тренировок)
        if service_type == ServiceType.TRAINING:
            training_loc = await get_training_location(db, booking_date, current_t, end_t)
            if training_loc is None:
                current_minutes += duration_minutes
                continue

        if not await has_booking_capacity(
            db, booking_date, current_t, end_t, transmission, service_type
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
    *,
    reference_now: Optional[datetime] = None,
) -> Optional[Instructor]:
    from app.models.models import InstructorRotation

    reference_now = reference_now or datetime.now(TIMEZONE)
    on_site_ids = await _get_on_site_instructor_ids(db, reference_now, location) \
        if booking_date == reference_now.date() else set()
    result = await db.execute(select(Instructor).where(Instructor.is_active == True))
    all_instructors = result.scalars().all()

    busy_result = await db.execute(
        select(Booking.instructor_id).where(
            and_(
                Booking.booking_date == booking_date,
                Booking.start_time < end_time,
                Booking.end_time > start_time,
                Booking.status.in_(ACTIVE_BOOKING_STATUSES),
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
                MobileBooking.status.in_(ACTIVE_MOBILE_BOOKING_STATUSES),
            )
        )
    )
    busy_ids.update(row[0] for row in mobile_busy_result.all())

    suitable = []
    for inst in all_instructors:
        if await _is_instructor_available(
            db, inst, booking_date, start_time, end_time, transmission,
            instructor_gender, busy_ids, service_type=service_type,
            reference_now=reference_now, on_site_instructor_ids=on_site_ids,
            location=location,
        ):
            suitable.append(inst)

    duty_instructor = next((inst for inst in all_instructors if inst.is_duty), None)
    duty_available = bool(duty_instructor) and await _is_instructor_available(
        db, duty_instructor, booking_date, start_time, end_time, transmission,
        instructor_gender, busy_ids, allow_duty=True, service_type=service_type,
        reference_now=reference_now, on_site_instructor_ids=on_site_ids,
        location=location,
    )

    if not suitable:
        return duty_instructor if duty_available else None

    daily_loads = await _get_daily_instructor_loads(
        db, booking_date, {inst.id for inst in suitable}
    )
    is_today = booking_date == reference_now.date()
    on_site_suitable = [inst for inst in suitable if inst.id in on_site_ids]

    if is_today:
        preferred_on_site = [
            inst for inst in on_site_suitable
            if daily_loads.get(inst.id, 0) <= ONSITE_DAILY_PRIORITY_LIMIT
        ]
        prioritized = preferred_on_site or suitable
    else:
        prioritized = suitable

    if not prioritized:
        return None

    rotation_result = await db.execute(
        select(InstructorRotation).where(
            InstructorRotation.instructor_id.in_([i.id for i in prioritized])
        )
    )
    rotations = {r.instructor_id: r for r in rotation_result.scalars().all()}

    prioritized.sort(key=lambda inst: (
        daily_loads.get(inst.id, 0),
        0 if inst.id in on_site_ids else 1,
        rotations[inst.id].rotation_count if inst.id in rotations else 0,
    ))

    chosen = prioritized[0]

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

    if not await has_booking_capacity(db, booking_date, start_time, end_time, transmission, service_type):
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
