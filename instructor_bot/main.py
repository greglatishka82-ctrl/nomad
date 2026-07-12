import asyncio
import logging
import sys
import os
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import FastAPI
import uvicorn
from aiogram import Bot, Dispatcher, F, Router, types
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from sqlalchemy import and_, select
from sqlalchemy.orm import selectinload

from app.database import async_session, engine
from app.models.models import (
    Booking, BookingStatus, Instructor, Client, ServiceType, TransmissionType, Base, Certificate
)
from app.config import Settings

logger = logging.getLogger(__name__)
settings = Settings()
bot = Bot(token=settings.INSTRUCTOR_BOT_TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)

INSTRUCTOR_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="📅 Мои записи на сегодня")]],
    resize_keyboard=True,
)


async def _get_instructor(message) -> Optional[Instructor]:
    async with async_session() as db:
        result = await db.execute(
            select(Instructor).where(Instructor.telegram_username == message.from_user.username)
        )
        return result.scalar_one_or_none()


async def _get_bookings(instructor_id: int):
    async with async_session() as db:
        now = datetime.now()
        today = date.today()
        result = await db.execute(
            select(Booking).options(
                selectinload(Booking.client),
                selectinload(Booking.instructor),
                selectinload(Booking.certificate),
            ).where(
                and_(
                    Booking.instructor_id == instructor_id,
                    Booking.booking_date >= today,
                    Booking.status.in_([
                        BookingStatus.CONFIRMED,
                        BookingStatus.PLANNED,
                        BookingStatus.IN_PROGRESS,
                    ]),
                )
            ).order_by(Booking.booking_date, Booking.start_time)
        )
        bookings = list(result.scalars().all())

        updated = False
        visible = []
        for b in bookings:
            lesson_end = datetime.combine(b.booking_date, b.end_time)
            cutoff = lesson_end + timedelta(hours=1)

            if b.status in (BookingStatus.PLANNED, BookingStatus.CONFIRMED) and now > cutoff:
                b.status = BookingStatus.NO_SHOW
                updated = True
                continue

            visible.append(b)

        if updated:
            await db.commit()

        return visible


async def _show_bookings(message, instructor):
    bookings = await _get_bookings(instructor.id)
    if not bookings:
        await message.answer("Нет активных записей.", reply_markup=INSTRUCTOR_KEYBOARD)
        return

    for b in bookings:
        service = "🚗 Обучение" if b.service_type == ServiceType.TRAINING else "🏁 Экзамен"
        trans = "Механика" if b.transmission == TransmissionType.MANUAL else "Автомат"
        status_labels = {
            BookingStatus.PLANNED: "📋 Запланирована",
            BookingStatus.CONFIRMED: "✅ Подтверждена",
            BookingStatus.IN_PROGRESS: "🔄 В процессе",
        }
        status_text = status_labels.get(b.status, b.status.value)

        cert_paid = b.certificate_id is not None

        kb = InlineKeyboardMarkup(inline_keyboard=[])
        if b.status in (BookingStatus.PLANNED, BookingStatus.CONFIRMED):
            kb.inline_keyboard.append([
                InlineKeyboardButton(text="✅ Клиент пришёл", callback_data=f"arrived:{b.id}")
            ])
        elif b.status == BookingStatus.IN_PROGRESS:
            # Если оплачено сертификатом — не показываем кнопку оплаты
            if not cert_paid:
                kb.inline_keyboard.append([
                    InlineKeyboardButton(text="💰 Занятие окончено (оплата получена)", callback_data=f"done:{b.id}")
                ])
            else:
                kb.inline_keyboard.append([
                    InlineKeyboardButton(text="✅ Занятие окончено", callback_data=f"done:{b.id}")
                ])

        cert_line = ""
        if cert_paid:
            cert_line = "\n🎟️ УРОК ОПЛАЧЕН СЕРТИФИКАТОМ — оплату НЕ брать!"

        text = (
            f"{status_text}\n\n"
            f"📅 {b.booking_date.strftime('%d.%m.%Y')} в {str(b.start_time)[:5]}\n"
            f"{service} ({trans})\n"
            f"📍 {b.location}\n"
            f"👤 {b.client.name}"
        )
        if b.client.phone:
            text += f" ({b.client.phone})"
        text += cert_line
        await message.answer(text, reply_markup=kb)


@router.message(CommandStart())
async def start(message: types.Message):
    instructor = await _get_instructor(message)
    if not instructor:
        await message.answer("Вы не зарегистрированы как инструктор.")
        return
    await message.answer(
        f"👋 Здравствуйте, {instructor.name}!\nВаши записи на сегодня:",
        reply_markup=INSTRUCTOR_KEYBOARD,
    )
    await _show_bookings(message, instructor)


@router.message(F.text == "📅 Мои записи на сегодня")
async def today_bookings(message: types.Message):
    instructor = await _get_instructor(message)
    if not instructor:
        return
    await _show_bookings(message, instructor)


@router.callback_query(F.data.startswith("arrived:"))
async def client_arrived(callback: types.CallbackQuery):
    booking_id = int(callback.data.split(":")[1])
    async with async_session() as db:
        result = await db.execute(select(Booking).where(Booking.id == booking_id))
        booking = result.scalar_one_or_none()
        if not booking:
            await callback.answer("Запись не найдена", show_alert=True)
            return
        booking.status = BookingStatus.IN_PROGRESS
        await db.commit()
    await callback.message.edit_text(
        f"{callback.message.text}\n\n✅ Клиент отмечен как пришедший",
        reply_markup=None,
    )
    await callback.answer("Отмечено!")


@router.callback_query(F.data.startswith("done:"))
async def lesson_done(callback: types.CallbackQuery):
    booking_id = int(callback.data.split(":")[1])
    async with async_session() as db:
        result = await db.execute(select(Booking).where(Booking.id == booking_id))
        booking = result.scalar_one_or_none()
        if not booking:
            await callback.answer("Запись не найдена", show_alert=True)
            return
        booking.status = BookingStatus.COMPLETED
        await db.commit()
    await callback.message.edit_text(
        f"{callback.message.text}\n\n💰 Занятие завершено. Оплата получена.",
        reply_markup=None,
    )
    await callback.answer("Занятие завершено!")


app = FastAPI()


@app.get("/")
def health():
    return {"status": "ok"}


def run_web():
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))


async def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    threading.Thread(target=run_web, daemon=True).start()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Instructor bot started, polling...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
