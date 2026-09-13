"""
Мобильное API - FAQ, пакеты, сертификаты, конфиг, реферал, отзывы
GET  /api/mobile/faq
GET  /api/mobile/packages
GET  /api/mobile/my-packages
POST /api/mobile/packages/{id}/request
GET  /api/mobile/certificates
POST /api/mobile/certificates/activate
GET  /api/mobile/config
GET  /api/mobile/referral
POST /api/mobile/app-review
"""
import uuid
from datetime import datetime

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.models import (
    FAQ, Package, Certificate, CertificateRequest, Client,
    ClientPackage, MobileAppReview, Event,
)
from app.routers.mobile_auth import get_current_user

router = APIRouter(prefix="/api/mobile", tags=["mobile-extras"])


@router.get("/config")
async def get_config():
    return {
        "price_training": settings.PRICE_TRAINING,
        "price_training_new": settings.PRICE_TRAINING_NEW,
        "price_exam": settings.PRICE_EXAM,
        "training_duration_minutes": settings.TRAINING_DURATION_MINUTES,
        "exam_duration_minutes": settings.EXAM_DURATION_MINUTES,
        "location_main": settings.LOCATION_MAIN,
        "location_exam": settings.LOCATION_EXAM,
        "working_hours_start": settings.WORKING_HOURS_START,
        "working_hours_end": settings.WORKING_HOURS_END,
        "phone": "+7 702 718 22 33",
        "payment_method": "Наличными или через Kaspi QR",
        "car_model_manual": "Chevrolet Cobalt",
        "car_model_automatic": "Chevrolet Cobalt",
    }


@router.get("/faq")
async def get_faq(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(FAQ).where(FAQ.is_active == True).order_by(FAQ.sort_order)
    )
    faqs = result.scalars().all()
    return [{"id": f.id, "question": f.question, "answer": f.answer} for f in faqs]


@router.get("/packages")
async def get_packages(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Package).where(Package.is_active == True).order_by(Package.sessions_count))
    packages = result.scalars().all()
    return [
        {
            "id": p.id,
            "name": p.name,
            "sessions_count": p.sessions_count,
            "price": p.price,
            "description": p.description,
        }
        for p in packages
    ]


@router.get("/my-packages")
async def get_my_packages(
    user: Client = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ClientPackage)
        .where(ClientPackage.client_id == user.id)
        .order_by(ClientPackage.purchased_at.desc())
    )
    user_packages = result.scalars().all()

    items = []
    for up in user_packages:
        if up.expires_at and up.expires_at < datetime.utcnow():
            up.is_active = False
        package = await db.get(Package, up.package_id)
        items.append({
            "id": up.id,
            "package_id": up.package_id,
            "name": package.name if package else "Пакет",
            "sessions_count": package.sessions_count if package else 0,
            "remaining_sessions": up.remaining_sessions,
            "is_active": up.is_active,
            "purchased_at": up.purchased_at.isoformat(),
            "expires_at": up.expires_at.isoformat() if up.expires_at else None,
            "bonus_exam": package.bonus_exam if package else False,
            "code": package.code if package else None,
            "remaining_bonus_exams": up.remaining_bonus_exams,
        })
    await db.commit()
    return items


class PackageRequestRequest(BaseModel):
    name: str = ""
    phone: str = ""


@router.post("/packages/{package_id}/request", status_code=201)
async def request_package(
    package_id: int,
    body: Optional[PackageRequestRequest] = None,
    user: Client = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    package = await db.get(Package, package_id)
    if not package or not package.is_active:
        raise HTTPException(status_code=404, detail="Package not found")

    db.add(Event(
        event_type="package_requested",
        source="mobile",
        client_id=user.id,
        message=f"Клиент «{user.name}» оставил заявку на пакет «{package.name}» ({package.code}).",
    ))
    await db.commit()
    return {
        "id": package.id,
        "message": f"Заявка на пакет '{package.name}' создана. Оплатить можно наличными или через Kaspi QR."
    }


@router.get("/certificates")
async def get_my_certificates(
    user: Client = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Certificate).where(Certificate.used_by_user_id == user.id)
    )
    certs = result.scalars().all()

    return [
        {
            "id": c.id,
            "code": c.code,
            "nominal": c.nominal,
            "remaining": c.remaining,
            "is_spent": c.is_used or c.remaining <= 0,
            "activated_at": c.used_at.isoformat() if c.used_at else "",
        }
        for c in certs
    ]


class ActivateCertificateRequest(BaseModel):
    code: str


@router.post("/certificates/activate")
async def activate_certificate(
    body: ActivateCertificateRequest,
    user: Client = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Certificate).where(Certificate.code == body.code))
    cert = result.scalar_one_or_none()

    if cert and cert.used_by_user_id:
        raise HTTPException(status_code=400, detail="Сертификат уже использован")
    existing = (await db.execute(select(CertificateRequest).where(
        CertificateRequest.client_id == user.id,
        CertificateRequest.code_entered == body.code,
        CertificateRequest.status == "pending",
    ))).scalar_one_or_none()
    if existing:
        return {"message": "Ваш код уже принят и находится в обработке.", "pending": True}
    db.add(CertificateRequest(
        client_id=user.id,
        code_entered=body.code,
        matched_certificate_id=cert.id if cert else None,
        status="pending",
    ))
    db.add(Event(
        event_type="certificate_activation_requested",
        source="mobile",
        client_id=user.id,
        message=f"Клиент {user.name} подал заявку на подтверждение сертификата. Код: {body.code}",
    ))
    await db.commit()
    return {"message": "Ваш код принят и находится в обработке.", "pending": True}


@router.get("/referral")
async def get_referral(
    user: Client = Depends(get_current_user),
):
    code = user.referral_code
    if not code:
        code = f"NOMAD-{user.id:04d}"

    link = f"https://nomadrive.vercel.app/?ref={code}"

    return {
        "referral_code": code,
        "referral_link": link,
        "referred_count": 0,
        "referred_users": [],
    }


class AppReviewRequest(BaseModel):
    stars: int


@router.post("/app-review", status_code=201)
async def submit_app_review(
    body: AppReviewRequest,
    user: Client = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if body.stars < 1 or body.stars > 5:
        raise HTTPException(status_code=400, detail="Stars must be 1-5")

    review = MobileAppReview(
        client_id=user.id,
        stars=body.stars,
    )
    db.add(review)
    db.add(Event(
        event_type="app_rating_given",
        source="mobile",
        client_id=user.id,
        message=f"Клиент «{user.name}» оценил приложение на {body.stars} из 5.",
    ))
    await db.commit()

    return {"message": "Спасибо за оценку!"}
