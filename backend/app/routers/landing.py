from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.models import Instructor, FAQItem

router = APIRouter(prefix="/api")


@router.get("/stats")
async def get_stats(db: AsyncSession = Depends(get_db)):
    """Возвращает статистику для сайта: количество активных инструкторов и площадок"""
    result = await db.execute(
        select(func.count()).select_from(Instructor).where(Instructor.is_active == True)
    )
    instructors_count = result.scalar() or 0
    # Количество площадок (фиксированное значение - 2 площадки)
    locations_count = 2
    return {
        "instructors_count": instructors_count,
        "locations_count": locations_count
    }


@router.get("/instructors")
async def get_instructors(db: AsyncSession = Depends(get_db)):
    """Возвращает список активных инструкторов для лендинга"""
    result = await db.execute(
        select(Instructor).where(Instructor.is_active == True).order_by(Instructor.name)
    )
    instructors = result.scalars().all()
    
    # Перевод transmission на русский
    transmission_labels = {
        "manual": "Механика",
        "automatic": "Автомат",
        "both": "Механика и автомат"
    }
    
    return [
        {
            "id": i.id,
            "name": i.name,
            "transmission": transmission_labels.get(i.transmission, i.transmission),
            "experience_years": i.experience_years,
            "rating": i.rating,
            "description": i.description or "",
        }
        for i in instructors
    ]


@router.get("/faq")
async def get_faq(db: AsyncSession = Depends(get_db)):
    """Возвращает список активных FAQ для лендинга"""
    result = await db.execute(
        select(FAQItem).where(FAQItem.is_active == True).order_by(FAQItem.sort_order)
    )
    faq_items = result.scalars().all()
    return [
        {
            "id": f.id,
            "question": f.question,
            "answer": f.answer,
        }
        for f in faq_items
    ]
