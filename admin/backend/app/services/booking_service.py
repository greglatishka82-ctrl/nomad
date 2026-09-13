from datetime import date, time, datetime
from typing import Optional

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.models import Booking, BookingResource, Vehicle, Instructor, InstructorDayOff, InstructorDailySchedule, InstructorRotation, MobileBooking


RUSSIAN_DAY_NAMES = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
ACTIVE_BOOKING_STATUSES = ("pending", "cancellation_pending", "reschedule_pending", "planned", "confirmed", "in_progress")
ACTIVE_MOBILE_BOOKING_STATUSES = ("pending", "planned", "confirmed", "in_progress")
EXAM_SENSOR_RESOURCE_KEY = "exam_sensor_kits"


def _day_name(d: date) -> str:
    return RUSSIAN_DAY_NAMES[d.weekday()]


def _lunch_is_empty(start: Optional[time], end: Optional[time]) -> bool:
    return not start or not end or start == end or (
        start.hour == 0 and start.minute == 0 and end.hour == 0 and end.minute == 0
    )


def _overlaps(start: time, end: time, busy_start: time, busy_end: time) -> bool:
    return start < busy_end and end > busy_start


def appointment_fits_schedule(
    start_time: time,
    end_time: time,
    work_start: Optional[time],
    work_end: Optional[time],
    lunch_start: Optional[time],
    lunch_end: Optional[time],
) -> bool:
    """Check schedule rules without consulting mutable instructor criteria."""
    if not work_start or not work_end:
        return False
    # working_hours_end is the last allowed lesson start, not lesson end.
    if work_start > start_time or work_end < start_time:
        return False
    if not _lunch_is_empty(lunch_start, lunch_end) and _overlaps(
        start_time, end_time, lunch_start, lunch_end
    ):
        return False
    return True


def teaches_service(instructor: Instructor, service_type: str) -> bool:
    lesson_type = str(getattr(instructor, "lesson_type", "both") or "both").lower()
    requested = service_type.value if hasattr(service_type, "value") else str(service_type).lower()
    return lesson_type in ("both", requested)


async def get_effective_schedule(db: AsyncSession, instructor: Instructor, schedule_date: date):
    result = await db.execute(
        select(InstructorDailySchedule).where(
            and_(
                InstructorDailySchedule.instructor_id == instructor.id,
                InstructorDailySchedule.schedule_date == schedule_date,
            )
        )
    )
    daily = result.scalar_one_or_none()
    if daily and daily.is_day_off:
        return None
    if not daily:
        day_off_result = await db.execute(
            select(InstructorDayOff.id).where(
                and_(
                    InstructorDayOff.instructor_id == instructor.id,
                    InstructorDayOff.day_off_date == schedule_date,
                )
            )
        )
        if day_off_result.scalar_one_or_none() is not None:
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


async def get_busy_instructor_ids(db: AsyncSession, booking_date: date, start_time: time, end_time: time) -> set[int]:
    result = await db.execute(
        select(Booking.instructor_id).where(
            and_(
                Booking.booking_date == booking_date,
                Booking.start_time < end_time,
                Booking.end_time > start_time,
                Booking.status.in_(["pending", "cancellation_pending", "reschedule_pending", "planned", "confirmed", "in_progress"]),
            )
        )
    )
    busy = {row[0] for row in result.all()}
    mobile_result = await db.execute(
        select(MobileBooking.instructor_id).where(
            and_(
                MobileBooking.booking_date == booking_date,
                MobileBooking.start_time < end_time,
                MobileBooking.end_time > start_time,
                MobileBooking.status.in_(["pending", "planned", "confirmed"]),
            )
        )
    )
    busy.update(row[0] for row in mobile_result.all())
    return busy


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
    return await has_booking_capacity(
        db, booking_date, start_time, end_time, transmission, service_type, exclude_booking_id,
    )


async def count_booked_at_location(
    db: AsyncSession, booking_date: date, start_time: time, end_time: time,
    location: str, exclude_booking_id: Optional[int] = None,
) -> int:
    return await _peak_booking_usage(
        db, booking_date, start_time, end_time, location=location,
        exclude_booking_id=exclude_booking_id,
    )


async def is_instructor_available(
    db: AsyncSession,
    instructor: Instructor,
    booking_date: date,
    start_time: time,
    end_time: time,
    transmission: str,
    busy_ids: Optional[set[int]] = None,
    allow_duty: bool = False,
    service_type: Optional[str] = None,
    preserve_existing_assignment: bool = False,
) -> bool:
    if not instructor.is_active or (
        instructor.is_duty and not allow_duty and not preserve_existing_assignment
    ):
        return False
    if busy_ids is not None and instructor.id in busy_ids:
        return False
    # Existing bookings keep their original assignment even if the instructor
    # card is narrowed later. New assignments always use the current profile.
    if not preserve_existing_assignment:
        if service_type and not teaches_service(instructor, service_type):
            return False
        if transmission == "manual" and instructor.transmission not in ("manual", "both"):
            return False
        if transmission == "automatic" and instructor.transmission not in ("automatic", "both"):
            return False
    days_off = [d.strip() for d in (instructor.days_off or "").split(",") if d.strip()]
    if _day_name(booking_date) in days_off:
        return False
    schedule = await get_effective_schedule(db, instructor, booking_date)
    if not schedule:
        return False
    work_start, work_end, lunch_start, lunch_end = schedule
    return appointment_fits_schedule(
        start_time, end_time, work_start, work_end, lunch_start, lunch_end
    )


async def find_best_instructor(
    db: AsyncSession, booking_date: date, start_time: time, end_time: time,
    transmission: str, service_type: str,
):
    busy_ids = await get_busy_instructor_ids(db, booking_date, start_time, end_time)
    result = await db.execute(select(Instructor).where(Instructor.is_active == True))
    instructors = result.scalars().all()
    suitable = []
    for instructor in instructors:
        if await is_instructor_available(db, instructor, booking_date, start_time, end_time, transmission, busy_ids, service_type=service_type):
            suitable.append(instructor)
    if not suitable:
        duty_result = await db.execute(select(Instructor).where(and_(Instructor.is_active == True, Instructor.is_duty == True)))
        duty = duty_result.scalar_one_or_none()
        if duty and await is_instructor_available(db, duty, booking_date, start_time, end_time, transmission, busy_ids, allow_duty=True, service_type=service_type):
            return duty
        return None

    rotation_result = await db.execute(
        select(InstructorRotation).where(InstructorRotation.instructor_id.in_([i.id for i in suitable]))
    )
    rotations = {r.instructor_id: r for r in rotation_result.scalars().all()}
    suitable.sort(key=lambda inst: rotations[inst.id].rotation_count if inst.id in rotations else 0)
    chosen = suitable[0]
    if chosen.id in rotations:
        rotation = rotations[chosen.id]
        rotation.rotation_count += 1
        rotation.last_booking_date = booking_date
        rotation.last_booking_time = start_time
        rotation.updated_at = datetime.utcnow()
    else:
        db.add(InstructorRotation(
            instructor_id=chosen.id,
            last_booking_date=booking_date,
            last_booking_time=start_time,
            rotation_count=1,
        ))
    await db.commit()
    return chosen


async def slot_has_capacity(
    db: AsyncSession,
    booking_date: date,
    start_time: time,
    end_time: time,
    location: str,
    transmission: str,
    service_type: str = "training",
    exclude_booking_id: Optional[int] = None,
) -> bool:
    has_location_capacity = await count_booked_at_location(
        db, booking_date, start_time, end_time, location,
        exclude_booking_id=exclude_booking_id,
    ) < settings.MAX_CARS_EXAM_LOCATION
    return has_location_capacity and await has_booking_capacity(
        db, booking_date, start_time, end_time, transmission, service_type,
        exclude_booking_id=exclude_booking_id,
    )
