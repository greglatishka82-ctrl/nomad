from datetime import date, time, datetime
from typing import Optional

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.models import Booking, Vehicle, Instructor, InstructorDayOff, InstructorDailySchedule, InstructorRotation, MobileBooking


RUSSIAN_DAY_NAMES = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
ACTIVE_BOOKING_STATUSES = ("pending", "cancellation_pending", "reschedule_pending", "planned", "confirmed", "in_progress")
ACTIVE_MOBILE_BOOKING_STATUSES = ("pending", "planned", "confirmed", "in_progress")


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


async def _count_vehicle_usage(
    db: AsyncSession,
    booking_date: date,
    start_time: time,
    end_time: time,
    transmission: str,
    exclude_booking_id: Optional[int] = None,
) -> int:
    """Count active requests that consume a car of the requested gearbox."""
    conditions = [
        Booking.booking_date == booking_date,
        Booking.start_time < end_time,
        Booking.end_time > start_time,
        Booking.transmission == transmission,
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
            MobileBooking.transmission == transmission,
            MobileBooking.status.in_(ACTIVE_MOBILE_BOOKING_STATUSES),
        ))
    )
    return (bookings.scalar() or 0) + (mobile_bookings.scalar() or 0)


async def has_available_vehicle(
    db: AsyncSession,
    booking_date: date,
    start_time: time,
    end_time: time,
    transmission: str,
    exclude_booking_id: Optional[int] = None,
) -> bool:
    total = (await db.execute(
        select(func.count()).select_from(Vehicle).where(
            Vehicle.transmission == transmission,
            Vehicle.is_under_repair == False,
        )
    )).scalar() or 0
    if total == 0:
        return False
    return await _count_vehicle_usage(
        db, booking_date, start_time, end_time, transmission, exclude_booking_id
    ) < total


async def reserve_available_vehicle(
    db: AsyncSession,
    booking_date: date,
    start_time: time,
    end_time: time,
    transmission: str,
    exclude_booking_id: Optional[int] = None,
) -> Optional[Vehicle]:
    """Lock one compatible physical car in the surrounding write transaction."""
    vehicles = (await db.execute(
        select(Vehicle).where(
            Vehicle.transmission == transmission,
            Vehicle.is_under_repair == False,
        )
        .order_by(Vehicle.id).with_for_update()
    )).scalars().all()
    if not vehicles or not await has_available_vehicle(
        db, booking_date, start_time, end_time, transmission, exclude_booking_id
    ):
        return None
    conditions = [
        Booking.booking_date == booking_date,
        Booking.start_time < end_time,
        Booking.end_time > start_time,
        Booking.vehicle_id.isnot(None),
        Booking.status.in_(ACTIVE_BOOKING_STATUSES),
    ]
    if exclude_booking_id is not None:
        conditions.append(Booking.id != exclude_booking_id)
    used_ids = set((await db.execute(
        select(Booking.vehicle_id).where(and_(*conditions))
    )).scalars().all())
    return next((vehicle for vehicle in vehicles if vehicle.id not in used_ids), None)


async def count_booked_at_location(
    db: AsyncSession,
    booking_date: date,
    start_time: time,
    end_time: time,
    location: str,
    exclude_booking_id: Optional[int] = None,
) -> int:
    conditions = [
        Booking.booking_date == booking_date,
        Booking.start_time < end_time,
        Booking.end_time > start_time,
        Booking.location == location,
        Booking.status.in_(["pending", "cancellation_pending", "reschedule_pending", "planned", "confirmed", "in_progress"]),
    ]
    if exclude_booking_id is not None:
        conditions.append(Booking.id != exclude_booking_id)
    result = await db.execute(
        select(func.count()).select_from(Booking).where(and_(*conditions))
    )
    mobile_result = await db.execute(
        select(func.count()).select_from(MobileBooking).where(
            and_(
                MobileBooking.booking_date == booking_date,
                MobileBooking.start_time < end_time,
                MobileBooking.end_time > start_time,
                MobileBooking.location == location,
                MobileBooking.status.in_(["pending", "planned", "confirmed"]),
            )
        )
    )
    return (result.scalar() or 0) + (mobile_result.scalar() or 0)


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
    exclude_booking_id: Optional[int] = None,
) -> bool:
    has_location_capacity = await count_booked_at_location(
        db, booking_date, start_time, end_time, location,
        exclude_booking_id=exclude_booking_id,
    ) < settings.MAX_CARS_EXAM_LOCATION
    return has_location_capacity and await has_available_vehicle(
        db, booking_date, start_time, end_time, transmission,
        exclude_booking_id=exclude_booking_id,
    )
