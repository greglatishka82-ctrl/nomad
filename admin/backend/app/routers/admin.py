import csv
import io
import os
import secrets
from datetime import datetime, date, time, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select, and_, func, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_db

KZ_TZ = timezone(timedelta(hours=6))


def today_kz() -> date:
    return datetime.now(KZ_TZ).date()
from app.models.models import (
    Admin, Instructor, Client, Booking, BookingStatus, ServiceType,
    TransmissionType, RatingRecord, RatingVote, Package, ClientPackage,
    Certificate, FAQItem, AuditLog, NotificationSent,
    MobileUser, MobileBooking, SupportMessage, AppReview
)
from app.services.auth import hash_password, verify_password

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _get_admin_username(request: Request) -> str:
    username = request.session.get("admin_username")
    if not username:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return username


async def _audit(db: AsyncSession, admin_username: str, action: str, details: str = ""):
    db.add(AuditLog(admin_username=admin_username, action=action, details=details))
    await db.commit()


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
    return {"ok": True}


@router.post("/logout")
async def logout(request: Request):
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
    await db.commit()
    await _audit(db, username, "change_password")
    return {"ok": True}


class InstructorCreate(BaseModel):
    name: str
    telegram_id: Optional[str] = None
    telegram_username: Optional[str] = None
    transmission: str = "both"
    experience_years: int = 0
    working_hours_start: str = "09:00"
    working_hours_end: str = "19:00"
    lunch_start: Optional[str] = None
    lunch_end: Optional[str] = None
    days_off: str = "Суббота,Воскресенье"
    description: Optional[str] = None


class InstructorUpdate(BaseModel):
    name: Optional[str] = None
    telegram_id: Optional[str] = None
    telegram_username: Optional[str] = None
    transmission: Optional[str] = None
    experience_years: Optional[int] = None
    rating: Optional[float] = None
    working_hours_start: Optional[str] = None
    working_hours_end: Optional[str] = None
    lunch_start: Optional[str] = None
    lunch_end: Optional[str] = None
    days_off: Optional[str] = None
    description: Optional[str] = None


@router.get("/instructors")
async def list_instructors(request: Request, db: AsyncSession = Depends(get_db)):
    _get_admin_username(request)
    result = await db.execute(select(Instructor).order_by(Instructor.name))
    instructors = result.scalars().all()
    return [
        {
            "id": i.id, "name": i.name, "telegram_id": i.telegram_id,
            "telegram_username": i.telegram_username, "transmission": i.transmission.value,
            "experience_years": i.experience_years, "rating": i.rating,
            "is_active": i.is_active,
            "working_hours_start": str(i.working_hours_start),
            "working_hours_end": str(i.working_hours_end),
            "lunch_start": str(i.lunch_start) if i.lunch_start else None,
            "lunch_end": str(i.lunch_end) if i.lunch_end else None,
            "days_off": i.days_off,
            "description": i.description,
            "avatar_url": i.avatar_url,
        }
        for i in instructors
    ]


@router.post("/instructors")
async def create_instructor(
    request: Request, body: InstructorCreate, db: AsyncSession = Depends(get_db)
):
    username = _get_admin_username(request)
    t_map = {"manual": TransmissionType.MANUAL, "automatic": TransmissionType.AUTOMATIC, "both": TransmissionType.BOTH}
    inst = Instructor(
        name=body.name,
        telegram_id=body.telegram_id,
        telegram_username=body.telegram_username,
        transmission=t_map.get(body.transmission, TransmissionType.BOTH),
        experience_years=body.experience_years,
        working_hours_start=time.fromisoformat(body.working_hours_start),
        working_hours_end=time.fromisoformat(body.working_hours_end),
        lunch_start=time.fromisoformat(body.lunch_start) if body.lunch_start else None,
        lunch_end=time.fromisoformat(body.lunch_end) if body.lunch_end else None,
        days_off=body.days_off,
        description=body.description,
    )
    db.add(inst)
    await db.commit()
    await db.refresh(inst)
    await _audit(db, username, "create_instructor", f"id={inst.id} name={inst.name}")
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
    t_map = {"manual": TransmissionType.MANUAL, "automatic": TransmissionType.AUTOMATIC, "both": TransmissionType.BOTH}
    if body.name is not None:
        inst.name = body.name
    if body.telegram_id is not None:
        inst.telegram_id = body.telegram_id
    if body.telegram_username is not None:
        inst.telegram_username = body.telegram_username
    if body.transmission is not None:
        inst.transmission = t_map.get(body.transmission, inst.transmission)
    if body.experience_years is not None:
        inst.experience_years = body.experience_years
    if body.rating is not None:
        inst.rating = max(settings.MIN_RATING, body.rating)
    if body.working_hours_start is not None:
        inst.working_hours_start = time.fromisoformat(body.working_hours_start)
    if body.working_hours_end is not None:
        inst.working_hours_end = time.fromisoformat(body.working_hours_end)
    if body.lunch_start is not None:
        inst.lunch_start = time.fromisoformat(body.lunch_start) if body.lunch_start else None
    if body.lunch_end is not None:
        inst.lunch_end = time.fromisoformat(body.lunch_end) if body.lunch_end else None
    if body.days_off is not None:
        inst.days_off = body.days_off
    if body.description is not None:
        inst.description = body.description
    await db.commit()
    await _audit(db, username, "update_instructor", f"id={instructor_id}")
    return {"ok": True}


@router.delete("/instructors/{instructor_id}")
async def delete_instructor(request: Request, instructor_id: int, db: AsyncSession = Depends(get_db)):
    username = _get_admin_username(request)
    result = await db.execute(select(Instructor).where(Instructor.id == instructor_id))
    inst = result.scalar_one_or_none()
    if not inst:
        raise HTTPException(status_code=404, detail="Instructor not found")
    name = inst.name
    await db.delete(inst)
    await db.commit()
    await _audit(db, username, "delete_instructor", f"id={instructor_id} name={name}")


UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "uploads", "avatars")


@router.post("/instructors/{instructor_id}/avatar")
async def upload_instructor_avatar(
    request: Request,
    instructor_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    _get_admin_username(request)
    result = await db.execute(select(Instructor).where(Instructor.id == instructor_id))
    inst = result.scalar_one_or_none()
    if not inst:
        raise HTTPException(status_code=404, detail="Instructor not found")

    # Проверяем формат
    allowed = {"image/jpeg", "image/png", "image/webp"}
    if file.content_type not in allowed:
        raise HTTPException(status_code=400, detail="Поддерживаются только JPG, PNG, WebP")

    # Проверяем размер (2 МБ)
    content = await file.read()
    if len(content) > 2 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Максимальный размер файла — 2 МБ")

    # Сохраняем файл
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    ext = file.filename.rsplit(".", 1)[-1] if "." in (file.filename or "") else "jpg"
    filename = f"instructor_{instructor_id}.{ext}"
    filepath = os.path.join(UPLOAD_DIR, filename)
    with open(filepath, "wb") as f:
        f.write(content)

    # Обновляем URL в базе
    inst.avatar_url = f"/uploads/avatars/{filename}"
    await db.commit()
    await _audit(request.session.get("admin_username", ""), "update_instructor", f"avatar id={instructor_id}")
    return {"ok": True, "avatar_url": inst.avatar_url}
    return {"ok": True}


@router.get("/bookings")
async def list_bookings(
    request: Request,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    instructor_id: Optional[int] = None,
    status: Optional[str] = None,
    location: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    _get_admin_username(request)
    query = select(Booking).options(selectinload(Booking.client), selectinload(Booking.instructor))
    conditions = []
    if date_from:
        conditions.append(Booking.booking_date >= date.fromisoformat(date_from))
    if date_to:
        conditions.append(Booking.booking_date <= date.fromisoformat(date_to))
    if instructor_id:
        conditions.append(Booking.instructor_id == instructor_id)
    if status:
        conditions.append(Booking.status == status)
    if location:
        conditions.append(Booking.location == location)
    if conditions:
        query = query.where(and_(*conditions))
    query = query.order_by(Booking.booking_date, Booking.start_time)
    result = await db.execute(query)
    bookings = result.scalars().all()

    # Also get mobile bookings
    mq = select(MobileBooking).options(selectinload(MobileBooking.instructor))
    mconditions = []
    if date_from:
        mconditions.append(MobileBooking.booking_date >= date.fromisoformat(date_from))
    if date_to:
        mconditions.append(MobileBooking.booking_date <= date.fromisoformat(date_to))
    if instructor_id:
        mconditions.append(MobileBooking.instructor_id == instructor_id)
    if status:
        mconditions.append(MobileBooking.status == status)
    if location:
        mconditions.append(MobileBooking.location == location)
    if mconditions:
        mq = mq.where(and_(*mconditions))
    mq = mq.order_by(MobileBooking.booking_date, MobileBooking.start_time)
    mresult = await db.execute(mq)
    mbookings = mresult.scalars().all()

    # Get mobile user names
    mobile_user_ids = list({b.user_id for b in mbookings})
    user_names = {}
    if mobile_user_ids:
        ures = await db.execute(select(MobileUser).where(MobileUser.id.in_(mobile_user_ids)))
        for u in ures.scalars().all():
            user_names[u.id] = u.name

    result_list = [
        {
            "id": b.id,
            "source": "telegram",
            "client_name": b.client.name if b.client else "",
            "client_phone": b.client.phone if b.client else "",
            "instructor_name": b.instructor.name if b.instructor else "",
            "service_type": b.service_type.value,
            "transmission": b.transmission.value,
            "location": b.location,
            "date": str(b.booking_date),
            "start_time": str(b.start_time),
            "end_time": str(b.end_time),
            "status": b.status.value,
            "price": b.price,
        }
        for b in bookings
    ] + [
        {
            "id": b.id,
            "source": "mobile",
            "client_name": user_names.get(b.user_id, ""),
            "client_phone": "",
            "instructor_name": b.instructor.name if b.instructor else "",
            "service_type": b.service_type.value,
            "transmission": b.transmission.value,
            "location": b.location,
            "date": str(b.booking_date),
            "start_time": str(b.start_time),
            "end_time": str(b.end_time),
            "status": b.status.value,
            "price": b.price,
        }
        for b in mbookings
    ]
    result_list.sort(key=lambda x: (x["date"], x["start_time"]))
    return result_list


@router.delete("/bookings/{booking_id}")
async def delete_booking(request: Request, booking_id: int, db: AsyncSession = Depends(get_db)):
    username = _get_admin_username(request)
    result = await db.execute(select(Booking).where(Booking.id == booking_id))
    booking = result.scalar_one_or_none()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    from app.models.models import RatingRecord
    await db.execute(
        RatingRecord.__table__.delete().where(RatingRecord.booking_id == booking_id)
    )
    await db.delete(booking)
    await db.commit()
    await _audit(db, username, "delete_booking", f"id={booking_id}")
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
    await db.commit()
    await _audit(db, username, "reassign_booking", f"id={booking_id}")
    return {"ok": True}


@router.get("/dashboard")
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    _get_admin_username(request)
    today = today_kz()
    week_ago = today - timedelta(days=7)
    month_ago = today - timedelta(days=30)

    revenue_today = await db.execute(
        select(func.coalesce(func.sum(Booking.price), 0)).where(
            and_(Booking.booking_date == today, Booking.status == BookingStatus.COMPLETED)
        )
    )
    revenue_week = await db.execute(
        select(func.coalesce(func.sum(Booking.price), 0)).where(
            and_(Booking.booking_date >= week_ago, Booking.status == BookingStatus.COMPLETED)
        )
    )
    revenue_month = await db.execute(
        select(func.coalesce(func.sum(Booking.price), 0)).where(
            and_(Booking.booking_date >= month_ago, Booking.status == BookingStatus.COMPLETED)
        )
    )

    # Telegram bookings
    total_bookings = await db.execute(select(func.count()).select_from(Booking))
    cancelled = await db.execute(
        select(func.count()).select_from(Booking).where(Booking.status == BookingStatus.CANCELLED)
    )
    no_shows = await db.execute(
        select(func.count()).select_from(Booking).where(Booking.status == BookingStatus.NO_SHOW)
    )

    # Mobile bookings
    mobile_total = await db.execute(select(func.count()).select_from(MobileBooking))
    mobile_cancelled = await db.execute(
        select(func.count()).select_from(MobileBooking).where(MobileBooking.status == BookingStatus.CANCELLED)
    )

    # Revenue from mobile
    mobile_revenue = await db.execute(
        select(func.coalesce(func.sum(MobileBooking.price), 0)).where(
            MobileBooking.status == BookingStatus.COMPLETED
        )
    )
    revenue_today_val = revenue_today.scalar() or 0
    mobile_rev = mobile_revenue.scalar() or 0

    clients_count = await db.execute(select(func.count()).select_from(Client))
    mobile_users_count = await db.execute(select(func.count()).select_from(MobileUser))
    instructors_count = await db.execute(
        select(func.count()).select_from(Instructor).where(Instructor.is_active == True)
    )

    return {
        "revenue_today": revenue_today_val,
        "revenue_week": (revenue_week.scalar() or 0) + mobile_rev,
        "revenue_month": (revenue_month.scalar() or 0) + mobile_rev,
        "total_bookings": (total_bookings.scalar() or 0) + (mobile_total.scalar() or 0),
        "cancelled": (cancelled.scalar() or 0) + (mobile_cancelled.scalar() or 0),
        "no_shows": no_shows.scalar() or 0,
        "clients_count": (clients_count.scalar() or 0) + (mobile_users_count.scalar() or 0),
        "instructors_count": instructors_count.scalar() or 0,
    }


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
    item = FAQItem(question=body.question, answer=body.answer, sort_order=body.sort_order)
    db.add(item)
    await db.commit()
    await _audit(db, username, "create_faq")
    return {"ok": True}


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
    item.is_active = False
    await db.commit()
    await _audit(db, username, "delete_faq", f"id={faq_id}")
    return {"ok": True}


class PackageCreate(BaseModel):
    name: str
    sessions_count: int
    price: int


@router.get("/packages")
async def packages_list(request: Request, db: AsyncSession = Depends(get_db)):
    _get_admin_username(request)
    result = await db.execute(select(Package).where(Package.is_active == True))
    packages = result.scalars().all()
    return [{"id": p.id, "name": p.name, "sessions_count": p.sessions_count, "price": p.price} for p in packages]


@router.post("/packages")
async def package_create(request: Request, body: PackageCreate, db: AsyncSession = Depends(get_db)):
    username = _get_admin_username(request)
    pkg = Package(name=body.name, sessions_count=body.sessions_count, price=body.price)
    db.add(pkg)
    await db.commit()
    await _audit(db, username, "create_package", f"name={body.name}")
    return {"ok": True}


@router.delete("/packages/{package_id}")
async def package_delete(request: Request, package_id: int, db: AsyncSession = Depends(get_db)):
    username = _get_admin_username(request)
    result = await db.execute(select(Package).where(Package.id == package_id))
    pkg = result.scalar_one_or_none()
    if not pkg:
        raise HTTPException(status_code=404, detail="Package not found")
    await db.delete(pkg)
    await db.commit()
    await _audit(db, username, "delete_package", f"id={package_id} name={pkg.name}")
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
        if c.activated_by_client_id:
            client_result = await db.execute(select(Client.name).where(Client.id == c.activated_by_client_id))
            row = client_result.first()
            client_name = row[0] if row else None
        output.append({
            "id": c.id, "code": c.code, "nominal": c.nominal,
            "remaining": c.remaining, "is_used": c.is_used,
            "client_name": client_name,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        })
    return output


@router.post("/certificates")
async def certificate_create(request: Request, body: CertificateCreate, db: AsyncSession = Depends(get_db)):
    username = _get_admin_username(request)
    code = secrets.token_hex(8).upper()
    cert = Certificate(code=code, nominal=body.nominal, remaining=body.nominal)
    db.add(cert)
    await db.commit()
    await _audit(db, username, "create_certificate", f"code={code}")
    return {"code": code, "nominal": body.nominal}


@router.delete("/certificates/{cert_id}")
async def certificate_delete(request: Request, cert_id: int, db: AsyncSession = Depends(get_db)):
    username = _get_admin_username(request)
    result = await db.execute(select(Certificate).where(Certificate.id == cert_id))
    cert = result.scalar_one_or_none()
    if not cert:
        raise HTTPException(status_code=404, detail="Certificate not found")
    await db.delete(cert)
    await db.commit()
    await _audit(db, username, "delete_certificate", f"id={cert_id} code={cert.code}")
    return {"ok": True}


@router.get("/audit-logs")
async def audit_logs(request: Request, limit: int = 200, db: AsyncSession = Depends(get_db)):
    _get_admin_username(request)
    result = await db.execute(
        select(AuditLog).where(AuditLog.action.notin_(BOT_ACTIONS)).order_by(AuditLog.created_at.desc()).limit(limit)
    )
    logs = result.scalars().all()
    return [
        {"id": l.id, "admin_username": l.admin_username, "action": l.action, "details": l.details, "created_at": str(l.created_at)}
        for l in logs
    ]


@router.get("/analytics/heatmap")
async def heatmap(request: Request, db: AsyncSession = Depends(get_db)):
    _get_admin_username(request)
    result = await db.execute(
        select(Booking.booking_date, Booking.start_time, func.count()).where(
            Booking.status.in_([BookingStatus.CONFIRMED, BookingStatus.COMPLETED])
        ).group_by(Booking.booking_date, Booking.start_time).order_by(Booking.booking_date)
    )
    rows = result.all()
    data = []
    for row in rows:
        d, t, count = row
        day_name = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"][d.weekday()] if isinstance(d, date) else str(d)
        data.append({"date": str(d), "day_name": day_name, "hour": t.hour if isinstance(t, time) else 0, "count": count})
    return data


@router.get("/analytics/instructor-load")
async def instructor_load(request: Request, days: int = 30, db: AsyncSession = Depends(get_db)):
    _get_admin_username(request)
    since = date.today() - timedelta(days=days)
    result = await db.execute(
        select(Instructor.name, func.count(Booking.id)).join(Booking).where(
            and_(Booking.booking_date >= since, Booking.status.in_([BookingStatus.CONFIRMED, BookingStatus.COMPLETED]))
        ).group_by(Instructor.id, Instructor.name)
    )
    rows = result.all()
    return [{"name": r[0], "bookings": r[1]} for r in rows]


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
            b.service_type.value, b.transmission.value, b.location, b.status.value, b.price,
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
    result = await db.execute(select(Client).order_by(Client.created_at.desc()))
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


BOT_ACTIONS = [
    "new_client", "new_booking", "booking_confirmed", "booking_cancelled",
    "no_show", "rating_given", "rating_request_sent", "confirmation_sent",
    "client_arrived", "lesson_completed",
]


@router.get("/notifications")
async def get_notifications(request: Request, db: AsyncSession = Depends(get_db)):
    _get_admin_username(request)
    notifications = []

    # Low rating alerts
    low_rating_result = await db.execute(
        select(NotificationSent).options(selectinload(NotificationSent.instructor_rel)).where(
            NotificationSent.notification_type == "low_rating"
        ).order_by(NotificationSent.sent_at.desc()).limit(20)
    )
    for n in low_rating_result.scalars().all():
        inst_name = n.instructor_rel.name if n.instructor_rel else f"ID {n.instructor_id}"
        notifications.append({
            "type": "low_rating",
            "message": f"Инструктор {inst_name} имеет низкий рейтинг. Люди недовольны",
            "created_at": str(n.sent_at),
        })

    # Bot actions from audit log
    audit_result = await db.execute(
        select(AuditLog).where(AuditLog.action.in_(BOT_ACTIONS)).order_by(AuditLog.created_at.desc()).limit(50)
    )
    for a in audit_result.scalars().all():
        notifications.append({
            "type": a.action,
            "message": a.details or a.action,
            "created_at": str(a.created_at),
        })

    # Mobile user registrations
    mobile_users_result = await db.execute(
        select(MobileUser).order_by(MobileUser.created_at.desc()).limit(20)
    )
    for u in mobile_users_result.scalars().all():
        notifications.append({
            "type": "mobile_registration",
            "message": f"Новая регистрация: {u.name} ({u.email})",
            "created_at": str(u.created_at),
        })

    # Mobile bookings
    mobile_bookings_result = await db.execute(
        select(MobileBooking).options(
            selectinload(MobileBooking.instructor)
        ).order_by(MobileBooking.created_at.desc()).limit(20)
    )
    for b in mobile_bookings_result.scalars().all():
        inst_name = b.instructor.name if b.instructor else ""
        status_map = {
            "planned": "создана",
            "confirmed": "подтверждена",
            "in_progress": "в процессе",
            "completed": "завершена",
            "cancelled": "отменена",
            "no_show": "неявка",
        }
        status_text = status_map.get(b.status.value, b.status.value)
        notifications.append({
            "type": f"mobile_booking_{b.status.value}",
            "message": f"Запись #{b.id} {status_text}: {b.booking_date} {b.start_time}, инструктор {inst_name}",
            "created_at": str(b.created_at),
        })

    # Support messages from mobile users
    support_result = await db.execute(
        select(SupportMessage).order_by(SupportMessage.created_at.desc()).limit(20)
    )
    for s in support_result.scalars().all():
        notifications.append({
            "type": "support_message",
            "message": f"Сообщение в поддержку: {s.text[:80]}",
            "created_at": str(s.created_at),
        })

    # App reviews from mobile users
    reviews_result = await db.execute(
        select(AppReview).order_by(AppReview.created_at.desc()).limit(30)
    )
    stars_label = {1: '★☆☆☆☆', 2: '★★☆☆☆', 3: '★★★☆☆', 4: '★★★★☆', 5: '★★★★★'}
    for r in reviews_result.scalars().all():
        user = await db.get(MobileUser, r.user_id) if r.user_id else None
        user_name = user.name if user else 'Аноним'
        notifications.append({
            "type": "app_review",
            "message": f"Оценка приложения: {stars_label.get(r.stars, r.stars)} от {user_name}",
            "created_at": str(r.created_at),
        })

    notifications.sort(key=lambda x: x["created_at"], reverse=True)
    return notifications[:50]
