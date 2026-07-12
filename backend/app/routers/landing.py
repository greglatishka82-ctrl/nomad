from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.models import Package, Certificate, Instructor, FAQItem

router = APIRouter(prefix="/api")


@router.get("/instructors")
async def public_instructors(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Instructor).where(Instructor.is_active == True).order_by(Instructor.rating.desc())
    )
    instructors = result.scalars().all()
    trans_labels = {"manual": "Механика", "automatic": "Автомат", "both": "Механика и автомат"}
    return [
        {
            "id": i.id,
            "name": i.name,
            "transmission": trans_labels.get(i.transmission.value, ""),
            "experience_years": i.experience_years,
            "description": i.description or "",
            "rating": i.rating,
            "avatar_url": i.avatar_url,
        }
        for i in instructors
    ]


@router.get("/faq")
async def public_faq(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(FAQItem).where(FAQItem.is_active == True).order_by(FAQItem.sort_order)
    )
    items = result.scalars().all()
    return [{"id": f.id, "question": f.question, "answer": f.answer} for f in items]


@router.get("/packages")
async def public_packages(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Package).order_by(Package.price))
    packages = result.scalars().all()
    return [{"id": p.id, "name": p.name, "sessions_count": p.sessions_count, "price": p.price} for p in packages]


@router.get("/certificates")
async def public_certificates(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Certificate).order_by(Certificate.amount))
    certificates = result.scalars().all()
    return [{"id": c.id, "amount": c.amount} for c in certificates]


class OrderRequest(BaseModel):
    item_type: str
    item_id: int | None = None
    item_name: str
    amount: int
    name: str
    phone: str
    email: str
    payment_method: str


PAYMENT_INSTRUCTIONS = {
    "kaspi": "Перевод на Kaspi Gold: +7 702 718 22 33 (Укажите имя в комментарии)",
    "halyk": "Перевод на карту Халык: +7 702 718 22 33 (Укажите имя в комментарии)",
    "forte": "Перевод на карту Форте: +7 702 718 22 33 (Укажите имя в комментарии)",
    "jysan": "Перевод на карту Жусан: +7 702 718 22 33 (Укажите имя в комментарии)",
    "bereke": "Перевод на карту Береке: +7 702 718 22 33 (Укажите имя в комментарии)",
    "sber": "Перевод на Сбер: +7 702 718 22 33 (Укажите имя в комментарии)",
    "crypto": "Криптовалюта: Свяжитесь с нами для получения реквизитов. Telegram: @drivepvlbot",
    "cash": "Оплата наличными при посещении автошколы: г. Павлодар, ул. Назарбекова 53, кабинет 309",
}


@router.post("/orders")
async def create_order(order: OrderRequest, db: AsyncSession = Depends(get_db)):
    if order.item_type not in ("package", "certificate", "custom_certificate"):
        raise HTTPException(status_code=400, detail="Неизвестный тип товара")
    if not order.name or not order.phone or not order.email:
        raise HTTPException(status_code=400, detail="Заполните все поля")
    if order.payment_method not in PAYMENT_INSTRUCTIONS:
        raise HTTPException(status_code=400, detail="Неизвестный способ оплаты")

    code = f"NOMAD-{order.item_id or 'CERT'}-{order.amount}-{order.name[:3].upper()}"

    return {
        "success": True,
        "message": f"Ваша заявка на «{order.item_name}» на сумму {order.amount:,} ₸ принята! Ожидайте подтверждения на почту.",
        "code": code,
        "payment_details": PAYMENT_INSTRUCTIONS.get(order.payment_method, ""),
        "payment_method": order.payment_method,
    }
