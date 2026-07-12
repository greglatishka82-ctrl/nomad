"""
Пакеты, сертификаты, рефералы мобильного приложения.
GET  /api/mobile/config
GET  /api/mobile/packages
GET  /api/mobile/my-packages
POST /api/mobile/packages/{id}/request
POST /api/mobile/certificates/activate
GET  /api/mobile/certificates
GET  /api/mobile/referral
GET  /api/mobile/instructors
GET  /api/mobile/faq
"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.models import (
    Package, Certificate, MobileUser, Instructor, FAQItem,
    MobileUserPackage, MobileUserCertificate, MobileReferralRecord,
    AppReview,
)
from app.services.mobile_auth import get_current_user_id

router = APIRouter(prefix="/api/mobile", tags=["mobile-extras"])


# ── Конфигурация приложения ──────────────────────────────────────────────────

@router.get("/config")
async def app_config():
    """Публичная конфигурация приложения: цены, адреса, контакты."""
    return {
        "price_training": settings.PRICE_TRAINING,
        "price_exam": settings.PRICE_EXAM,
        "training_duration_minutes": settings.TRAINING_DURATION_MINUTES,
        "exam_duration_minutes": settings.EXAM_DURATION_MINUTES,
        "location_main": settings.LOCATION_MAIN,
        "location_exam": settings.LOCATION_EXAM,
        "working_hours_start": settings.WORKING_HOURS_START,
        "working_hours_end": settings.WORKING_HOURS_END,
        "phone": "+7 702 718 22 33",
        "payment_method": "Наличными на месте",
        "car_model_manual": "Chevrolet Cobalt с ручной коробкой",
        "car_model_automatic": "Chevrolet Cobalt с автоматической коробкой",
    }


# ── Пакеты ────────────────────────────────────────────────────────────────────

@router.get("/packages")
async def list_packages(db: AsyncSession = Depends(get_db)):
    """Публичный список пакетов (без авторизации)."""
    result = await db.execute(
        select(Package).where(Package.is_active == True).order_by(Package.price)
    )
    packages = result.scalars().all()
    return [
        {
            "id": p.id,
            "name": p.name,
            "sessions_count": p.sessions_count,
            "price": p.price,
        }
        for p in packages
    ]


@router.get("/my-packages")
async def my_packages(
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(MobileUserPackage).where(MobileUserPackage.user_id == user_id)
    )
    items = result.scalars().all()
    out = []
    for item in items:
        pkg = await db.get(Package, item.package_id)
        out.append({
            "id": item.id,
            "package_id": item.package_id,
            "name": pkg.name if pkg else "",
            "sessions_count": pkg.sessions_count if pkg else 0,
            "remaining_sessions": item.remaining_sessions,
            "is_active": item.is_active,
            "purchased_at": item.purchased_at.isoformat(),
            "activated_at": item.activated_at.isoformat() if item.activated_at else None,
        })
    return out


class PackageRequestBody(BaseModel):
    """Клиент «заявляет» о намерении купить пакет — администратор активирует вручную."""
    pass


@router.post("/packages/{package_id}/request", status_code=status.HTTP_201_CREATED)
async def request_package(
    package_id: int,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    pkg = await db.get(Package, package_id)
    if not pkg or not pkg.is_active:
        raise HTTPException(status_code=404, detail="Пакет не найден")

    # Проверяем — нет ли уже ожидающей заявки на этот пакет
    existing = await db.execute(
        select(MobileUserPackage).where(
            MobileUserPackage.user_id == user_id,
            MobileUserPackage.package_id == package_id,
            MobileUserPackage.is_active == False,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Заявка на этот пакет уже ожидает активации")

    item = MobileUserPackage(
        user_id=user_id,
        package_id=package_id,
        remaining_sessions=pkg.sessions_count,
        is_active=False,  # активирует администратор вручную в админке
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return {
        "id": item.id,
        "message": "Заявка принята. Администратор активирует пакет после оплаты.",
        "package_name": pkg.name,
        "price": pkg.price,
    }


# ── Сертификаты ───────────────────────────────────────────────────────────────

class ActivateCertRequest(BaseModel):
    code: str


@router.post("/certificates/activate")
async def activate_certificate(
    body: ActivateCertRequest,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    code = body.code.strip().upper()
    cert_result = await db.execute(
        select(Certificate).where(Certificate.code == code)
    )
    cert = cert_result.scalar_one_or_none()
    if not cert:
        raise HTTPException(status_code=404, detail="Сертификат не найден")
    if cert.remaining <= 0:
        raise HTTPException(status_code=400, detail="Баланс сертификата исчерпан")

    # Проверяем — не активировал ли уже кто-то другой
    if cert.activated_by_client_id and cert.activated_by_client_id != user_id:
        raise HTTPException(status_code=409, detail="Этот сертификат уже активирован другим пользователем")

    # Проверяем — не активировал ли уже этот пользователь
    existing = await db.execute(
        select(MobileUserCertificate).where(
            MobileUserCertificate.user_id == user_id,
            MobileUserCertificate.certificate_id == cert.id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Вы уже активировали этот сертификат")

    cert.activated_by_client_id = user_id
    link = MobileUserCertificate(user_id=user_id, certificate_id=cert.id)
    db.add(link)
    await db.commit()
    return {
        "message": "Сертификат активирован",
        "code": cert.code,
        "nominal": cert.nominal,
        "remaining": cert.remaining,
    }


@router.get("/certificates")
async def my_certificates(
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(MobileUserCertificate).where(MobileUserCertificate.user_id == user_id)
    )
    items = result.scalars().all()
    out = []
    for item in items:
        cert = await db.get(Certificate, item.certificate_id)
        out.append({
            "id": item.id,
            "certificate_id": item.certificate_id,
            "code": cert.code if cert else "",
            "nominal": cert.nominal if cert else 0,
            "remaining": cert.remaining if cert else 0,
            "is_spent": (cert.remaining == 0) if cert else True,
            "activated_at": item.activated_at.isoformat(),
        })
    return out


# ── Рефералы ──────────────────────────────────────────────────────────────────

@router.get("/referral")
async def referral_info(
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    user = await db.get(MobileUser, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    # Список приглашённых
    result = await db.execute(
        select(MobileReferralRecord).where(MobileReferralRecord.referrer_id == user_id)
    )
    records = result.scalars().all()

    referred_list = []
    for r in records:
        referred_user = await db.get(MobileUser, r.referred_id)
        if referred_user:
            referred_list.append({
                "id": referred_user.id,
                "name": referred_user.name,
                "joined_at": referred_user.created_at.isoformat(),
                "discount_applied": r.discount_applied,
            })

    return {
        "referral_code": user.referral_code,
        "referral_link": f"https://nomadpvl.kz/app?ref={user.referral_code}",
        "referred_count": len(referred_list),
        "referred_users": referred_list,
    }


# ── Инструкторы (мобильный) ─────────────────────────────────────────────────

@router.get("/instructors")
async def mobile_instructors(db: AsyncSession = Depends(get_db)):
    """Список инструкторов для мобильного приложения (с рейтингом и аватаром)."""
    result = await db.execute(
        select(Instructor).where(Instructor.is_active == True).order_by(Instructor.id)
    )
    instructors = result.scalars().all()
    trans_labels = {"manual": "Механика", "automatic": "Автомат", "both": "Механика и автомат"}
    return [
        {
            "id": i.id,
            "name": i.name,
            "transmission": trans_labels.get(i.transmission.value, "Обе"),
            "experience_years": i.experience_years,
            "description": i.description or "",
            "rating": i.rating,
            "avatar_url": i.avatar_url,
        }
        for i in instructors
    ]


# ── FAQ (мобильный) ─────────────────────────────────────────────────────────

@router.get("/faq")
async def mobile_faq(db: AsyncSession = Depends(get_db)):
    """Список FAQ для мобильного приложения."""
    result = await db.execute(
        select(FAQItem).where(FAQItem.is_active == True).order_by(FAQItem.sort_order)
    )
    items = result.scalars().all()
    return [{"id": f.id, "question": f.question, "answer": f.answer} for f in items]


# ── Оценка приложения ─────────────────────────────────────────────────────────

class AppReviewRequest(BaseModel):
    stars: int  # 1..5

    def validate_stars(self) -> "AppReviewRequest":
        if not (1 <= self.stars <= 5):
            raise ValueError("Оценка должна быть от 1 до 5")
        return self


@router.post("/app-review", status_code=status.HTTP_201_CREATED)
async def submit_app_review(
    body: AppReviewRequest,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    if not (1 <= body.stars <= 5):
        raise HTTPException(status_code=400, detail="Оценка должна быть от 1 до 5")

    review = AppReview(user_id=user_id, stars=body.stars)
    db.add(review)
    await db.commit()
    await db.refresh(review)
    return {"ok": True, "id": review.id}
