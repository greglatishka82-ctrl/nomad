"""
Скрипт для наполнения БД тестовыми данными.
Запускать из папки backend: python seed_data.py
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from datetime import time
from sqlalchemy import select
from app.database import async_session, init_db
from app.models.models import (
    Instructor, TransmissionType, Package, FAQItem
)


INSTRUCTORS = [
    {
        "name": "Алибек Жаксыбеков",
        "transmission": TransmissionType.BOTH,
        "experience_years": 8,
        "rating": 4.9,
        "working_hours_start": time(9, 0),
        "working_hours_end": time(19, 0),
        "lunch_start": time(13, 0),
        "lunch_end": time(14, 0),
        "days_off": "Воскресенье",
        "description": "Опытный инструктор, специализируется на подготовке к экзаменам. Обучил более 500 учеников.",
        "telegram_username": "@alibek_nomad",
    },
    {
        "name": "Динара Сейткали",
        "transmission": TransmissionType.AUTOMATIC,
        "experience_years": 5,
        "rating": 4.8,
        "working_hours_start": time(10, 0),
        "working_hours_end": time(18, 0),
        "lunch_start": time(13, 0),
        "lunch_end": time(14, 0),
        "days_off": "Суббота,Воскресенье",
        "description": "Специалист по автоматической коробке передач. Мягкий и терпеливый подход к обучению.",
        "telegram_username": "@dinara_nomad",
    },
    {
        "name": "Серик Байжанов",
        "transmission": TransmissionType.MANUAL,
        "experience_years": 12,
        "rating": 5.0,
        "working_hours_start": time(8, 0),
        "working_hours_end": time(17, 0),
        "lunch_start": time(12, 0),
        "lunch_end": time(13, 0),
        "days_off": "Воскресенье",
        "description": "Ветеран автошколы NOMAD. Мастер механической коробки передач, строгий но справедливый.",
        "telegram_username": "@serik_nomad",
    },
    {
        "name": "Айгуль Нурланова",
        "transmission": TransmissionType.BOTH,
        "experience_years": 6,
        "rating": 4.7,
        "working_hours_start": time(9, 0),
        "working_hours_end": time(18, 0),
        "lunch_start": time(13, 0),
        "lunch_end": time(14, 0),
        "days_off": "Суббота,Воскресенье",
        "description": "Дружелюбный инструктор, особый подход к новичкам. Работает с механикой и автоматом.",
        "telegram_username": "@aigul_nomad",
    },
    {
        "name": "Бауыржан Ахметов",
        "transmission": TransmissionType.BOTH,
        "experience_years": 10,
        "rating": 4.8,
        "working_hours_start": time(9, 0),
        "working_hours_end": time(20, 0),
        "lunch_start": time(14, 0),
        "lunch_end": time(15, 0),
        "days_off": "Воскресенье",
        "description": "Инструктор широкого профиля. Помогает ученикам преодолеть страх вождения.",
        "telegram_username": "@baurzhan_nomad",
    },
]

PACKAGES = [
    {"name": "5 занятий", "sessions_count": 5, "price": 27000},
    {"name": "10 занятий", "sessions_count": 10, "price": 50000},
    {"name": "20 занятий", "sessions_count": 20, "price": 95000},
]

FAQ_ITEMS = [
    {
        "question": "Как записаться на занятие?",
        "answer": "Откройте вкладку 'Записаться' в приложении, выберите тип занятия, КПП, дату и время. Инструктор назначается автоматически.",
        "sort_order": 1,
    },
    {
        "question": "Сколько стоит урок вождения?",
        "answer": "Урок вождения стоит 6 000 ₸ за 60 минут. Пробный экзамен — 5 000 ₸ за 15 минут. При покупке пакета занятий действует скидка.",
        "sort_order": 2,
    },
    {
        "question": "Как отменить запись?",
        "answer": "Отменить запись можно не позднее чем за 2 часа до начала занятия в разделе 'Мои записи'. Поздняя отмена не предусмотрена.",
        "sort_order": 3,
    },
    {
        "question": "Где находится автошкола?",
        "answer": "Учебная площадка: Павлодар, ул. Циолковского 28/1. Экзаменационная площадка: ул. Циолковского 30.",
        "sort_order": 4,
    },
    {
        "question": "Как происходит оплата?",
        "answer": "Оплата производится наличными на месте перед занятием. Эквайринг не предусмотрен.",
        "sort_order": 5,
    },
    {
        "question": "Можно ли выбрать инструктора?",
        "answer": "Инструктор назначается автоматически из доступных специалистов с наивысшим рейтингом и нужным типом коробки передач.",
        "sort_order": 6,
    },
    {
        "question": "Что такое пакет занятий?",
        "answer": "Пакет — это предоплаченный набор занятий со скидкой. Доступны пакеты на 5, 10 и 20 занятий. Заявку можно оформить в разделе 'Пакеты'.",
        "sort_order": 7,
    },
    {
        "question": "Как использовать реферальный код?",
        "answer": "Поделитесь своим кодом с другом. После его первой оплаты вы получите бонус на счёт. Код находится в разделе 'Реферальная программа'.",
        "sort_order": 8,
    },
]


async def seed():
    await init_db()
    async with async_session() as db:
        # Инструкторы
        existing = await db.execute(select(Instructor))
        if not existing.scalars().first():
            print("Добавляю инструкторов...")
            for data in INSTRUCTORS:
                db.add(Instructor(**data))
            await db.commit()
            print(f"  ✓ Добавлено {len(INSTRUCTORS)} инструкторов")
        else:
            print("  Инструкторы уже есть, пропускаю")

        # Пакеты
        existing_pkg = await db.execute(select(Package))
        if not existing_pkg.scalars().first():
            print("Добавляю пакеты занятий...")
            for data in PACKAGES:
                db.add(Package(**data))
            await db.commit()
            print(f"  ✓ Добавлено {len(PACKAGES)} пакетов")
        else:
            print("  Пакеты уже есть, пропускаю")

        # FAQ
        existing_faq = await db.execute(select(FAQItem))
        if not existing_faq.scalars().first():
            print("Добавляю FAQ...")
            for data in FAQ_ITEMS:
                db.add(FAQItem(**data))
            await db.commit()
            print(f"  ✓ Добавлено {len(FAQ_ITEMS)} вопросов FAQ")
        else:
            print("  FAQ уже есть, пропускаю")

    print("\n✅ База данных заполнена тестовыми данными!")
    print("   Инструкторы, пакеты и FAQ готовы.")


if __name__ == "__main__":
    asyncio.run(seed())
