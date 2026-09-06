import logging
from typing import Optional
from datetime import datetime, date, time, timedelta

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, InputMediaDocument
)
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

# Инструкторский бот — заполняется из main.py при старте
instructor_bot: Optional[Bot] = None
from sqlalchemy import select, and_, or_, func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings, TIMEZONE
from app.database import async_session
from app.models.models import (
    Client, Booking, BookingStatus, ServiceType, TransmissionType,
    Instructor, RatingRecord, RatingVote, ReferralRecord, FAQItem,
    NotificationSent, Certificate, AuditLog, InstructorGender, MobileBooking, ClientPackage, Package,
    SupportMessage, Event
)
from app.services.booking_service import get_available_slots, get_available_slots_for_instructor, find_best_instructor, find_best_instructor_with_location, has_available_instructors, reserve_available_vehicle
from app.services.client_lifecycle import (
    find_client_by_phone as _get_client_by_phone,
    reactivate_deleted_client,
)
from app.services.phone_utils import normalize_phone

logger = logging.getLogger(__name__)
router = Router()

BOOKING_WINDOW_DAYS = 5


async def _log_event(db: AsyncSession, event_type: str, message: str, client_id: int = None, instructor_id: int = None, booking_id: int = None, source: str = "telegram"):
    """Записывает событие клиента/инструктора в таблицу events"""
    from app.models.models import Event
    db.add(Event(
        event_type=event_type,
        source=source,
        client_id=client_id,
        instructor_id=instructor_id,
        booking_id=booking_id,
        message=message
    ))
    await db.commit()


async def _block_after_repeated_cancellations(db: AsyncSession, client_id: int) -> None:
    """Compatibility check: the final block is created by the admin decision."""
    from app.models.models import ClientBlock, Event
    now = datetime.now(TIMEZONE).replace(tzinfo=None)
    count = (await db.execute(select(func.count()).select_from(Event).where(
        Event.event_type == "booking_cancelled", Event.client_id == client_id,
        Event.created_at >= now - timedelta(hours=24),
    ))).scalar() or 0
    if count < 5:
        return
    active = (await db.execute(select(ClientBlock).where(
        ClientBlock.client_id == client_id, ClientBlock.blocked_until > now,
    ))).scalar_one_or_none()
    if not active:
        db.add(ClientBlock(client_id=client_id, blocked_until=now + timedelta(hours=24),
                           reason="Пять отмен записей за последние 24 часа"))
        await db.commit()


async def _ensure_client_is_not_blocked(db: AsyncSession, client_id: int) -> None:
    from app.models.models import ClientBlock
    now = datetime.now(TIMEZONE).replace(tzinfo=None)
    block = (await db.execute(select(ClientBlock).where(
        ClientBlock.client_id == client_id, ClientBlock.blocked_until > now,
    ))).scalars().first()
    if block:
        raise ValueError(
            "Для вашего аккаунта временно ограничены создание, отмена и перенос записей. "
            f"Ограничение действует до {block.blocked_until.strftime('%d.%m.%Y %H:%M')}."
        )


async def _consume_client_reschedule_slot(db: AsyncSession, client_id: int) -> tuple[Client, int]:
    """Atomically consume one of the three client reschedules for 24 hours.

    The counter lives on the client, not in a bot state or app cache, so the
    Telegram bot and the mobile API share exactly the same limit.  A bootstrap
    from recent legacy logs prevents a client from bypassing the new counter
    immediately after deployment.
    """
    now = datetime.now(TIMEZONE).replace(tzinfo=None)
    client = (await db.execute(
        select(Client).where(Client.id == client_id).with_for_update()
    )).scalar_one()
    window_started = client.reschedule_window_started_at

    if not window_started:
        since = now - timedelta(hours=24)
        audit_filters = (
            AuditLog.action.in_(["booking_reschedule_requested", "booking_rescheduled"]),
            AuditLog.created_at >= since,
            AuditLog.details.contains(f"(id={client_id})"),
        )
        event_filters = (
            Event.event_type == "booking_rescheduled",
            Event.client_id == client_id,
            Event.source.in_(["telegram", "mobile"]),
            Event.created_at >= since,
        )
        audit_count = (await db.execute(select(func.count()).select_from(AuditLog).where(*audit_filters))).scalar() or 0
        legacy_event_count = (await db.execute(select(func.count()).select_from(Event).where(*event_filters))).scalar() or 0
        audit_first = (await db.execute(select(func.min(AuditLog.created_at)).where(*audit_filters))).scalar_one_or_none()
        event_first = (await db.execute(select(func.min(Event.created_at)).where(*event_filters))).scalar_one_or_none()
        client.reschedule_count_24h = min(3, audit_count + legacy_event_count)
        client.reschedule_window_started_at = min(
            (value for value in (audit_first, event_first) if value is not None),
            default=now,
        )
    elif now - window_started >= timedelta(hours=24):
        client.reschedule_count_24h = 0
        client.reschedule_window_started_at = now

    if client.reschedule_count_24h >= 3:
        raise ValueError(
            "Лимит переносов исчерпан: за последние 24 часа можно самостоятельно "
            "перенести запись не более 3 раз. Следующий перенос будет доступен позже."
        )

    client.reschedule_count_24h += 1
    return client, client.reschedule_count_24h


async def _add_support_notice(db: AsyncSession, client_id: int, text: str) -> None:
    db.add(SupportMessage(
        client_id=client_id,
        channel="client",
        sender="admin",
        text=text,
        is_read=False,
        is_admin_read=True,
    ))


async def _restore_package_session(db: AsyncSession, booking: Booking) -> None:
    if not booking.package_id:
        return
    purchase = (await db.execute(select(ClientPackage).where(
        ClientPackage.client_id == booking.client_id,
        ClientPackage.package_id == booking.package_id,
    ).order_by(ClientPackage.purchased_at.desc()))).scalars().first()
    if purchase:
        purchase.remaining_sessions += 1
        purchase.is_active = True


class BookingStates(StatesGroup):
    waiting_name = State()
    choosing_service = State()
    choosing_location = State()  # Новый шаг для выбора площадки
    choosing_transmission = State()
    choosing_instructor_gender = State()
    choosing_date = State()
    choosing_time = State()
    entering_phone = State()
    confirming = State()


class RescheduleStates(StatesGroup):
    choosing_date = State()
    choosing_time = State()


class CertificateStates(StatesGroup):
    waiting_code = State()


class SupportStates(StatesGroup):
    writing_message = State()


MAIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📝 Записаться"), KeyboardButton(text="📋 Мои записи")],
        [KeyboardButton(text="ℹ️ Как записаться"), KeyboardButton(text="❓ FAQ")],
        [KeyboardButton(text="📚 История обучения"), KeyboardButton(text="📞 Контакты")],
        [KeyboardButton(text="🎁 Пригласи друга"), KeyboardButton(text="💬 Поддержка")],
        [KeyboardButton(text="🎟️ Сертификат")],
    ],
    resize_keyboard=True,
)


def _instructor_card_text(inst: Instructor) -> str:
    trans_labels = {"manual": "Механика", "automatic": "Автомат", "both": "Механика и автомат"}
    return (
        f"👨‍🏫 <b>{inst.name}</b>\n"
        f"⚙️ {trans_labels.get(inst.transmission, 'Обе')}\n"
        f"📅 Стаж: {inst.experience_years} лет"
    )


BACK_BUTTON = InlineKeyboardButton(text="◀️ Назад", callback_data="back")


def _kb_with_back(rows: list) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows + [[BACK_BUTTON]])


async def _build_date_buttons(
    db: AsyncSession,
    service_type: ServiceType,
    transmission: TransmissionType,
    instructor_gender: InstructorGender = "any",
    prefix: str = "date",
) -> list:
    now = datetime.now(TIMEZONE)
    today = now.date()

    # Определяем последний слот динамически по инструкторам
    all_instructors_result = await db.execute(select(Instructor).where(Instructor.is_active == True))
    all_instructors = all_instructors_result.scalars().all()
    if all_instructors:
        max_last_slot_hour = max(
            (inst.working_hours_end.hour for inst in all_instructors if inst.working_hours_end),
            default=settings.WORKING_HOURS_END
        )
    else:
        max_last_slot_hour = settings.WORKING_HOURS_END

    # Ограничиваем максимум до 21:00 (слот 21:00 = занятие с 21:00 до 22:00)
    max_last_slot_hour = min(max_last_slot_hour, 21)

    after_cutoff = now.hour > max_last_slot_hour or (now.hour == max_last_slot_hour and now.minute >= 1)
    start_offset = 1 if after_cutoff else 0

    buttons = []
    i = start_offset
    while i < BOOKING_WINDOW_DAYS:
        target_date = today + timedelta(days=i)
        if await has_available_instructors(db, target_date, service_type, transmission, instructor_gender):
            buttons.append([InlineKeyboardButton(
                text=target_date.strftime("%d.%m.%Y"),
                callback_data=f"{prefix}:{target_date.strftime('%d.%m.%Y')}"
            )])
        i += 1
    return buttons


async def _get_client_by_telegram(telegram_id: str) -> Optional[Client]:
    async with async_session() as db:
        result = await db.execute(
            select(Client).where(
                Client.telegram_id == telegram_id,
                Client.is_deleted == False,
            )
        )
        return result.scalar_one_or_none()


async def _show_service_keyboard(target, client_name: str):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚗 Обучение вождению", callback_data="service:training")],
        [InlineKeyboardButton(text="🏁 Пробный экзамен", callback_data="service:exam")],
    ])
    text = f"{client_name}, выберите услугу:" if client_name else "Выберите услугу:"
    await target.answer(text, reply_markup=kb)


@router.callback_query(F.data == "back")
async def go_back(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    current = await state.get_state()

    if current == BookingStates.choosing_service.state:
        await callback.message.edit_text("Нажмите «Записаться» чтобы начать заново.", reply_markup=None)
        await state.clear()

    elif current == BookingStates.choosing_location.state:
        # Возврат к выбору услуги
        data = await state.get_data()
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚗 Обучение вождению", callback_data="service:training")],
            [InlineKeyboardButton(text="🏁 Пробный экзамен", callback_data="service:exam")],
        ])
        await callback.message.edit_text(f"{data.get('client_name', '')}, выберите услугу:", reply_markup=kb)
        await state.set_state(BookingStates.choosing_service)

    elif current == BookingStates.choosing_transmission.state:
        # Возврат к выбору услуги
        data = await state.get_data()
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚗 Обучение вождению", callback_data="service:training")],
            [InlineKeyboardButton(text="🏁 Пробный экзамен", callback_data="service:exam")],
        ])
        await callback.message.edit_text(f"{data.get('client_name', '')}, выберите услугу:", reply_markup=kb)
        await state.set_state(BookingStates.choosing_service)

    elif current == BookingStates.choosing_instructor_gender.state:
        data = await state.get_data()
        service = data.get("service_type", "training")
        if service == "exam":
            # Для экзамена КПП не выбирается — возвращаемся к выбору услуги
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🚗 Обучение вождению", callback_data="service:training")],
                [InlineKeyboardButton(text="🏁 Пробный экзамен", callback_data="service:exam")],
            ])
            await callback.message.edit_text(f"{data.get('client_name', '')}, выберите услугу:", reply_markup=kb)
            await state.set_state(BookingStates.choosing_service)
        else:
            kb = _kb_with_back([
                [InlineKeyboardButton(text="⚙️ Механика", callback_data="trans:manual")],
                [InlineKeyboardButton(text="🔄 Автомат", callback_data="trans:automatic")],
            ])
            await callback.message.edit_text("Выберите коробку передач:", reply_markup=kb)
            await state.set_state(BookingStates.choosing_transmission)

    elif current == BookingStates.choosing_date.state:
        kb = _kb_with_back([
            [InlineKeyboardButton(text="👨 Мужчина", callback_data="gender:male")],
            [InlineKeyboardButton(text="👩 Девушка", callback_data="gender:female")],
            [InlineKeyboardButton(text="🤷 Не важно", callback_data="gender:any")],
        ])
        await callback.message.edit_text("Предпочтения по инструктору:", reply_markup=kb)
        await state.set_state(BookingStates.choosing_instructor_gender)

    elif current == BookingStates.choosing_time.state:
        data = await state.get_data()
        service_type = ServiceType.TRAINING if data["service_type"] == "training" else ServiceType.EXAM
        trans_map = {"manual": "manual", "automatic": "automatic"}
        transmission = trans_map[data["transmission"]]
        
        gender_map = {"male": "male", "female": "female", "any": "any"}
        instructor_gender = gender_map.get(data.get("instructor_gender", "any"), "any")

        async with async_session() as db:
            buttons = await _build_date_buttons(db, service_type, transmission, instructor_gender)
        kb = _kb_with_back(buttons)
        await callback.message.edit_text("Выберите дату:", reply_markup=kb)
        await state.set_state(BookingStates.choosing_date)

    elif current == BookingStates.entering_phone.state:
        data = await state.get_data()
        booking_date = date.fromisoformat(data["booking_date"])
        service_type = ServiceType.TRAINING if data["service_type"] == "training" else ServiceType.EXAM
        trans_map = {"manual": "manual", "automatic": "automatic"}
        transmission = trans_map[data["transmission"]]
        location = data["location"]
        
        gender_map = {"male": "male", "female": "female", "any": "any"}
        instructor_gender = gender_map.get(data.get("instructor_gender", "any"), "any")

        async with async_session() as db:
            slots = await get_available_slots(db, booking_date, service_type, transmission, location, instructor_gender)

        buttons = []
        for slot in slots[:12]:
            buttons.append([InlineKeyboardButton(
                text=slot.strftime("%H:%M"),
                callback_data=f"time:{slot.strftime('%H:%M')}"
            )])
        kb = _kb_with_back(buttons)
        await callback.message.edit_text("Выберите время:", reply_markup=kb)
        await state.set_state(BookingStates.choosing_time)

    elif current == RescheduleStates.choosing_time.state:
        data = await state.get_data()
        booking_id = data["reschedule_booking_id"]
        async with async_session() as db:
            result = await db.execute(select(Booking).where(Booking.id == booking_id))
            booking = result.scalar_one_or_none()
            if not booking:
                await callback.message.edit_text("Запись не найдена.")
                await state.clear()
                return
            buttons = await _build_date_buttons(db, booking.service_type, booking.transmission, prefix="resch_date")
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)
        await callback.message.edit_text("Выберите новую дату:", reply_markup=kb)
        await state.set_state(RescheduleStates.choosing_date)

    else:
        await callback.message.edit_text("Нажмите «Записаться» чтобы начать заново.", reply_markup=None)
        await state.clear()


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    args = message.text.split() if message.text else []
    referral_code = None
    if len(args) > 1:
        referral_code = args[1]
    if referral_code:
        await state.update_data(referral_code=referral_code)

    client = await _get_client_by_telegram(str(message.from_user.id))
    if client:
        # Существующий клиент — сразу показываем меню
        await message.answer(
            "С возвращением! 👋",
            reply_markup=MAIN_KEYBOARD,
        )
    else:
        # Новый пользователь — показываем приветственный текст
        welcome_text = (
            "Добро пожаловать! 👋\n\n"
            "Секрет успеха - правильное обучение.\n\n"
            "Вот что мы предлагаем:\n\n"
            "🚗 <b>Вождение — 10 000 ₸/час</b>\n"
            "Занятие с инструктором на площадке Циолковского 30.\n\n"
            "📋 <b>Пробный экзамен — 5 000 ₸</b>\n"
            "1 круг / 20 минут — проверить себя перед настоящим экзаменом."
        )
        await message.answer(welcome_text, reply_markup=MAIN_KEYBOARD, parse_mode="HTML")


@router.message(F.text == "📝 Записаться")
async def btn_book(message: Message, state: FSMContext):
    await state.clear()

    client = await _get_client_by_telegram(str(message.from_user.id))
    if client:
        await state.update_data(client_name=client.name, client_id=client.id)
        await _show_service_keyboard(message, client.name)
        await state.set_state(BookingStates.choosing_service)
    else:
        # Новый пользователь — сразу начинаем запись, имя спросим в конце
        await _show_service_keyboard(message, "")
        await state.set_state(BookingStates.choosing_service)


@router.message(BookingStates.waiting_name)
async def process_name(message: Message, state: FSMContext):
    name = message.text.strip()
    if len(name) < 2:
        await message.answer("Пожалуйста, введите ваше имя:")
        return
    await state.update_data(client_name=name)
    phone_kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Отправить номер", request_contact=True)]],
        resize_keyboard=True, one_time_keyboard=True,
    )
    await message.answer(
        "Введите ваш номер телефона или отправьте его кнопкой ниже:",
        reply_markup=phone_kb,
    )
    await state.set_state(BookingStates.entering_phone)


@router.callback_query(BookingStates.choosing_service, F.data.startswith("service:"))
async def process_service(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    service = callback.data.split(":")[1]
    await state.update_data(service_type=service)

    # Пробный экзамен — только автомат и только на новой площадке
    if service == "exam":
        await state.update_data(
            transmission="automatic",
            location="Циолковского 30",
            price=5000
        )
        kb = _kb_with_back([
            [InlineKeyboardButton(text="👨 Мужчина", callback_data="gender:male")],
            [InlineKeyboardButton(text="👩 Девушка", callback_data="gender:female")],
            [InlineKeyboardButton(text="🤷 Не важно", callback_data="gender:any")],
        ])
        await callback.message.edit_text("Предпочтения по инструктору:", reply_markup=kb)
        await state.set_state(BookingStates.choosing_instructor_gender)
    else:
        await state.update_data(location=settings.LOCATION_EXAM, price=settings.PRICE_TRAINING_NEW)
        kb = _kb_with_back([
            [InlineKeyboardButton(text="⚙️ Механика", callback_data="trans:manual")],
            [InlineKeyboardButton(text="🔄 Автомат", callback_data="trans:automatic")],
        ])
        await callback.message.edit_text("Выберите коробку передач:", reply_markup=kb)
        await state.set_state(BookingStates.choosing_transmission)


@router.callback_query(BookingStates.choosing_location, F.data.startswith("location:"))
async def process_location(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    location_type = callback.data.split(":")[1]
    
    location = settings.LOCATION_EXAM
    price = settings.PRICE_TRAINING_NEW
    
    await state.update_data(location=location, price=price)
    
    # Выбор коробки передач
    kb = _kb_with_back([
        [InlineKeyboardButton(text="⚙️ Механика", callback_data="trans:manual")],
        [InlineKeyboardButton(text="🔄 Автомат", callback_data="trans:automatic")],
    ])
    await callback.message.edit_text("Выберите коробку передач:", reply_markup=kb)
    await state.set_state(BookingStates.choosing_transmission)


@router.callback_query(BookingStates.choosing_transmission, F.data.startswith("trans:"))
async def process_transmission(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    trans = callback.data.split(":")[1]
    await state.update_data(transmission=trans)

    kb = _kb_with_back([
        [InlineKeyboardButton(text="👨 Мужчина", callback_data="gender:male")],
        [InlineKeyboardButton(text="👩 Девушка", callback_data="gender:female")],
        [InlineKeyboardButton(text="🤷 Не важно", callback_data="gender:any")],
    ])
    await callback.message.edit_text("Предпочтения по инструктору:", reply_markup=kb)
    await state.set_state(BookingStates.choosing_instructor_gender)


@router.callback_query(BookingStates.choosing_instructor_gender, F.data.startswith("gender:"))
async def process_instructor_gender(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    gender = callback.data.split(":")[1]
    await state.update_data(instructor_gender=gender)

    data = await state.get_data()
    service_type_data = data.get("service_type", "training")
    service_type = ServiceType.TRAINING if service_type_data == "training" else ServiceType.EXAM
    trans_map = {"manual": "manual", "automatic": "automatic"}
    transmission = trans_map[data["transmission"]]

    gender_map = {"male": "male", "female": "female", "any": "any"}
    instructor_gender = gender_map.get(gender, "any")

    async with async_session() as db:
        buttons = await _build_date_buttons(db, service_type, transmission, instructor_gender)
    if not buttons:
        await callback.message.edit_text("К сожалению, на ближайшие 5 дней нет свободных дат. Попробуйте позже.")
        await state.clear()
        return
    kb = _kb_with_back(buttons)
    await callback.message.edit_text("Выберите дату:", reply_markup=kb)
    await state.set_state(BookingStates.choosing_date)


@router.callback_query(BookingStates.choosing_date, F.data.startswith("date:"))
async def process_date_callback(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    # Extract date from callback data (format: date:DD.MM.YYYY)
    parts = callback.data.split(":", 1)  # Split only on first colon
    date_str = parts[1] if len(parts) > 1 else ""

    try:
        booking_date = datetime.strptime(date_str, "%d.%m.%Y").date()
    except ValueError:
        await callback.message.edit_text("Ошибка в дате. Попробуйте снова:")
        return

    today_kz = datetime.now(TIMEZONE).date()
    if booking_date < today_kz:
        await callback.message.edit_text("Дата не может быть в прошлом. Выберите другую дату:")
        return

    data = await state.get_data()
    service_type = ServiceType.TRAINING if data["service_type"] == "training" else ServiceType.EXAM
    trans_map = {"manual": "manual", "automatic": "automatic"}
    transmission = trans_map[data["transmission"]]
    # Для экзамена площадка фиксированная, для тренировки — определяется в момент записи
    location = settings.LOCATION_EXAM if service_type == ServiceType.EXAM else settings.LOCATION_EXAM

    gender_map = {"male": "male", "female": "female", "any": "any"}
    instructor_gender = gender_map.get(data.get("instructor_gender", "any"), "any")

    async with async_session() as db:
        slots = await get_available_slots(db, booking_date, service_type, transmission, location, instructor_gender)

    if not slots:
        async with async_session() as db:
            buttons = await _build_date_buttons(db, service_type, transmission, instructor_gender)
        kb = _kb_with_back(buttons)
        await callback.message.edit_text(
            "На эту дату нет свободных слотов. Выберите другую дату:",
            reply_markup=kb,
        )
        return

    await state.update_data(booking_date=str(booking_date))
    buttons = []
    for slot in slots[:12]:
        buttons.append([InlineKeyboardButton(
            text=slot.strftime("%H:%M"),
            callback_data=f"time:{slot.strftime('%H:%M')}"
        )])
    kb = _kb_with_back(buttons)
    await callback.message.edit_text("Выберите время:", reply_markup=kb)
    await state.set_state(BookingStates.choosing_time)


@router.message(BookingStates.choosing_date)
async def process_date(message: Message, state: FSMContext):
    try:
        booking_date = datetime.strptime(message.text.strip(), "%d.%m.%Y").date()
    except ValueError:
        await message.answer("Неверный формат. Введите дату как ДД.ММ.ГГГГ:")
        return
    today_kz = datetime.now(TIMEZONE).date()
    if booking_date < today_kz:
        await message.answer("Дата не может быть в прошлом. Введите другую дату:")
        return

    data = await state.get_data()
    service_type = ServiceType.TRAINING if data["service_type"] == "training" else ServiceType.EXAM
    trans_map = {"manual": "manual", "automatic": "automatic"}
    transmission = trans_map[data["transmission"]]
    location = settings.LOCATION_EXAM  # для слотов передаём exam location, площадка определяется в финализации

    gender_map = {"male": "male", "female": "female", "any": "any"}
    instructor_gender = gender_map.get(data.get("instructor_gender", "any"), "any")

    async with async_session() as db:
        slots = await get_available_slots(db, booking_date, service_type, transmission, location, instructor_gender)

    if not slots:
        await message.answer("На эту дату нет свободных слотов. Попробуйте другую дату:")
        return

    await state.update_data(booking_date=str(booking_date))
    buttons = []
    for slot in slots[:12]:
        buttons.append([InlineKeyboardButton(
            text=slot.strftime("%H:%M"),
            callback_data=f"time:{slot.strftime('%H:%M')}"
        )])
    kb = _kb_with_back(buttons)
    await message.answer("Выберите время:", reply_markup=kb)
    await state.set_state(BookingStates.choosing_time)


@router.callback_query(BookingStates.choosing_time, F.data.startswith("time:"))
async def process_time(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    time_str = callback.data.split(":", 1)[1]
    await state.update_data(start_time=time_str)

    client = await _get_client_by_telegram(str(callback.from_user.id))
    if client and client.phone:
        # Знакомый клиент с телефоном — сразу финализируем
        await callback.message.edit_text(f"Выбрано время: {time_str}")
        await _finalize_booking(callback.message, state, callback.from_user.id, client)
    elif client and not client.phone:
        # Клиент есть но без телефона — спрашиваем только телефон
        phone_kb = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="📱 Отправить номер", request_contact=True)]],
            resize_keyboard=True, one_time_keyboard=True,
        )
        await callback.message.edit_text(f"Выбрано время: {time_str}")
        await callback.message.answer(
            "Введите ваш номер телефона или отправьте его кнопкой ниже:",
            reply_markup=phone_kb,
        )
        await state.set_state(BookingStates.entering_phone)
    else:
        # Новый пользователь — сначала спрашиваем имя
        await callback.message.edit_text(f"Выбрано время: {time_str}")
        await callback.message.answer("Как вас зовут?", reply_markup=MAIN_KEYBOARD)
        await state.set_state(BookingStates.waiting_name)


async def _finalize_booking(message: Message, state: FSMContext, telegram_id: str, client: Client = None):
    data = await state.get_data()
    booking_location = settings.LOCATION_EXAM  # будет переопределено внутри
    package_progress = None

    async with async_session() as db:
        # Always load a fresh object in this session. The caller may have just
        # linked Telegram to an existing phone profile in another session.
        # That profile is authoritative and its administrator-entered name is
        # intentionally never overwritten by the name typed in Telegram.
        result = await db.execute(
            select(Client).where(
                Client.telegram_id == str(telegram_id),
                Client.is_deleted == False,
            ).with_for_update()
        )
        client = result.scalar_one_or_none()
        if not client:
            referral_code = data.get("referral_code")
            referred_by = None
            if referral_code:
                ref_result = await db.execute(
                    select(Client).where(
                        Client.referral_code == referral_code,
                        Client.is_deleted == False,
                    )
                )
                referrer = ref_result.scalar_one_or_none()
                if referrer:
                    referred_by = referrer.id

            client = Client(
                telegram_id=str(telegram_id),
                name=data["client_name"],
                referral_code=telegram_id,
                referred_by_client_id=referred_by,
                referral_discount_available=True if referred_by else False,
            )
            db.add(client)
            await db.flush()

            if referred_by:
                db.add(ReferralRecord(referrer_client_id=referred_by, referred_client_id=client.id))

        active_count_result = await db.execute(
            select(func.count()).select_from(Booking).where(
                and_(
                    Booking.client_id == client.id,
                    Booking.status.in_(["pending", "cancellation_pending", "reschedule_pending", "planned", "confirmed"]),
                )
            )
        )
        active_count = active_count_result.scalar() or 0
        if active_count >= settings.MAX_ACTIVE_BOOKINGS:
            await message.answer(
                f"У вас уже {settings.MAX_ACTIVE_BOOKINGS} активные записи. "
                "Отмените одну через «Мои записи» перед новой записью.",
                reply_markup=MAIN_KEYBOARD,
            )
            await state.clear()
            return

        booking_date_check = date.fromisoformat(data["booking_date"])
        daily_count_result = await db.execute(
            select(func.count()).select_from(Booking).where(
                and_(
                    Booking.client_id == client.id,
                    Booking.booking_date == booking_date_check,
                    Booking.status.in_(["pending", "cancellation_pending", "reschedule_pending", "planned", "confirmed"]),
                )
            )
        )
        daily_count = daily_count_result.scalar() or 0
        if daily_count >= 2:
            await message.answer(
                "Вы не можете создать более 2 записей на один день. "
                "Отмените одну из существующих записей или выберите другой день.",
                reply_markup=MAIN_KEYBOARD,
            )
            await state.clear()
            return

        from app.models.models import ClientBlock
        now_check = datetime.now(TIMEZONE).replace(tzinfo=None)
        block_result = await db.execute(
            select(ClientBlock).where(
                and_(ClientBlock.client_id == client.id, ClientBlock.blocked_until > now_check)
            )
        )
        if block_result.scalars().first():
            await message.answer(
                "Вы слишком часто создавали и отменяли записи. Дождитесь окончания блокировки и выберите подходящее время обдуманно.",
                reply_markup=MAIN_KEYBOARD,
            )
            await state.clear()
            return

        service_type = ServiceType.TRAINING if data["service_type"] == "training" else ServiceType.EXAM
        trans_map = {"manual": "manual", "automatic": "automatic"}
        transmission = trans_map[data["transmission"]]
        trans_label = "Механика" if transmission == "manual" else "Автомат"
        booking_date = date.fromisoformat(data["booking_date"])
        start_t = time.fromisoformat(data["start_time"])
        duration = settings.TRAINING_DURATION_MINUTES if service_type == ServiceType.TRAINING else settings.EXAM_DURATION_MINUTES
        et = timedelta(hours=start_t.hour, minutes=start_t.minute) + timedelta(minutes=duration)
        end_t = time(int(et.total_seconds() // 3600), int((et.total_seconds() % 3600) // 60))

        dup_result = await db.execute(
            select(Booking).where(
                and_(
                    Booking.client_id == client.id,
                    Booking.booking_date == booking_date,
                    Booking.start_time == start_t,
                    Booking.status.in_(["pending", "cancellation_pending", "reschedule_pending", "planned", "confirmed"]),
                )
            )
        )
        if dup_result.scalar_one_or_none():
            await message.answer(
                "У вас уже есть запись на это время. Выберите другое.",
                reply_markup=MAIN_KEYBOARD,
            )
            await state.clear()
            return

        existing_result = await db.execute(
            select(Booking).where(
                and_(
                    Booking.client_id == client.id,
                    Booking.booking_date == booking_date,
                    Booking.status.in_(["pending", "cancellation_pending", "reschedule_pending", "planned", "confirmed"]),
                )
            ).order_by(Booking.start_time)
        )
        existing = list(existing_result.scalars().all())

        class _Tmp:
            pass
        tmp = _Tmp()
        tmp.start_time = start_t
        tmp.end_time = end_t
        all_slots = sorted(existing + [tmp], key=lambda b: b.start_time)

        max_chain = 1
        chain = 1
        for i in range(1, len(all_slots)):
            if all_slots[i - 1].end_time == all_slots[i].start_time:
                chain += 1
                max_chain = max(max_chain, chain)
            else:
                chain = 1

        if max_chain > 2:
            await message.answer(
                "Вы можете записаться не более чем на 2 занятия подряд. "
                "Выберите другое время.",
                reply_markup=MAIN_KEYBOARD,
            )
            await state.clear()
            return

        # Цена и площадка уже определены при выборе площадки и сохранены в state
        price = data.get("price", settings.PRICE_TRAINING if service_type == ServiceType.TRAINING else settings.PRICE_EXAM)
        base_price = price  # Сохраняем исходную цену
        booking_location = data.get("location", settings.LOCATION_EXAM)

        cert_result = await db.execute(
            select(Certificate).where(
                and_(
                    Certificate.activated_by_client_id == client.id,
                    Certificate.remaining > 0,
                )
            ).with_for_update()
        )
        cert = cert_result.scalar_one_or_none()
        certificate_discount = 0
        certificate_id = None
        
        # ВАЖНО: Сертификат применяется ТОЛЬКО если nominal точно совпадает с price
        # Нельзя использовать больший сертификат на меньшую услугу или наоборот
        if cert and cert.nominal == price:
            certificate_discount = price
            cert.remaining -= certificate_discount
            if cert.remaining <= 0:
                cert.is_used = True
            certificate_id = cert.id
            price -= certificate_discount
        
        # Реферальная скидка доступна другу только после того, как пригласивший
        # уже завершил хотя бы одно занятие.
        referral_discount = 0
        if client.referral_discount_available and price > 0:
            existing_referral_booking = (await db.execute(
                select(func.count()).select_from(Booking).where(
                    Booking.client_id == client.id,
                    Booking.referral_discount_amount > 0,
                    Booking.status.in_(["pending", "planned", "confirmed", "in_progress", "completed"]),
                )
            )).scalar() or 0
            referral_eligible = False
            if client.referred_by_client_id:
                referral_eligible = bool((await db.execute(
                    select(func.count()).select_from(Booking).where(
                        Booking.client_id == client.referred_by_client_id,
                        Booking.status == "completed",
                    )
                )).scalar() or 0)
            else:
                # Reward earned by the referrer after the friend's first lesson.
                referral_eligible = True
            if referral_eligible and not existing_referral_booking:
                referral_discount = min(1000, price)
                price -= referral_discount

        gender_map = {"male": "male", "female": "female", "any": "any"}
        instructor_gender = gender_map.get(data.get("instructor_gender", "any"), "any")

        instructor, location_from_search = await find_best_instructor_with_location(
            db, booking_date, start_t, end_t, transmission, service_type, instructor_gender
        )
        if not instructor:
            if certificate_discount > 0:
                cert.remaining += certificate_discount
                if cert.is_used:
                    cert.is_used = False
                await db.commit()
            await message.answer(
                "К сожалению, на это время нет свободных инструкторов. Выберите другое время через «Записаться».",
                reply_markup=MAIN_KEYBOARD,
            )
            await state.clear()
            return

        # Используем площадку из state (которую выбрал пользователь)
        # location_from_search это резервный вариант если что-то пошло не так
        final_location = booking_location if booking_location else location_from_search

        package_purchase = None
        package_bonus_exam_used = False
        if service_type in (ServiceType.TRAINING, ServiceType.EXAM):
            now_naive = datetime.now(TIMEZONE).replace(tzinfo=None)
            package_purchase = (await db.execute(select(ClientPackage).options(selectinload(ClientPackage.package)).where(
                ClientPackage.client_id == client.id, ClientPackage.is_active == True,
                ((ClientPackage.remaining_sessions > 0) if service_type == ServiceType.TRAINING else
                 (ClientPackage.remaining_sessions <= 0) & (ClientPackage.remaining_bonus_exams > 0)),
                (ClientPackage.expires_at.is_(None)) | (ClientPackage.expires_at >= now_naive),
            ).order_by(ClientPackage.expires_at).with_for_update())).scalars().first()
            if package_purchase:
                if service_type == ServiceType.TRAINING:
                    package_purchase.remaining_sessions -= 1
                else:
                    package_purchase.remaining_bonus_exams -= 1
                    package_bonus_exam_used = True
                if package_purchase.remaining_sessions == 0 and package_purchase.remaining_bonus_exams == 0:
                    package_purchase.is_active = False
                if package_purchase.package:
                    total = max(0, package_purchase.package.sessions_count or 0)
                    remaining = max(0, package_purchase.remaining_sessions or 0)
                    package_progress = (max(0, total - remaining), total)
                # A package takes precedence.  Undo a certificate reservation
                # made earlier in the booking flow and do not consume a
                # referral reward for a lesson that is already package-paid.
                if certificate_discount:
                    cert.remaining += certificate_discount
                    cert.is_used = False
                    certificate_discount = 0
                    certificate_id = None
                referral_discount = 0
                price = 0

        vehicle = await reserve_available_vehicle(
            db, booking_date, start_t, end_t, transmission
        )
        if not vehicle:
            await db.rollback()
            await message.answer(
                "Это время только что заняли. Выберите другой свободный слот через «Записаться».",
                reply_markup=MAIN_KEYBOARD,
            )
            await state.clear()
            return

        booking = Booking(
            client_id=client.id,
            instructor_id=instructor.id,
            vehicle_id=vehicle.id,
            service_type=service_type,
            transmission=transmission,
            location=final_location,
            booking_date=booking_date,
            start_time=start_t,
            end_time=end_t,
            status="pending",
            price=price,
            base_price=base_price,
            # Store a certificate only when it actually paid this booking.
            # A merely active but non-matching certificate must not make the
            # instructor card say that no cash is due.
            certificate_id=certificate_id,
            package_id=package_purchase.package_id if package_purchase else None,
            package_bonus_exam_used=package_bonus_exam_used,
            certificate_amount=certificate_discount if certificate_discount > 0 else 0,
            referral_discount_amount=referral_discount if referral_discount > 0 else 0,
            admin_viewed=False,
            admin_confirmed=False,
        )
        db.add(booking)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            await message.answer(
                "Это время уже занято другой записью. Обновите свободные места и выберите другое время.",
                reply_markup=MAIN_KEYBOARD,
            )
            await state.clear()
            return
        await db.refresh(booking)
        
        event_message = f"Заявка #{booking.id} создана, клиент: {data['client_name']}, дата: {data['booking_date']} {data['start_time']}, ожидает подтверждения"
        await _log_event(db, "new_booking", event_message, client_id=client.id, instructor_id=instructor.id, booking_id=booking.id, source="telegram")
        
        if certificate_discount > 0:
            cert_status = "полностью" if cert.is_used else f"остаток {cert.remaining}₸"
            cert_message = f"Сертификат {cert.code} использован клиентом {data['client_name']}: −{certificate_discount}₸, статус: {cert_status}"
            await _log_event(db, "certificate_used", cert_message, client_id=client.id, booking_id=booking.id, source="telegram")

    package_note = (
        f"\n\n📦 <b>Запись оплачена пакетом — {package_progress[0]}/{package_progress[1]} занятий.</b> "
        "Оплачивать инструктору ничего не нужно."
        if package_progress else ""
    )
    pending_msg = (
        f"⏳ <b>Ваша заявка находится в обработке. Ожидайте подтверждения.</b>\n\n"
        f"Если подтверждение не пришло в течение 15 минут, свяжитесь с администратором автошколы.\n\n"
        f"📞 +7 702 718 22 33\n"
        f"📞 +7 707 881 08 48\n\n"
        f"⏰ Заявки подтверждаются в рабочее время с 09:00 до 19:00.\n"
        f"Заявки, созданные после рабочего времени, рассматриваются и подтверждаются на следующий день."
    ) + package_note
    await message.answer(pending_msg, reply_markup=MAIN_KEYBOARD, parse_mode="HTML")

    # Данные для инструктора: сумма к оплате, скидка, сертификат
    # Определяем день: сегодня / завтра / день недели
    _RU_DAYS = ["в понедельник", "во вторник", "в среду", "в четверг", "в пятницу", "в субботу", "в воскресенье"]
    _RU_DAYS_SHORT = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
    _today = datetime.now(TIMEZONE).date()
    _bdate = booking_date  # уже date-объект
    _delta = (_bdate - _today).days
    if _delta <= 7:
        if _delta == 0:
            _day_phrase = "сегодня"
        elif _delta == 1:
            _day_phrase = "завтра"
        elif _delta == 2:
            _day_phrase = "послезавтра"
        else:
            _day_phrase = _RU_DAYS[_bdate.weekday()]
        _header = f"{instructor.name}, у вас {_day_phrase} запись:"
        _date_line = f"📅 {data['booking_date']} в {data['start_time']}"
    else:
        _header = f"{instructor.name}, у вас запись:"
        _date_line = f"📅 {data['booking_date']} ({_RU_DAYS_SHORT[_bdate.weekday()]}) в {data['start_time']}"

    instr_lines = [
        f"📌 Новая запись!",
        f"{_header}",
        f"",
        f"{_date_line}",
        f"Клиент: {data['client_name']}",
        f"Площадка: {booking_location}",
        f"Коробка: {trans_label}",
    ]
    if package_purchase:
        instr_lines.append("📦 ОПЛАЧЕНО ПАКЕТОМ — деньги НЕ брать!")
    elif certificate_id:
        instr_lines.append("🎟️ ОПЛАЧЕНО СЕРТИФИКАТОМ — деньги НЕ брать!")
    elif price > 0:
        discount_parts = []
        if certificate_discount > 0:
            discount_parts.append(f"сертификат −{certificate_discount} ₸")
        if referral_discount > 0:
            discount_parts.append(f"реферал −{referral_discount} ₸")
        if discount_parts:
            if referral_discount > 0:
                instr_lines.append(f"🎁 Скидка по реферальному коду: {referral_discount} ₸")
            instr_lines.append(f"💰 К оплате: {price} ₸")
        else:
            instr_lines.append(f"💰 К оплате: {price} ₸")
    else:
        instr_lines.append("💰 К оплате: 0 ₸")
    instr_text = "\n".join(instr_lines)

    if instructor.telegram_id:
        try:
            # Уведомление о новой записи шлём через инструкторский бот
            notify_bot = instructor_bot if instructor_bot else message.bot
            await notify_bot.send_message(
                int(instructor.telegram_id),
                instr_text,
            )
        except Exception as e:
            logger.error(f"Failed to notify instructor {instructor.id}: {e}")

    await state.clear()


@router.message(BookingStates.entering_phone)
async def process_phone(message: Message, state: FSMContext):
    if message.contact:
        phone = message.contact.phone_number
    else:
        phone = message.text.strip()

    phone = normalize_phone(phone)
    if not phone:
        await message.answer("Не удалось распознать номер. Отправьте номер ещё раз кнопкой ниже.")
        return

    try:
        async with async_session() as db:
            client = (await db.execute(
                select(Client).where(
                    Client.telegram_id == str(message.from_user.id),
                    Client.is_deleted == False,
                )
            )).scalar_one_or_none()
            phone_owner = await _get_client_by_phone(
                db, phone, include_deleted=True, for_update=True,
            )
            was_deleted = bool(phone_owner and phone_owner.is_deleted)
            if was_deleted:
                data = await state.get_data()
                await reactivate_deleted_client(
                    db,
                    phone_owner,
                    name=data.get("client_name") or phone_owner.name,
                    phone=phone,
                    password_hash=None,
                )

            if phone_owner and (not client or phone_owner.id != client.id):
                # The administrator may have created this client from a call.
                # Phone is the identity, so attach Telegram to that same card
                # and retain its bookings, packages and history.
                if (
                    not was_deleted
                    and phone_owner.telegram_id
                    and phone_owner.telegram_id != str(message.from_user.id)
                ):
                    await message.answer("Этот номер уже привязан к другому Telegram-аккаунту.", reply_markup=MAIN_KEYBOARD)
                    await state.clear()
                    return
                if client:
                    # Move data from a legacy empty Telegram profile into the
                    # phone owner's canonical profile before detaching it.
                    await db.execute(update(Booking).where(Booking.client_id == client.id).values(client_id=phone_owner.id))
                    await db.execute(update(ClientPackage).where(ClientPackage.client_id == client.id).values(client_id=phone_owner.id))
                    await db.execute(update(Certificate).where(Certificate.activated_by_client_id == client.id).values(activated_by_client_id=phone_owner.id))
                    await db.execute(update(Certificate).where(Certificate.used_by_user_id == client.id).values(used_by_user_id=phone_owner.id))
                    client.telegram_id = None
                    client.phone = None
                phone_owner.phone = phone  # normalize legacy records too
                phone_owner.telegram_id = str(message.from_user.id)
                client = phone_owner
                await db.commit()
                event_type = "client_profile_reactivated" if was_deleted else "client_profile_linked"
                event_message = (
                    f"Клиент повторно зарегистрирован через Telegram: {client.name}, телефон: {phone}"
                    if was_deleted else
                    f"Telegram привязан к существующему клиенту: {client.name}, телефон: {phone}"
                )
                await _log_event(
                    db, event_type, event_message, client_id=client.id, source="telegram",
                )
            elif client:
                client.phone = phone
                await db.commit()
            else:
                data = await state.get_data()
                referral_code = data.get("referral_code")
                referred_by = None
                if referral_code:
                    referrer = (await db.execute(
                        select(Client).where(
                            Client.referral_code == referral_code,
                            Client.is_deleted == False,
                        )
                    )).scalar_one_or_none()
                    if referrer:
                        referred_by = referrer.id
                client = Client(
                    telegram_id=str(message.from_user.id), name=data["client_name"],
                    phone=phone, referral_code=str(message.from_user.id),
                    referred_by_client_id=referred_by,
                )
                db.add(client)
                await db.flush()
                if referred_by:
                    db.add(ReferralRecord(referrer_client_id=referred_by, referred_client_id=client.id))
                await db.commit()
                await _log_event(db, "new_client", f"Новый клиент зарегистрирован: {data['client_name']}, телефон: {phone}", client_id=client.id, source="telegram")
    except Exception:
        logger.exception("Could not link Telegram client by phone %s", phone)
        await message.answer("Не удалось привязать номер к профилю. Попробуйте ещё раз чуть позже.", reply_markup=MAIN_KEYBOARD)
        return

    try:
        await _finalize_booking(message, state, message.from_user.id, client)
    except Exception:
        logger.exception("Could not create Telegram booking after phone link for %s", phone)
        await message.answer("Номер привязан к вашему профилю, но заявку не удалось создать. Выберите время ещё раз.", reply_markup=MAIN_KEYBOARD)


@router.message(F.text == "📋 Мои записи")
async def my_bookings(message: Message):
    async with async_session() as db:
        result = await db.execute(
            select(Client).where(
                Client.telegram_id == str(message.from_user.id),
                Client.is_deleted == False,
            )
        )
        client = result.scalar_one_or_none()
        if not client:
            await message.answer("У вас пока нет записей. Нажмите «Записаться».", reply_markup=MAIN_KEYBOARD)
            return

        bookings_result = await db.execute(
            select(Booking).options(
                selectinload(Booking.certificate),
                selectinload(Booking.instructor)
            ).where(
                and_(
                    Booking.client_id == client.id,
                    Booking.status.in_(["pending", "reschedule_pending", "planned", "confirmed"]),
                )
            ).order_by(Booking.booking_date, Booking.start_time)
        )
        bookings = bookings_result.scalars().all()
        package_ids = {booking.package_id for booking in bookings if booking.package_id}
        package_progress_by_id = {}
        if package_ids:
            purchases = (await db.execute(
                select(ClientPackage).options(selectinload(ClientPackage.package)).where(
                    ClientPackage.client_id == client.id,
                    ClientPackage.package_id.in_(package_ids),
                )
            )).scalars().all()
            for purchase in purchases:
                total = max(0, purchase.package.sessions_count if purchase.package else 0)
                remaining = max(0, purchase.remaining_sessions or 0)
                package_progress_by_id[purchase.package_id] = (
                    max(0, total - remaining), total, remaining,
                )

    if not bookings:
        await message.answer("У вас нет активных записей.", reply_markup=MAIN_KEYBOARD)
        return

    status_labels = {
        "pending": "⏳ Ожидает подтверждения",
        "planned": "📋 Запланирована",
        "confirmed": "✅ Подтверждена",
    }
    
    for b in bookings:
        service_label = "Обучение вождению" if b.service_type == ServiceType.TRAINING else "Пробный экзамен"
        trans_label = "Механика" if b.transmission == "manual" else "Автомат"
        
        cert_line = ""
        if b.certificate_id:
            cert_line = "\n🎟️ Оплачено сертификатом"
        
        ref_line = ""
        if b.referral_discount_amount and b.referral_discount_amount > 0:
            ref_line = f"\n🎁 Скидка за друга: −{b.referral_discount_amount} ₸"

        package_line = ""
        if b.package_id:
            package_progress = package_progress_by_id.get(b.package_id)
            if package_progress:
                used, total, remaining = package_progress
                package_line = (
                    f"\n📦 Пакет: использовано {used}/{total}"
                    f"\n📦 Осталось занятий по пакету: {remaining} из {total}"
                )
        
        instructor_name = b.instructor.name if b.instructor else "—"
        instructor_trans = b.instructor.transmission if b.instructor else "—"
        instructor_trans_label = {"manual": "Механика", "automatic": "Автомат", "both": "Механика и автомат"}.get(instructor_trans, instructor_trans)
        instructor_exp = b.instructor.experience_years if b.instructor else 0

        number_line = f"📋 Номер записи: {b.booking_number}\n" if b.booking_number else ""
        cash_note = " (оплата наличными или через Kaspi QR)" if b.price > 0 and not (b.certificate_id or b.package_id) else ""
        text = (
            f"{status_labels.get(b.status, b.status)}\n\n"
            f"{number_line}"
            f"📍 {b.location}\n"
            f"📅 {b.booking_date} 🕐 {b.start_time.strftime('%H:%M')}\n"
            f"🚗 {service_label} ({trans_label})\n"
            f"💰 {b.price} ₸{cash_note}"
            f"{package_line}{cert_line}{ref_line}\n\n"
            f"👨‍🏫 **{instructor_name}**\n"
            f"⚙️ {instructor_trans_label}\n"
            f"📅 Стаж: {instructor_exp} лет"
        )

        # Если оплачено сертификатом — нельзя отменить, только перенести
        if b.certificate_id:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Перенести", callback_data=f"reschedule:{b.id}")],
            ])
        else:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отменить", callback_data=f"cancel:{b.id}")],
                [InlineKeyboardButton(text="🔄 Перенести", callback_data=f"reschedule:{b.id}")],
            ])
        await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("cancel:"))
async def cancel_booking(callback: CallbackQuery):
    booking_id = int(callback.data.split(":")[1])
    async with async_session() as db:
        result = await db.execute(select(Booking).where(Booking.id == booking_id))
        booking = result.scalar_one_or_none()
        if booking and str(callback.from_user.id) == (
            await db.execute(select(Client.telegram_id).where(Client.id == booking.client_id))
        ).scalar_one_or_none():
            try:
                await _ensure_client_is_not_blocked(db, booking.client_id)
            except ValueError as error:
                await callback.answer(str(error), show_alert=True)
                return
            # Клиент не может отменить запись с сертификатом
            if booking.certificate_id or (booking.certificate_amount or 0) > 0:
                await callback.message.edit_text(
                    "❌ Запись с сертификатом нельзя отменить.\n\n"
                    "Вы можете перенести её на другое время через кнопку 🔄 Перенести или "
                    "обратиться в поддержку.",
                    reply_markup=None,
                )
                return
            
            booking.cancellation_previous_status = booking.status
            booking.status = "cancellation_pending"
            await db.commit()

            client_result = await db.execute(select(Client).where(Client.id == booking.client_id))
            client = client_result.scalar_one_or_none()
            client_name = client.name if client else "Клиент"
            await _log_event(
                db,
                "booking_cancellation_requested",
                (f"Клиент «{client_name}» запросил отмену записи на "
                 f"{booking.booking_date.strftime('%d.%m.%Y')} в {booking.start_time.strftime('%H:%M')}."),
                client_id=booking.client_id,
                instructor_id=booking.instructor_id,
                booking_id=booking.id,
            )

            # Уведомление инструктору об отмене
            instructor_result = await db.execute(select(Instructor).where(Instructor.id == booking.instructor_id))
            instructor = instructor_result.scalar_one_or_none()
            if instructor and instructor.telegram_id:
                try:
                    notify_bot = instructor_bot if instructor_bot else callback.bot
                    await notify_bot.send_message(
                        int(instructor.telegram_id),
                        f"⏳ Клиент запросил отмену записи.\n\n"
                        f"{instructor.name}, ожидается подтверждение администратора.\n\n"
                        f"📅 {booking.booking_date.strftime('%d.%m.%Y')} в {booking.start_time.strftime('%H:%M')}\n"
                        f"📍 {booking.location}\n"
                        f"Коробка: {'Механика' if booking.transmission == 'manual' else 'Автомат'}",
                    )
                except Exception as e:
                    logger.error(f"Failed to notify instructor {instructor.id} about cancellation: {e}")

            await callback.message.edit_text(
                "⏳ Ваша заявка на отмену находится в обработке.\n\nЕсли вы нажали кнопку случайно, отмените это действие ниже.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="↩️ Отменить отмену записи", callback_data=f"cancel_revoke:{booking_id}")
                ]]),
            )
        else:
            await callback.answer("Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("cancel_revoke:"))
async def revoke_cancel_booking(callback: CallbackQuery):
    booking_id = int(callback.data.split(":")[1])
    async with async_session() as db:
        booking = await db.get(Booking, booking_id)
        if not booking or booking.status != "cancellation_pending":
            await callback.answer("Заявка уже обработана", show_alert=True)
            return
        client = await db.get(Client, booking.client_id)
        telegram_id = client.telegram_id if client else None
        if str(callback.from_user.id) != str(telegram_id):
            await callback.answer("Ошибка", show_alert=True)
            return
        booking.status = booking.cancellation_previous_status or "confirmed"
        booking.cancellation_previous_status = None
        await _log_event(
            db,
            "booking_cancellation_revoked",
            f"Клиент «{client.name}» отозвал заявку на отмену записи.",
            client_id=booking.client_id,
            instructor_id=booking.instructor_id,
            booking_id=booking.id,
        )
        await callback.message.edit_text("✅ Заявка на отмену отозвана. Ваша запись сохранена.")


@router.callback_query(F.data.startswith("reschedule:"))
async def start_reschedule(callback: CallbackQuery, state: FSMContext):
    booking_id = int(callback.data.split(":")[1])
    async with async_session() as db:
        result = await db.execute(
            select(Booking).options(selectinload(Booking.client)).where(Booking.id == booking_id)
        )
        booking = result.scalar_one_or_none()
        if not booking or str(callback.from_user.id) != (
            await db.execute(select(Client.telegram_id).where(Client.id == booking.client_id))
        ).scalar_one_or_none():
            await callback.answer("Ошибка", show_alert=True)
            return
        if booking.status not in ("planned", "confirmed"):
            await callback.answer("Перенос доступен только для подтверждённой записи.", show_alert=True)
            return
        try:
            await _ensure_client_is_not_blocked(db, booking.client_id)
        except ValueError as error:
            await callback.answer(str(error), show_alert=True)
            return
        await state.update_data(reschedule_booking_id=booking_id)
        buttons = await _build_date_buttons(db, booking.service_type, booking.transmission, prefix="resch_date")

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text("Выберите новую дату:", reply_markup=kb)
    await state.set_state(RescheduleStates.choosing_date)


@router.callback_query(RescheduleStates.choosing_date, F.data.startswith("resch_date:"))
async def reschedule_choose_date(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    parts = callback.data.split(":", 1)
    date_str = parts[1] if len(parts) > 1 else ""
    try:
        new_date = datetime.strptime(date_str, "%d.%m.%Y").date()
    except ValueError:
        await callback.message.edit_text("Ошибка в дате. Попробуйте снова:")
        return
    today_kz = datetime.now(TIMEZONE).date()
    if new_date < today_kz:
        await callback.message.edit_text("Дата не может быть в прошлом.")
        return

    data = await state.get_data()
    booking_id = data["reschedule_booking_id"]

    async with async_session() as db:
        result = await db.execute(select(Booking).where(Booking.id == booking_id))
        booking = result.scalar_one_or_none()
        if not booking:
            await callback.message.edit_text("Запись не найдена.")
            await state.clear()
            return
        slots = await get_available_slots_for_instructor(
            db, new_date, booking.service_type, booking.transmission,
            booking.location, booking.instructor_id,
            preserve_existing_assignment=True,
        )

    if not slots:
        async with async_session() as db:
            buttons = await _build_date_buttons(db, booking.service_type, booking.transmission, prefix="resch_date")
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)
        await callback.message.edit_text("Нет свободных слотов на эту дату. Выберите другую:", reply_markup=kb)
        return

    await state.update_data(reschedule_new_date=str(new_date))
    buttons = []
    for slot in slots[:12]:
        buttons.append([InlineKeyboardButton(
            text=slot.strftime("%H:%M"),
            callback_data=f"resch_time:{slot.strftime('%H:%M')}"
        )])
    kb = _kb_with_back(buttons)
    await callback.message.edit_text("Выберите новое время:", reply_markup=kb)
    await state.set_state(RescheduleStates.choosing_time)


@router.callback_query(RescheduleStates.choosing_time, F.data.startswith("resch_time:"))
async def reschedule_choose_time(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    chosen_time = callback.data.split(":", 1)[1]

    data = await state.get_data()
    booking_id = data["reschedule_booking_id"]
    new_date = date.fromisoformat(data["reschedule_new_date"])
    new_start = time.fromisoformat(chosen_time)

    async with async_session() as db:
        result = await db.execute(select(Booking).where(Booking.id == booking_id))
        booking = result.scalar_one_or_none()
        if not booking:
            await callback.message.edit_text("Запись не найдена.")
            await state.clear()
            return

        duration = settings.TRAINING_DURATION_MINUTES if booking.service_type == ServiceType.TRAINING else settings.EXAM_DURATION_MINUTES
        et = timedelta(hours=new_start.hour, minutes=new_start.minute) + timedelta(minutes=duration)
        new_end = time(int(et.total_seconds() // 3600), int((et.total_seconds() % 3600) // 60))

        # Проверяем что ТЕКУЩИЙ инструктор свободен в новое время
        # (не ищем нового — инструктор не меняется)
        current_instructor_id = booking.instructor_id

        # Проверяем конфликты у текущего инструктора (Booking + MobileBooking)
        conflict_result = await db.execute(
            select(Booking).where(
                and_(
                    Booking.instructor_id == current_instructor_id,
                    Booking.booking_date == new_date,
                    Booking.id != booking.id,
                    Booking.status.in_(["pending", "reschedule_pending", "planned", "confirmed", "in_progress"]),
                    Booking.start_time < new_end,
                    Booking.end_time > new_start,
                )
            )
        )
        conflict = conflict_result.scalar_one_or_none()
        if not conflict:
            mobile_conflict_result = await db.execute(
                select(MobileBooking).where(
                    and_(
                        MobileBooking.instructor_id == current_instructor_id,
                        MobileBooking.booking_date == new_date,
                        MobileBooking.status.in_(["pending", "planned", "confirmed"]),
                        MobileBooking.start_time < new_end,
                        MobileBooking.end_time > new_start,
                    )
                )
            )
            conflict = mobile_conflict_result.scalar_one_or_none()
        if conflict:
            # У текущего инструктора занято — сообщаем клиенту
            buttons = []
            slots = await get_available_slots_for_instructor(
                db, new_date, booking.service_type, booking.transmission,
                booking.location, current_instructor_id,
                preserve_existing_assignment=True,
            )
            for slot in slots[:12]:
                buttons.append([InlineKeyboardButton(
                    text=slot.strftime("%H:%M"),
                    callback_data=f"resch_time:{slot.strftime('%H:%M')}"
                )])
            kb = _kb_with_back(buttons) if buttons else None
            await callback.message.edit_text(
                "На это время у вашего инструктора уже есть запись. Выберите другое время:",
                reply_markup=kb,
            )
            return

        client_result = await db.execute(select(Client).where(Client.id == booking.client_id))
        client = client_result.scalar_one_or_none()
        client_name = client.name if client else "Клиент"
        try:
            client, reschedule_count = await _consume_client_reschedule_slot(db, booking.client_id)
        except ValueError as error:
            await callback.message.edit_text(
                str(error)
            )
            await state.clear()
            return

        booking.reschedule_previous_status = booking.status
        booking.requested_reschedule_date = new_date
        booking.requested_reschedule_start_time = new_start
        booking.requested_reschedule_end_time = new_end
        booking.reschedule_requested_at = datetime.now(TIMEZONE).replace(tzinfo=None)
        booking.status = "reschedule_pending"
        await _log_event(
            db,
            "booking_reschedule_requested",
            (f"Клиент «{client_name}» запросил перенос записи на "
             f"{new_date.strftime('%d.%m.%Y')} в {chosen_time}."),
            client_id=booking.client_id,
            instructor_id=booking.instructor_id,
            booking_id=booking.id,
        )
        if reschedule_count == 2:
            warning = (
                "⚠️ Внимание: это второй перенос за последние 24 часа. Вы можете отправить ещё одну заявку; "
                "после третьего переноса новые самостоятельные переносы будут недоступны до окончания 24-часового периода."
            )
        else:
            warning = None

    service_label = "Обучение вождению" if booking.service_type == ServiceType.TRAINING else "Пробный экзамен"
    trans_label = "Механика" if booking.transmission == "manual" else "Автомат"
    await callback.message.edit_text(
        f"⏳ Заявка на перенос отправлена администратору.\n\n"
        f"После подтверждения мы сообщим о новой дате и времени.\n"
        f"Запрошено: 📅 {new_date.strftime('%d.%m.%Y')} в {chosen_time}\n"
        f"📍 {booking.location}\n"
        f"🚗 {service_label} ({trans_label})",
        reply_markup=None,
    )
    if warning:
        try:
            # Telegram-клиент должен увидеть предупреждение прямо в диалоге
            # с ботом, а не как служебное сообщение поддержки в приложении.
            await callback.bot.send_message(callback.from_user.id, warning)
        except Exception as error:
            logger.error("Failed to send reschedule warning to Telegram client: %s", error)
    await state.clear()


@router.message(F.text == "❓ FAQ")
async def faq_command(message: Message):
    async with async_session() as db:
        result = await db.execute(
            select(FAQItem).where(FAQItem.is_active == True).order_by(FAQItem.sort_order)
        )
        items = result.scalars().all()
    if not items:
        await message.answer("FAQ пока пуст.", reply_markup=MAIN_KEYBOARD)
        return
    for item in items:
        await message.answer(f"❓ {item.question}\n💡 {item.answer}")


@router.message(F.text == "ℹ️ Как записаться")
async def how_to_book(message: Message):
    await message.answer(
        "Как записаться:\n\n"
        "1. Нажмите «Записаться».\n"
        "2. Выберите услугу: вождение или пробный экзамен.\n"
        "3. Для вождения выберите КПП: механика или автомат.\n"
        "4. Выберите дату и свободное время.\n"
        "5. Подтвердите запись.\n\n"
        "Инструктор назначается автоматически из свободных и подходящих по КПП. "
        "Занятия и пробный экзамен проходят на Циолковского 30.",
        reply_markup=MAIN_KEYBOARD,
    )


@router.message(F.text == "📞 Контакты")
async def contacts(message: Message):
    await message.answer(
        "📞 Телефон: +77027182233\n"
        "📍 Занятия и пробный экзамен: Циолковского 30\n"
        "🤖 Бот: https://t.me/nomadrive_bot",
        reply_markup=MAIN_KEYBOARD,
    )


SUPPORT_CANCEL_KB = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="❌ Завершить чат")]],
    resize_keyboard=True,
)


def _is_support_chat_open(client: Client) -> bool:
    return bool(client.support_chat_opened_at and not client.support_chat_closed_at)


def _open_support_chat(client: Client, now: datetime) -> None:
    if not _is_support_chat_open(client):
        client.support_chat_opened_at = now
        client.support_chat_closed_at = None


def _close_support_chat(client: Client, now: datetime) -> bool:
    if not _is_support_chat_open(client):
        return False
    client.support_chat_closed_at = now
    return True


async def _save_client_support_message(
    db: AsyncSession, client: Client, text: str,
) -> bool:
    """Сохраняет входящее обращение клиента и применяет общий лимит сообщений."""
    recent = (await db.execute(
        select(func.count()).select_from(SupportMessage).where(
            SupportMessage.client_id == client.id,
            SupportMessage.sender == "user",
            SupportMessage.created_at >= datetime.now(TIMEZONE).replace(tzinfo=None) - timedelta(minutes=1),
        )
    )).scalar() or 0
    if recent >= 5:
        return False

    db.add(SupportMessage(
        client_id=client.id,
        channel="telegram",
        sender="user",
        text=text,
        is_admin_read=False,
    ))
    db.add(Event(
        event_type="client_support_message",
        source="telegram",
        client_id=client.id,
        message=f"Клиент «{client.name}» написал в поддержку.",
    ))
    await db.commit()
    return True


@router.message(F.text == "💬 Поддержка")
async def support_start(message: Message, state: FSMContext):
    async with async_session() as db:
        client = (await db.execute(select(Client).where(
            Client.telegram_id == str(message.from_user.id),
            Client.is_deleted == False,
        ))).scalar_one_or_none()
        if not client:
            await message.answer("Сначала зарегистрируйтесь, чтобы использовать поддержку.", reply_markup=MAIN_KEYBOARD)
            return
        _open_support_chat(client, datetime.now(TIMEZONE).replace(tzinfo=None))
        await db.commit()
        await state.set_state(SupportStates.writing_message)
        await message.answer(
            "💬 <b>Чат с администратором</b>\n\n"
            "Напишите ваше сообщение, и администратор ответит вам.\n"
            "Для завершения чата нажмите «❌ Завершить чат».",
            reply_markup=SUPPORT_CANCEL_KB,
            parse_mode="HTML",
        )


@router.message(F.text == "❌ Завершить чат")
async def support_end(message: Message, state: FSMContext):
    async with async_session() as db:
        client = (await db.execute(select(Client).where(
            Client.telegram_id == str(message.from_user.id),
            Client.is_deleted == False,
        ))).scalar_one_or_none()
        if client:
            closed = _close_support_chat(client, datetime.now(TIMEZONE).replace(tzinfo=None))
            if closed:
                db.add(Event(
                    event_type="client_support_closed",
                    source="telegram",
                    client_id=client.id,
                    message=f"Клиент «{client.name}» завершил чат поддержки.",
                ))
            await db.commit()
    await state.clear()
    await message.answer("Чат завершён.", reply_markup=MAIN_KEYBOARD)


@router.message(SupportStates.writing_message)
async def support_send_message(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("Можно отправлять только текстовые сообщения.", reply_markup=SUPPORT_CANCEL_KB)
        return
    if len(message.text) > 2000:
        await message.answer("Сообщение слишком длинное. Максимум 2000 символов.", reply_markup=SUPPORT_CANCEL_KB)
        return
    if message.text and message.text.startswith("/"):
        await state.clear()
        return
    async with async_session() as db:
        client = (await db.execute(select(Client).where(
            Client.telegram_id == str(message.from_user.id),
            Client.is_deleted == False,
        ))).scalar_one_or_none()
        if not client:
            await state.clear()
            await message.answer("Ошибка авторизации.", reply_markup=MAIN_KEYBOARD)
            return
        if not _is_support_chat_open(client):
            await state.clear()
            await message.answer(
                "Чат с администратором завершён. Нажмите «💬 Поддержка», чтобы начать новый.",
                reply_markup=MAIN_KEYBOARD,
            )
            return
        saved = await _save_client_support_message(db, client, message.text)
    if not saved:
        await message.answer("Слишком много сообщений. Подождите минуту.", reply_markup=SUPPORT_CANCEL_KB)
        return
    await message.answer("✅ Сообщение отправлено. Ожидайте ответа администратора.", reply_markup=SUPPORT_CANCEL_KB)


@router.message(F.text == "📚 История обучения")
async def learning_history(message: Message):
    async with async_session() as db:
        client = await _get_client_by_telegram(str(message.from_user.id))
        if not client:
            await message.answer("Сначала запишитесь на занятие.", reply_markup=MAIN_KEYBOARD)
            return
        bookings, has_more = await _learning_history_page(db, client.id, page=1)
        if not bookings:
            await message.answer("У вас пока нет истории обучения.", reply_markup=MAIN_KEYBOARD)
            return
        await _send_learning_history_page(message, bookings, page=1, has_more=has_more)


async def _learning_history_page(db, client_id: int, page: int) -> tuple[list[Booking], bool]:
    page_size = 7
    safe_page = max(1, page)
    now = datetime.now(TIMEZONE).replace(tzinfo=None)
    rows = (await db.execute(
        select(Booking)
        .options(selectinload(Booking.instructor), selectinload(Booking.certificate))
        .where(
            Booking.client_id == client_id,
            or_(
                Booking.booking_date < now.date(),
                and_(Booking.booking_date == now.date(), Booking.start_time < now.time()),
                Booking.status.in_(["completed", "cancelled", "no_show"]),
            ),
        )
        .order_by(Booking.booking_date.desc(), Booking.start_time.desc(), Booking.id.desc())
        .offset((safe_page - 1) * page_size)
        .limit(page_size + 1)
    )).scalars().all()
    return rows[:page_size], len(rows) > page_size


def _learning_history_text(booking: Booking) -> str:
    status_labels = {
        "planned": "📋 Запланирована", "confirmed": "✅ Подтверждена",
        "in_progress": "🔄 В процессе", "completed": "🎉 Завершена",
        "cancelled": "❌ Отменена", "no_show": "🚫 Неявка",
    }
    service_label = "🚗 Обучение вождению" if booking.service_type == ServiceType.TRAINING else "🏁 Пробный экзамен"
    trans_label = "Механика" if booking.transmission == "manual" else "Автомат"
    lines = [
        status_labels.get(booking.status, booking.status), "",
        f"📅 {booking.booking_date.strftime('%d.%m.%Y')}",
        f"🕐 {booking.start_time.strftime('%H:%M')} - {booking.end_time.strftime('%H:%M')}",
        f"📍 {booking.location}", f"{service_label} ({trans_label})",
    ]
    if booking.instructor:
        lines.append(f"👨‍🏫 Инструктор: {booking.instructor.name}")
    if booking.base_price:
        lines.append(f"💵 Стоимость: {booking.base_price} ₸")
        if booking.certificate_amount > 0:
            lines.append(f"🎟️ Сертификат: −{booking.certificate_amount} ₸")
        if booking.referral_discount_amount > 0:
            lines.append(f"🎁 Скидка за друга: −{booking.referral_discount_amount} ₸")
        lines.append(f"💰 Итого к оплате: {booking.price} ₸" if booking.price > 0 else "💰 Итого: 0 ₸ (бесплатно)")
    if booking.certificate_id:
        lines.append("🎟️ Занятие ОПЛАЧЕНО СЕРТИФИКАТОМ")
    return "\n".join(lines)


async def _send_learning_history_page(message: Message, bookings: list[Booking], page: int, has_more: bool) -> None:
    for booking in bookings:
        await message.answer(_learning_history_text(booking), reply_markup=MAIN_KEYBOARD)
    if has_more:
        await message.answer(
            "Показаны записи по 7. Для следующей страницы нажмите кнопку.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="Загрузить ещё", callback_data=f"learning_history:{page + 1}")
            ]]),
        )


@router.callback_query(F.data.startswith("learning_history:"))
async def learning_history_more(callback: CallbackQuery):
    try:
        page = max(1, int(callback.data.rsplit(":", 1)[1]))
    except (AttributeError, ValueError):
        await callback.answer("Не удалось определить страницу истории.", show_alert=True)
        return
    async with async_session() as db:
        client = await _get_client_by_telegram(str(callback.from_user.id))
        if not client:
            await callback.answer("Сначала запишитесь на занятие.", show_alert=True)
            return
        bookings, has_more = await _learning_history_page(db, client.id, page)
        if not bookings:
            await callback.answer("Больше записей нет.")
            await callback.message.edit_reply_markup(reply_markup=None)
            return
        await callback.message.edit_reply_markup(reply_markup=None)
        await _send_learning_history_page(callback.message, bookings, page=page, has_more=has_more)
    await callback.answer()


async def send_lesson_reminders(bot: Bot):
    """
    Отправляет клиентам напоминания о занятиях:
      — за 24 часа: простое текстовое напоминание
      — за 1 час:   запрос подтверждения (Да/Нет)
    Запускается планировщиком каждую минуту.
    """
    async with async_session() as db:
        now = datetime.now(TIMEZONE)
        today = now.date()
        tomorrow = today + timedelta(days=1)

        # ── Напоминание за 24 часа ────────────────────────────────────────
        # Целевое время: сейчас + ~24ч (окно ±5 минут)
        target_24h = (now + timedelta(hours=24)).time()
        window_start_24h = (now + timedelta(hours=24)).time()
        window_end_dt = now + timedelta(hours=24, minutes=5)
        window_end_24h = window_end_dt.time()

        result_24h = await db.execute(
            select(Booking).where(
                and_(
                    Booking.booking_date == tomorrow,
                    Booking.start_time >= window_start_24h,
                    Booking.start_time < window_end_24h,
                    Booking.status.in_(["pending", "reschedule_pending", "planned", "confirmed"]),
                    Booking.reminder_24h_sent == False,
                )
            )
        )
        for booking in result_24h.scalars().all():
            client_result = await db.execute(
                select(Client).where(Client.id == booking.client_id)
            )
            client = client_result.scalar_one_or_none()
            if not client or not client.telegram_id:
                continue
            service_label = "занятие" if booking.service_type == ServiceType.TRAINING else "пробный экзамен"
            try:
                await bot.send_message(
                    int(client.telegram_id),
                    f"🔔 {client.name}, напоминаем!\n\n"
                    f"Завтра у вас {service_label}:\n"
                    f"📅 {booking.booking_date.strftime('%d.%m.%Y')}\n"
                    f"🕐 {booking.start_time.strftime('%H:%M')}\n"
                    f"📍 {booking.location}\n\n"
                    "💵 Оплатить занятие можно наличными или через Kaspi QR.\n\n"
                    f"Ждём вас! 🚗",
                )
                booking.reminder_24h_sent = True
                await db.commit()
                await _log_event(db, "reminder_sent", f"24ч: Запись #{booking.id}, Клиент: {client.name}")
            except Exception as e:
                logger.error(f"Failed to send 24h reminder to {client.telegram_id}: {e}")

        # ── Напоминание за 1 час ──────────────────────────────────────────
        # Только для записей у которых ещё НЕ было отправлено confirmation
        target_1h = (now + timedelta(hours=1)).time()
        end_1h_dt = now + timedelta(hours=1, minutes=5)
        window_end_1h = end_1h_dt.time()

        result_1h = await db.execute(
            select(Booking).where(
                and_(
                    Booking.booking_date == today,
                    Booking.start_time >= target_1h,
                    Booking.start_time < window_end_1h,
                    Booking.status.in_(["pending", "reschedule_pending", "planned", "confirmed"]),
                    Booking.reminder_1h_sent == False,
                    Booking.confirmation_sent == False,
                )
            )
        )
        for booking in result_1h.scalars().all():
            client_result = await db.execute(
                select(Client).where(Client.id == booking.client_id)
            )
            client = client_result.scalar_one_or_none()
            if not client or not client.telegram_id:
                continue
            service_label = "занятие" if booking.service_type == ServiceType.TRAINING else "пробный экзамен"
            try:
                await bot.send_message(
                    int(client.telegram_id),
                    f"⏰ {client.name}, через час ваше {service_label}!\n\n"
                    f"🕐 {booking.start_time.strftime('%H:%M')}\n"
                    f"📍 {booking.location}\n\n"
                    "💵 Оплатить занятие можно наличными или через Kaspi QR.\n\n"
                    f"Пора собираться! 🚗",
                )
                booking.reminder_1h_sent = True
                await db.commit()
                await _log_event(db, "reminder_sent", f"1ч: Запись #{booking.id}, Клиент: {client.name}")
            except Exception as e:
                logger.error(f"Failed to send 1h reminder to {client.telegram_id}: {e}")

        # ── Напоминание инструктору за 10 минут ──────────────────────────
        target_10min = (now + timedelta(minutes=10)).time()
        end_10min_dt = now + timedelta(minutes=15)
        window_end_10min = end_10min_dt.time()

        result_10min = await db.execute(
            select(Booking).where(
                and_(
                    Booking.booking_date == today,
                    Booking.start_time >= target_10min,
                    Booking.start_time < window_end_10min,
                    Booking.status.in_(["pending", "reschedule_pending", "planned", "confirmed"]),
                    Booking.reminder_10min_sent == False,
                )
            )
        )
        for booking in result_10min.scalars().all():
            instructor_result = await db.execute(
                select(Instructor).where(Instructor.id == booking.instructor_id)
            )
            instructor = instructor_result.scalar_one_or_none()
            if not instructor or not instructor.telegram_id:
                booking.reminder_10min_sent = True
                await db.commit()
                continue
            client_result = await db.execute(
                select(Client).where(Client.id == booking.client_id)
            )
            client = client_result.scalar_one_or_none()
            trans_label = "Механика" if booking.transmission == "manual" else "Автомат"
            client_name = client.name if client else "—"
            client_phone = f" ({client.phone})" if client and client.phone else ""
            try:
                notify_bot = instructor_bot if instructor_bot else bot
                await notify_bot.send_message(
                    int(instructor.telegram_id),
                    f"⏰ Через 10 минут занятие!\n\n"
                    f"📅 {booking.booking_date.strftime('%d.%m.%Y')} в {booking.start_time.strftime('%H:%M')}\n"
                    f"👤 {client_name}{client_phone}\n"
                    f"📍 {booking.location}\n"
                    f"Коробка: {trans_label}\n"
                    f"💰 К оплате: {booking.price} ₸",
                )
                booking.reminder_10min_sent = True
                await db.commit()
                await _log_event(db, "reminder_sent", f"10мин инструктору: Запись #{booking.id}, Инструктор: {instructor.name}")
            except Exception as e:
                logger.error(f"Failed to send 10min reminder to instructor {instructor.id}: {e}")


async def send_confirmation_reminders(bot: Bot):
    async with async_session() as db:
        now = datetime.now(TIMEZONE)
        target_time = (now + timedelta(hours=1)).time()
        today = now.date()

        result = await db.execute(
            select(Booking).where(
                and_(
                    Booking.booking_date == today,
                    Booking.start_time >= target_time,
                    Booking.start_time < (time(target_time.hour, target_time.minute + 5) if target_time.minute < 55 else time(target_time.hour + 1, 0)),
                    Booking.status == "planned",
                    Booking.confirmation_sent == False,
                )
            )
        )
        bookings = result.scalars().all()

        for booking in bookings:
            client_result = await db.execute(select(Client).where(Client.id == booking.client_id))
            client = client_result.scalar_one_or_none()
            if client:
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="✅ Да, я приду", callback_data=f"confirm_yes:{booking.id}")],
                    [InlineKeyboardButton(text="❌ Нет, не получается", callback_data=f"confirm_no:{booking.id}")],
                ])
                try:
                    await bot.send_message(
                        int(client.telegram_id),
                        f"Здравствуйте, {client.name}! Напоминаем, у вас занятие сегодня в {booking.start_time}. "
                        f"Если у вас не получается прийти — сообщите нам.",
                        reply_markup=kb,
                    )
                    booking.confirmation_sent = True
                    await db.commit()
                    await _log_event(db, "confirmation_sent", f"Запись #{booking.id}, Клиент: {client.name}, Время: {booking.start_time}")
                except Exception as e:
                    logger.error(f"Failed to send confirmation to {client.telegram_id}: {e}")


@router.callback_query(F.data.startswith("confirm_yes:"))
async def confirm_yes(callback: CallbackQuery):
    booking_id = int(callback.data.split(":")[1])
    async with async_session() as db:
        result = await db.execute(select(Booking).where(Booking.id == booking_id))
        booking = result.scalar_one_or_none()
        if booking:
            client = await db.get(Client, booking.client_id)
            booking.confirmed_by_client = True
            booking.status = "confirmed"
            await _log_event(
                db,
                "booking_attendance_confirmed",
                f"Клиент «{client.name if client else 'Клиент'}» подтвердил, что придёт на занятие.",
                client_id=booking.client_id,
                instructor_id=booking.instructor_id,
                booking_id=booking.id,
            )
    await callback.message.edit_text("✅ Спасибо! Ждём вас на занятии.", reply_markup=None)


@router.callback_query(F.data.startswith("confirm_no:"))
async def confirm_no(callback: CallbackQuery):
    booking_id = int(callback.data.split(":")[1])
    async with async_session() as db:
        result = await db.execute(select(Booking).where(Booking.id == booking_id))
        booking = result.scalar_one_or_none()
        if booking:
            try:
                await _ensure_client_is_not_blocked(db, booking.client_id)
            except ValueError as error:
                await callback.answer(str(error), show_alert=True)
                return
            booking.cancellation_previous_status = booking.status
            booking.status = "cancellation_pending"
            await db.commit()

            client_result = await db.execute(select(Client).where(Client.id == booking.client_id))
            client = client_result.scalar_one_or_none()
            client_name = client.name if client else "Клиент"
            await _log_event(
                db,
                "booking_cancellation_requested",
                (f"Клиент «{client_name}» сообщил, что не придёт, и запросил отмену записи "
                 f"на {booking.booking_date.strftime('%d.%m.%Y')} в {booking.start_time.strftime('%H:%M')}."),
                client_id=booking.client_id,
                instructor_id=booking.instructor_id,
                booking_id=booking.id,
            )

            # Уведомление инструктору об отмене
            instructor_result = await db.execute(select(Instructor).where(Instructor.id == booking.instructor_id))
            instructor = instructor_result.scalar_one_or_none()
            if instructor and instructor.telegram_id:
                try:
                    notify_bot = instructor_bot if instructor_bot else callback.bot
                    await notify_bot.send_message(
                        int(instructor.telegram_id),
                        f"⏳ Клиент запросил отмену записи.\n\n"
                        f"{instructor.name}, ожидается подтверждение администратора.\n\n"
                        f"📅 {booking.booking_date.strftime('%d.%m.%Y')} в {booking.start_time.strftime('%H:%M')}\n"
                        f"📍 {booking.location}\n"
                        f"Коробка: {'Механика' if booking.transmission == 'manual' else 'Автомат'}",
                    )
                except Exception as e:
                    logger.error(f"Failed to notify instructor {instructor.id} about cancellation: {e}")

    await callback.message.edit_text(
        "⏳ Ваша заявка на отмену находится в обработке.\n\nЕсли это случайное нажатие, отмените отмену ниже.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="↩️ Отменить отмену записи", callback_data=f"cancel_revoke:{booking_id}")
        ]]),
    )


async def send_rating_requests(bot: Bot):
    async with async_session() as db:
        rating_due_at = datetime.now(TIMEZONE).replace(tzinfo=None) - timedelta(hours=1)

        # The completion transition is the source of truth: it is written by
        # the instructor action or by the automatic lesson transition. A past
        # scheduled end alone does not prove that the lesson took place.
        result = await db.execute(
            select(Booking).where(
                and_(
                    Booking.status == "completed",
                    Booking.completed_at.is_not(None),
                    Booking.completed_at <= rating_due_at,
                    Booking.rating_sent == False,
                )
            )
        )
        bookings = result.scalars().all()

        for booking in bookings:
            client_result = await db.execute(select(Client).where(Client.id == booking.client_id))
            client = client_result.scalar_one_or_none()
            telegram_id = (client.telegram_id or "").strip() if client else ""
            if not telegram_id.isascii() or not telegram_id.isdigit():
                continue

            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="👍 Хорошо", callback_data=f"rate:good:{booking.id}")],
                [InlineKeyboardButton(text="😐 Нормально", callback_data=f"rate:normal:{booking.id}")],
                [InlineKeyboardButton(text="👎 Плохо", callback_data=f"rate:bad:{booking.id}")],
            ])
            try:
                await bot.send_message(
                    int(telegram_id),
                    "Оцените как прошло вождение — это очень важно для нас.\n\n"
                    "Оно полностью анонимно и ни на что не влияет.\n"
                    "Это только для повышения качества обслуживания.",
                    reply_markup=kb,
                )
                booking.rating_sent = True
                await db.commit()
                await _log_event(db, "rating_request_sent", f"Запись #{booking.id}, Клиент: {client.name}")
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to send rating to Telegram client %s: %s", client.id, exc)


@router.callback_query(F.data.startswith("rate:"))
async def process_rating(callback: CallbackQuery):
    parts = callback.data.split(":")
    vote_str = parts[1]
    booking_id = int(parts[2])

    vote_map = {"good": RatingVote.GOOD, "normal": RatingVote.NORMAL, "bad": RatingVote.BAD}
    vote = vote_map[vote_str]

    async with async_session() as db:
        if booking_id > 0:
            result = await db.execute(select(Booking).where(Booking.id == booking_id))
            booking = result.scalar_one_or_none()
            if not booking:
                await callback.answer("Запись не найдена", show_alert=True)
                return
            if booking.status != "completed":
                await callback.answer(
                    "Оценка доступна только после завершённого занятия",
                    show_alert=True,
                )
                return

            db.add(RatingRecord(
                booking_id=booking_id,
                instructor_id=booking.instructor_id,
                vote=vote,
            ))
            instructor_id = booking.instructor_id
        else:
            inst = await db.execute(select(Instructor).limit(1))
            instructor = inst.scalar_one_or_none()
            if not instructor:
                await callback.answer("Инструктор не найден", show_alert=True)
                return
            instructor_id = instructor.id

        inst_result = await db.execute(select(Instructor).where(Instructor.id == instructor_id))
        instructor = inst_result.scalar_one_or_none()
        if instructor:
            if vote == RatingVote.GOOD:
                instructor.rating = round(instructor.rating + settings.RATING_STEP, 1)
            elif vote == RatingVote.BAD:
                instructor.rating = round(max(settings.MIN_RATING, instructor.rating - settings.RATING_STEP), 1)

            if instructor.rating <= settings.MIN_RATING:
                already_notified = await db.execute(
                    select(NotificationSent).where(
                        and_(
                            NotificationSent.instructor_id == instructor.id,
                            NotificationSent.notification_type == "low_rating",
                        )
                    )
                )
                if not already_notified.scalar_one_or_none():
                    db.add(NotificationSent(
                        instructor_id=instructor.id,
                        notification_type="low_rating",
                    ))

        await db.commit()
        vote_labels = {"good": "Хорошо", "normal": "Нормально", "bad": "Плохо"}
        client = await db.get(Client, booking.client_id) if booking_id > 0 else None
        await _log_event(
            db,
            "rating_given",
            f"Клиент «{client.name if client else 'Клиент'}» оценил занятие: {vote_labels[vote_str]}.",
            client_id=booking.client_id if booking_id > 0 else None,
            instructor_id=instructor.id,
            booking_id=booking.id if booking_id > 0 else None,
        )

    labels = {"good": "👍 Хорошо", "normal": "😐 Нормально", "bad": "👎 Плохо"}
    await callback.message.edit_text(f"Спасибо за оценку: {labels[vote_str]}!", reply_markup=None)


@router.message(F.text == "🎁 Пригласи друга")
async def referral_command(message: Message):
    async with async_session() as db:
        result = await db.execute(select(Client).where(
            Client.telegram_id == str(message.from_user.id),
            Client.is_deleted == False,
        ))
        client = result.scalar_one_or_none()
        if not client:
            await message.answer("Сначала запишитесь на занятие, чтобы получить реферальную ссылку.", reply_markup=MAIN_KEYBOARD)
            return
        completed_lessons = (await db.execute(
            select(func.count()).select_from(Booking).where(
                Booking.client_id == client.id,
                Booking.status == "completed",
            )
        )).scalar() or 0
        if not completed_lessons:
            await message.answer(
                "Реферальная ссылка станет доступна после первого завершённого занятия.",
                reply_markup=MAIN_KEYBOARD,
            )
            return
        ref_count_result = await db.execute(
            select(func.count()).select_from(ReferralRecord).where(ReferralRecord.referrer_client_id == client.id)
        )
        ref_count = ref_count_result.scalar() or 0
    link = f"https://t.me/nomadrive_bot?start={client.referral_code}"
    await message.answer(
        f"🎁 <b>Реферальная программа</b>\n\n"
        f"Пригласите друга — и он получит скидку 1000 ₸ на первое занятие,\n"
        f"если введёт ваш код при регистрации!\n\n"
        f"Ваша ссылка:\n{link}\n\n"
        f"Приведено друзей: {ref_count}",
        parse_mode="HTML",
        reply_markup=MAIN_KEYBOARD,
    )


@router.message(F.text == "📦 Пакеты")
async def packages_command(message: Message):
    async with async_session() as db:
        result = await db.execute(select(Package).where(Package.is_active == True))
        packages = result.scalars().all()
        client_result = await db.execute(select(Client).where(
            Client.telegram_id == str(message.from_user.id),
            Client.is_deleted == False,
        ))
        client = client_result.scalar_one_or_none()
        my_packages_text = ""
        if client:
            cp_result = await db.execute(
                select(ClientPackage).where(and_(ClientPackage.client_id == client.id, ClientPackage.is_active == True))
            )
            cps = cp_result.scalars().all()
            if cps:
                lines = []
                for cp in cps:
                    pkg_result = await db.execute(select(Package).where(Package.id == cp.package_id))
                    pkg = pkg_result.scalar_one_or_none()
                    if pkg:
                        expiry = f", до {cp.expires_at.strftime('%d.%m.%Y')}" if cp.expires_at else ""
                        bonus = ", пробный экзамен доступен" if cp.remaining_bonus_exams > 0 else ""
                        code = f" ({pkg.code})" if pkg.code else ""
                        lines.append(f"• {pkg.name}{code}: осталось {cp.remaining_sessions} занятий{bonus}{expiry}")
                my_packages_text = "\n\n<b>Ваши активные пакеты:</b>\n" + "\n".join(lines)

    if not packages:
        await message.answer("Пакеты пока недоступны.", reply_markup=MAIN_KEYBOARD)
        return
    text = "<b>📦 Доступные пакеты:</b>\n\n"
    for p in packages:
        per_session = p.price // p.sessions_count if p.sessions_count else 0
        text += f"• <b>{p.name}</b>: {p.sessions_count} занятий за {p.price} ₸ ({per_session} ₸/занятие)\n"
    text += "\nДля покупки пакета свяжитесь с нами: +77027182233"
    text += my_packages_text
    await message.answer(text, parse_mode="HTML", reply_markup=MAIN_KEYBOARD)


@router.message(F.text == "🎟️ Сертификат")
async def certificate_command(message: Message, state: FSMContext):
    data = await state.get_data()
    cert_info = data.get("cert_info")
    if cert_info:
        await message.answer(
            f"У вас уже активирован сертификат:\n\n"
            f"{cert_info}\n\n"
            f"Он будет применён при следующей записи.\n"
            f"Если хотите активировать другой — введите новый код:",
        )
    else:
        await message.answer(
            "🎁 <b>Активация сертификата</b>\n\n"
            "Введите код подарочного сертификата, который вы получили.\n"
            "Номинал сертификата будет вычтен при записи на занятие.",
            parse_mode="HTML",
        )
    await state.set_state(CertificateStates.waiting_code)


@router.message(CertificateStates.waiting_code)
async def process_certificate(message: Message, state: FSMContext):
    code = message.text.strip().upper()

    if code.startswith("/") or len(code) < 4:
        await state.clear()
        await message.answer("Активация сертификата отменена.", reply_markup=MAIN_KEYBOARD)
        return

    async with async_session() as db:
        result = await db.execute(select(Certificate).where(Certificate.code == code))
        cert = result.scalar_one_or_none()
        if not cert:
            client_result = await db.execute(select(Client).where(
                Client.telegram_id == str(message.from_user.id),
                Client.is_deleted == False,
            ))
            client = client_result.scalar_one_or_none()
            if client:
                from app.models.models import CertificateRequest
                existing_request = (await db.execute(select(CertificateRequest).where(
                    CertificateRequest.client_id == client.id,
                    CertificateRequest.code_entered == code,
                    CertificateRequest.status == "pending",
                ))).scalar_one_or_none()
                if not existing_request:
                    db.add(CertificateRequest(
                        client_id=client.id,
                        code_entered=code,
                        matched_certificate_id=None,
                        status="pending",
                    ))
                    await db.commit()
                    await _log_event(
                        db, "certificate_activation_requested",
                        f"Клиент {client.name} подал заявку на подтверждение сертификата. Код: {code}",
                        client_id=client.id,
                    )
                await message.answer(
                    "⏳ <b>Ваш код принят и находится в обработке.</b>\n\n"
                    "Сертификат будет доступен после подтверждения администратором.",
                    reply_markup=MAIN_KEYBOARD,
                    parse_mode="HTML",
                )
                await state.clear()
                return
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="cert_retry")],
                [InlineKeyboardButton(text="◀️ Назад в меню", callback_data="cert_back")],
            ])
            await message.answer(
                f"❌ Сертификат с кодом <code>{code}</code> не найден.\n\n"
                "Проверьте код и попробуйте снова, или нажмите «Назад».",
                reply_markup=kb,
                parse_mode="HTML",
            )
            return

        if cert.is_used and cert.remaining <= 0:
            await message.answer(
                "❌ Этот сертификат уже полностью использован.",
                reply_markup=MAIN_KEYBOARD,
            )
            await state.clear()
            return

        client_result = await db.execute(select(Client).where(
            Client.telegram_id == str(message.from_user.id),
            Client.is_deleted == False,
        ))
        client = client_result.scalar_one_or_none()
        if not client:
            await message.answer(
                "Сначала запишитесь на занятие, чтобы активировать сертификат.\n"
                "Нажмите «Записаться» в главном меню.",
                reply_markup=MAIN_KEYBOARD,
            )
            await state.clear()
            return

        existing = await db.execute(
            select(Certificate).where(
                and_(
                    Certificate.activated_by_client_id == client.id,
                    Certificate.remaining > 0,
                )
            )
        )
        existing_cert = existing.scalar_one_or_none()
        if existing_cert and existing_cert.id != cert.id:
            await message.answer(
                f"У вас уже есть активный сертификат (<code>{existing_cert.code}</code>, "
                f"остаток {existing_cert.remaining} ₸).\n\n"
                f"Он будет применён при следующей записи. "
                f"Невозможно активировать второй сертификат одновременно.",
                reply_markup=MAIN_KEYBOARD,
                parse_mode="HTML",
            )
            await state.clear()
            return

        from app.models.models import CertificateRequest
        existing_request = (await db.execute(select(CertificateRequest).where(
            CertificateRequest.client_id == client.id,
            CertificateRequest.code_entered == code,
            CertificateRequest.status == "pending",
        ))).scalar_one_or_none()
        if not existing_request:
            db.add(CertificateRequest(
                client_id=client.id,
                code_entered=code,
                matched_certificate_id=cert.id,
                status="pending",
            ))
        await db.commit()
        await _log_event(
            db, "certificate_activation_requested",
            f"Клиент {client.name} подал заявку на подтверждение сертификата. Код: {cert.code}",
            client_id=client.id,
        )

    await message.answer(
        "⏳ <b>Ваш код принят и находится в обработке.</b>\n\n"
        "Сертификат будет доступен после подтверждения администратором.",
        reply_markup=MAIN_KEYBOARD,
        parse_mode="HTML",
    )
    await state.clear()


@router.callback_query(F.data == "cert_retry")
async def cert_retry(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_text("Введите код сертификата:")
    await state.set_state(CertificateStates.waiting_code)


@router.callback_query(F.data == "cert_back")
async def cert_back(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_text("Главное меню:", reply_markup=None)
    await state.clear()


async def notify_instructor(bot: Bot, booking: Booking, instructor: Instructor):
    if not instructor.telegram_id:
        return
    try:
        await bot.send_message(
            int(instructor.telegram_id),
            f"📌 Напоминание: у вас урок {booking.booking_date} в {booking.start_time}.\n"
            f"Клиент: {booking.client.name if booking.client else '—'}\n"
            f"Площадка: {booking.location}\n"
            f"Коробка: {booking.transmission}",
        )
    except Exception as e:
        logger.error(f"Failed to notify instructor {instructor.id}: {e}")


async def check_unconfirmed_bookings(bot: Bot):
    # Kept as a scheduler hook for compatibility. An unanswered client
    # confirmation is not a no-show: arrival/completion are now fixed by the
    # booked slot unless the client cancels or reschedules.
    return


@router.message(Command("test_rating"))
async def test_rating(message: Message):
    async with async_session() as db:
        inst = await db.execute(select(Instructor).limit(1))
        instructor = inst.scalar_one_or_none()
        if not instructor:
            await message.answer("Нет инструкторов в базе")
            return

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👍 Хорошо", callback_data=f"rate:good:0")],
            [InlineKeyboardButton(text="😐 Нормально", callback_data=f"rate:normal:0")],
            [InlineKeyboardButton(text="👎 Плохо", callback_data=f"rate:bad:0")],
        ])
        await message.answer(
            f"🧪 Тестовый опрос (инструктор: {instructor.name}, рейтинг: {instructor.rating})\n\n"
            "Оцените как прошло вождение — это очень важно для нас.\n\n"
            "Оно полностью анонимно и ни на что не влияет.\n"
            "Это только для повышения качества обслуживания.",
            reply_markup=kb,
        )


@router.message()
async def relay_telegram_support_reply(message: Message):
    """Передаёт в поддержку обычный ответ клиента после сообщения администратора.

    Специальные сценарии (запись, сертификат, кнопки меню и активные состояния)
    обрабатываются зарегистрированными выше хендлерами. Сюда попадает только
    неизвестный текст зарегистрированного клиента — в том числе ответ в уже
    открытом Telegram-диалоге после перезапуска бота, когда FSM-состояние
    поддержки больше недоступно.
    """
    if not message.text or message.text.startswith("/"):
        return
    text = message.text.strip()
    if not text:
        return
    if len(text) > 2000:
        await message.answer("Сообщение слишком длинное. Максимум 2000 символов.")
        return

    async with async_session() as db:
        client = (await db.execute(select(Client).where(
            Client.telegram_id == str(message.from_user.id),
            Client.is_deleted == False,
        ))).scalar_one_or_none()
        if not client:
            return
        if not _is_support_chat_open(client):
            await message.answer(
                "Чат с администратором завершён. Нажмите «💬 Поддержка», чтобы начать новый.",
                reply_markup=MAIN_KEYBOARD,
            )
            return
        saved = await _save_client_support_message(db, client, text)

    if saved:
        await message.answer("✅ Сообщение передано администратору.")
    else:
        await message.answer("Слишком много сообщений. Подождите минуту.")
