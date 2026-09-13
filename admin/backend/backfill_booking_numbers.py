"""Одноразовый скрипт: пересчитывает шестизначные номера всех записей.

Запуск из папки admin/backend:
    python backfill_booking_numbers.py

Проставляет booking_number для ВСЕХ записей (Booking) подряд, начиная с
самой ранней по времени создания: 000001, 000002, ...
Идемпотентен — повторный запуск просто переприсвоит те же номера.
Номер уникален и продолжает последовательность для новых подтверждённых заявок.
"""
import asyncio

from sqlalchemy import select

from app.database import async_session
from app.models.models import Booking


async def main():
    async with async_session() as db:
        result = await db.execute(
            select(Booking).order_by(
                Booking.created_at.asc(),
                Booking.booking_date.asc(),
                Booking.start_time.asc(),
            )
        )
        bookings = result.scalars().all()
        if not bookings:
            print("Записей нет, нумеровать нечего.")
            return

        n = 0
        for b in bookings:
            n += 1
            b.booking_number = f"{n:06d}"
        await db.commit()
        print(f"Проставлены номера для {n} записей: 000001 … {n:06d}")


if __name__ == "__main__":
    asyncio.run(main())
