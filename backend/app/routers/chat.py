from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.ai_service import chat_completion
from app.models.models import FAQItem, Instructor

router = APIRouter(prefix="/api", tags=["chat"])


class ChatRequest(BaseModel):
    message: str
    history: list = []


class ChatResponse(BaseModel):
    reply: str


@router.post("/chat/", response_model=ChatResponse)
async def chat(request: ChatRequest, db: AsyncSession = Depends(get_db)):
    reply = await chat_completion(request.message, request.history)
    return ChatResponse(reply=reply)


@router.get("/faq")
async def public_faq(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(FAQItem).where(FAQItem.is_active == True).order_by(FAQItem.sort_order)
    )
    items = result.scalars().all()
    return [{"id": f.id, "question": f.question, "answer": f.answer} for f in items]


@router.get("/instructors")
async def public_instructors(db: AsyncSession = Depends(get_db)):
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
