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
from sqlalchemy import select, and_, func
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session
from app.models.models import (
    Client, Booking, BookingStatus, ServiceType, TransmissionType,
    Instructor, RatingRecord, RatingVote, ReferralRecord, FAQItem,
    NotificationSent, Package, ClientPackage, Certificate, AuditLog
)
from app.services.booking_service import get_available_slots, find_best_instructor, has_available_instructors

logger = logging.getLogger(__name__)
router = Router()


async def _log_event(db: AsyncSession, action: str, details: str = ""):
    db.add(AuditLog(admin_username="bot", action=action, details=details))


class BookingStates(StatesGroup):
    waiting_name = State()
    choosing_service = State()
    choosing_transmission = State()
    choosing_date = State()
    choosing_time = State()
    entering_phone = State()
    confirming = State()


class RescheduleStates(StatesGroup):
    choosing_date = State()
    choosing_time = State()


class CertificateStates(StatesGroup):
    waiting_code = State()


MAIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📝 Записаться"), KeyboardButton(text="📋 Мои записи")],
        [KeyboardButton(text="❓ FAQ"), KeyboardButton(text="📞 Контакты")],
        [KeyboardButton(text="🎁 Пригласи друга"), KeyboardButton(text="📦 Пакеты")],
        [KeyboardButton(text="🎟️ Сертификат")],
    ],
    resize_keyboard=True,
)


def _instructor_card_text(inst: Instructor) -> str:
    trans_labels = {TransmissionType.MANUAL: "Механика", TransmissionType.AUTOMATIC: "Автомат", TransmissionType.BOTH: "Механика и автомат"}
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
    prefix: str = "date",
) -> list:
    today = date.today()
    buttons = []
    for i in range(4):
        target_date = today + timedelta(days=i)
        if await has_available_instructors(db, target_date, service_type, transmission):
            buttons.append([InlineKeyboardButton(
                text=target_date.strftime("%d.%m.%Y"),
                callback_data=f"{prefix}:{target_date.strftime('%d.%m.%Y')}"
            )])
    return buttons


async def _get_client_by_telegram(telegram_id: str) -> Optional[Client]:
    async with async_session() as db:
        result = await db.execute(
            select(Client).where(Client.telegram_id == telegram_id)
        )
        return result.scalar_one_or_none()


async def _show_service_keyboard(target, client_name: str):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚗 Обучение вождению", callback_data="service:training")],
        [InlineKeyboardButton(text="🏁 Пробный экзамен", callback_data="service:exam")],
    ])
    await target.answer(f"{client_name}, выберите услугу:", reply_markup=kb)


@router.callback_query(F.data == "back")
async def go_back(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    current = await state.get_state()

    if current == BookingStates.choosing_service.state:
        data = await state.get_data()
        if data.get("client_id"):
            await callback.message.edit_text("Нажмите «Записаться» чтобы начать заново.", reply_markup=None)
            await state.clear()
        else:
            await state.set_state(BookingStates.waiting_name)
            await callback.message.edit_text("Как к вам обращаться?")

    elif current == BookingStates.choosing_transmission.state:
        data = await state.get_data()
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚗 Обучение вождению", callback_data="service:training")],
            [InlineKeyboardButton(text="🏁 Пробный экзамен", callback_data="service:exam")],
        ])
        await callback.message.edit_text(f"{data.get('client_name', '')}, выберите услугу:", reply_markup=kb)
        await state.set_state(BookingStates.choosing_service)

    elif current == BookingStates.choosing_date.state:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⚙️ Механика", callback_data="trans:manual")],
            [InlineKeyboardButton(text="🔄 Автомат", callback_data="trans:automatic")],
        ])
        await callback.message.edit_text("Выберите коробку передач:", reply_markup=kb)
        await state.set_state(BookingStates.choosing_transmission)

    elif current == BookingStates.choosing_time.state:
        data = await state.get_data()
        service_type = ServiceType.TRAINING if data["service_type"] == "training" else ServiceType.EXAM
        trans_map = {"manual": TransmissionType.MANUAL, "automatic": TransmissionType.AUTOMATIC}
        transmission = trans_map[data["transmission"]]
        async with async_session() as db:
            buttons = await _build_date_buttons(db, service_type, transmission)
        kb = _kb_with_back(buttons)
        await callback.message.edit_text("Выберите дату:", reply_markup=kb)
        await state.set_state(BookingStates.choosing_date)

    elif current == BookingStates.entering_phone.state:
        data = await state.get_data()
        booking_date = date.fromisoformat(data["booking_date"])
        service_type = ServiceType.TRAINING if data["service_type"] == "training" else ServiceType.EXAM
        trans_map = {"manual": TransmissionType.MANUAL, "automatic": TransmissionType.AUTOMATIC}
        transmission = trans_map[data["transmission"]]
        location = data["location"]

        async with async_session() as db:
            slots = await get_available_slots(db, booking_date, service_type, transmission, location)

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
        await state.update_data(client_name=client.name, client_id=client.id)
        await _show_service_keyboard(message, client.name)
        await state.set_state(BookingStates.choosing_service)
    else:
        await message.answer(
            "Здравствуйте! Это автошкола NOMAD. Как к вам обращаться?",
            reply_markup=MAIN_KEYBOARD,
        )
        await state.set_state(BookingStates.waiting_name)


@router.message(F.text == "📝 Записаться")
async def btn_book(message: Message, state: FSMContext):
    await state.clear()

    client = await _get_client_by_telegram(str(message.from_user.id))
    if client:
        await state.update_data(client_name=client.name, client_id=client.id)
        await _show_service_keyboard(message, client.name)
        await state.set_state(BookingStates.choosing_service)
    else:
        await message.answer("Как к вам обращаться?")
        await state.set_state(BookingStates.waiting_name)


@router.message(BookingStates.waiting_name)
async def process_name(message: Message, state: FSMContext):
    name = message.text.strip()
    if len(name) < 2:
        await message.answer("Пожалуйста, введите корректное имя.")
        return
    await state.update_data(client_name=name)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚗 Обучение вождению", callback_data="service:training")],
        [InlineKeyboardButton(text="🏁 Пробный экзамен", callback_data="service:exam")],
    ])
    await message.answer("Выберите услугу:", reply_markup=kb)
    await state.set_state(BookingStates.choosing_service)


@router.callback_query(BookingStates.choosing_service, F.data.startswith("service:"))
async def process_service(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    service = callback.data.split(":")[1]
    await state.update_data(service_type=service)
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

    service_type_data = (await state.get_data()).get("service_type", "training")
    service_type = ServiceType.TRAINING if service_type_data == "training" else ServiceType.EXAM
    trans_map = {"manual": TransmissionType.MANUAL, "automatic": TransmissionType.AUTOMATIC}
    transmission = trans_map[trans]

    async with async_session() as db:
        buttons = await _build_date_buttons(db, service_type, transmission)
    if not buttons:
        await callback.message.edit_text("К сожалению, на ближайшие 2 недели нет свободных дней. Попробуйте позже.")
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

    if booking_date < date.today():
        await callback.message.edit_text("Дата не может быть в прошлом. Выберите другую дату:")
        return

    data = await state.get_data()
    service_type = ServiceType.TRAINING if data["service_type"] == "training" else ServiceType.EXAM
    trans_map = {"manual": TransmissionType.MANUAL, "automatic": TransmissionType.AUTOMATIC}
    transmission = trans_map[data["transmission"]]
    location = settings.LOCATION_MAIN if service_type == ServiceType.TRAINING else settings.LOCATION_EXAM

    async with async_session() as db:
        slots = await get_available_slots(db, booking_date, service_type, transmission, location)

    if not slots:
        async with async_session() as db:
            buttons = await _build_date_buttons(db, service_type, transmission)
        kb = _kb_with_back(buttons)
        await callback.message.edit_text(
            "На эту дату нет свободных слотов. Выберите другую дату:",
            reply_markup=kb,
        )
        return

    await state.update_data(booking_date=str(booking_date), location=location)
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
    if booking_date < date.today():
        await message.answer("Дата не может быть в прошлом. Введите другую дату:")
        return

    data = await state.get_data()
    service_type = ServiceType.TRAINING if data["service_type"] == "training" else ServiceType.EXAM
    trans_map = {"manual": TransmissionType.MANUAL, "automatic": TransmissionType.AUTOMATIC}
    transmission = trans_map[data["transmission"]]
    location = settings.LOCATION_MAIN if service_type == ServiceType.TRAINING else settings.LOCATION_EXAM

    async with async_session() as db:
        slots = await get_available_slots(db, booking_date, service_type, transmission, location)

    if not slots:
        await message.answer("На эту дату нет свободных слотов. Попробуйте другую дату:")
        return

    await state.update_data(booking_date=str(booking_date), location=location)
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
        await callback.message.edit_text(f"Выбрано время: {time_str}")
        await _finalize_booking(callback.message, state, callback.from_user.id, client)
    else:
        phone_kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="📱 Отправить номер", request_contact=True)],
            ],
            resize_keyboard=True,
            one_time_keyboard=True,
        )
        await callback.message.edit_text(f"Выбрано время: {time_str}")
        await callback.message.answer(
            "Введите ваш номер телефона или отправьте его кнопкой ниже:",
            reply_markup=phone_kb,
        )
        await state.set_state(BookingStates.entering_phone)


async def _finalize_booking(message: Message, state: FSMContext, telegram_id: str, client: Client = None):
    data = await state.get_data()

    async with async_session() as db:
        if not client:
            result = await db.execute(
                select(Client).where(Client.telegram_id == str(telegram_id))
            )
            client = result.scalar_one_or_none()
            if not client:
                referral_code = data.get("referral_code")
                referred_by = None
                if referral_code:
                    ref_result = await db.execute(
                        select(Client).where(Client.referral_code == referral_code)
                    )
                    referrer = ref_result.scalar_one_or_none()
                    if referrer:
                        referred_by = referrer.id

                client = Client(
                    telegram_id=str(telegram_id),
                    name=data["client_name"],
                    referral_code=telegram_id,
                    referred_by_client_id=referred_by,
                )
                db.add(client)
                await db.flush()

                if referred_by:
                    db.add(ReferralRecord(referrer_client_id=referred_by, referred_client_id=client.id))
            else:
                client.name = data["client_name"]

        active_count_result = await db.execute(
            select(func.count()).select_from(Booking).where(
                and_(
                    Booking.client_id == client.id,
                    Booking.status.in_([BookingStatus.PLANNED, BookingStatus.CONFIRMED]),
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

        service_type = ServiceType.TRAINING if data["service_type"] == "training" else ServiceType.EXAM
        trans_map = {"manual": TransmissionType.MANUAL, "automatic": TransmissionType.AUTOMATIC}
        transmission = trans_map[data["transmission"]]
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
                    Booking.status.in_([BookingStatus.PLANNED, BookingStatus.CONFIRMED]),
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
                    Booking.status.in_([BookingStatus.PLANNED, BookingStatus.CONFIRMED]),
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

        price = settings.PRICE_TRAINING if service_type == ServiceType.TRAINING else settings.PRICE_EXAM

        cert_result = await db.execute(
            select(Certificate).where(
                and_(
                    Certificate.activated_by_client_id == client.id,
                    Certificate.remaining > 0,
                )
            )
        )
        cert = cert_result.scalar_one_or_none()
        certificate_discount = 0
        if cert:
            certificate_discount = min(cert.remaining, price)
            cert.remaining -= certificate_discount
            if cert.remaining <= 0:
                cert.is_used = True
            price -= certificate_discount

        instructor = await find_best_instructor(db, booking_date, start_t, end_t, transmission, data["location"])
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

        booking = Booking(
            client_id=client.id,
            instructor_id=instructor.id,
            service_type=service_type,
            transmission=transmission,
            location=data["location"],
            booking_date=booking_date,
            start_time=start_t,
            end_time=end_t,
            price=price,
            certificate_id=cert.id if cert else None,
        )
        db.add(booking)
        await db.commit()
        service_label = "Обучение вождению" if data["service_type"] == "training" else "Пробный экзамен"
        await _log_event(db, "new_booking", f"Клиент: {data['client_name']}, Дата: {data['booking_date']} {data['start_time']}, Инструктор: {instructor.name}, Услуга: {service_label}, Цена: {price}₸")
        
        if certificate_discount > 0:
            cert_status = "полностью" if cert.is_used else f"остаток {cert.remaining}₸"
            await _log_event(db, "certificate_used", f"Клиент: {data['client_name']}, Код: {cert.code}, Использовано: {certificate_discount}₸, Статус: {cert_status}")

    service_label = "Обучение вождению" if data["service_type"] == "training" else "Пробный экзамен"
    trans_label = "Механика" if data["transmission"] == "manual" else "Автомат"
    cert_line = ""
    if certificate_discount > 0:
        cert_line = f"\n🎟️ Сертификат: −{certificate_discount} ₸"
        if cert and cert.remaining > 0:
            cert_line += f"\n💳 Остаток сертификата: {cert.remaining} ₸"
        elif cert:
            cert_line += "\n💳 Сертификат использован полностью"
    summary = (
        f"✅ Вы записаны, {data['client_name']}!\n\n"
        f"📍 {data['location']}\n"
        f"📅 {data['booking_date']}\n"
        f"🕐 {data['start_time']}\n"
        f"🚗 {service_label} ({trans_label})\n"
        f"💰 {price} ₸{cert_line}\n\n"
        f"{_instructor_card_text(instructor)}\n\n"
        f"Мы напомним вам за час до начала занятия."
    )
    await message.answer(summary, reply_markup=MAIN_KEYBOARD, parse_mode="HTML")

    if instructor.telegram_id:
        try:
            await message.bot.send_message(
                int(instructor.telegram_id),
                f"📌 Новая запись!\n"
                f"📅 {data['booking_date']} в {data['start_time']}\n"
                f"Клиент: {data['client_name']}\n"
                f"Площадка: {data['location']}\n"
                f"Коробка: {trans_label}",
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

    async with async_session() as db:
        result = await db.execute(
            select(Client).where(Client.telegram_id == str(message.from_user.id))
        )
        client = result.scalar_one_or_none()
        if client:
            client.phone = phone
            await db.commit()
        else:
            data = await state.get_data()
            referral_code = data.get("referral_code")
            referred_by = None
            if referral_code:
                ref_result = await db.execute(
                    select(Client).where(Client.referral_code == referral_code)
                )
                referrer = ref_result.scalar_one_or_none()
                if referrer:
                    referred_by = referrer.id

            client = Client(
                telegram_id=str(message.from_user.id),
                name=data["client_name"],
                phone=phone,
                referral_code=str(message.from_user.id),
                referred_by_client_id=referred_by,
            )
            db.add(client)
            await db.flush()

            if referred_by:
                db.add(ReferralRecord(referrer_client_id=referred_by, referred_client_id=client.id))
            await db.commit()
            await _log_event(db, "new_client", f"Клиент: {data['client_name']}, Телефон: {phone}")

    await _finalize_booking(message, state, message.from_user.id, client)


@router.message(F.text == "📋 Мои записи")
async def my_bookings(message: Message):
    async with async_session() as db:
        result = await db.execute(
            select(Client).where(Client.telegram_id == str(message.from_user.id))
        )
        client = result.scalar_one_or_none()
        if not client:
            await message.answer("У вас пока нет записей. Нажмите «Записаться».", reply_markup=MAIN_KEYBOARD)
            return

        bookings_result = await db.execute(
            select(Booking).options(selectinload(Booking.certificate)).where(
                and_(
                    Booking.client_id == client.id,
                    Booking.status.in_([BookingStatus.PLANNED, BookingStatus.CONFIRMED]),
                )
            ).order_by(Booking.booking_date, Booking.start_time)
        )
        bookings = bookings_result.scalars().all()

    if not bookings:
        await message.answer("У вас нет активных записей.", reply_markup=MAIN_KEYBOARD)
        return

    status_labels = {
        BookingStatus.PLANNED: "📋 Запланирована",
        BookingStatus.CONFIRMED: "✅ Подтверждена",
    }
    for b in bookings:
        service_label = "Обучение" if b.service_type == ServiceType.TRAINING else "Экзамен"
        cert_line = ""
        if b.certificate_id:
            cert_line = "\n🎟️ Оплачено сертификатом"

        text = (
            f"{status_labels.get(b.status, b.status.value)}\n"
            f"📍 {b.location}\n"
            f"📅 {b.booking_date} 🕐 {b.start_time}\n"
            f"🚗 {service_label}"
            f"{cert_line}"
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
            booking.status = BookingStatus.CANCELLED
            await db.commit()
            await callback.message.edit_text(
                "❌ Запись отменена. Если появится свободное время, мы всегда будем рады!",
                reply_markup=None,
            )
        else:
            await callback.answer("Ошибка", show_alert=True)


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
    if new_date < date.today():
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
        slots = await get_available_slots(db, new_date, booking.service_type, booking.transmission, booking.location)

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

        instructor = await find_best_instructor(db, new_date, new_start, new_end, booking.transmission, booking.location)
        if not instructor:
            await callback.message.edit_text("Нет свободного инструктора на это время. Выберите другое время через «Мои записи».", reply_markup=None)
            await state.clear()
            return

        booking.booking_date = new_date
        booking.start_time = new_start
        booking.end_time = new_end
        booking.instructor_id = instructor.id
        booking.status = BookingStatus.PLANNED
        booking.confirmation_sent = False
        booking.confirmed_by_client = False
        await db.commit()

    await callback.message.edit_text(
        f"✅ Запись перенесена!\n📅 {new_date} 🕐 {chosen_time}\n👨‍🏫 {instructor.name}",
        reply_markup=None,
    )
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


@router.message(F.text == "📞 Контакты")
async def contacts(message: Message):
    await message.answer(
        "📞 Телефон: +77027182233\n"
        "📍 Обучение: Циолковского 28/1\n"
        "📍 Экзамен: Циолковского 30\n"
        "🤖 Бот: @drivepvlbot",
        reply_markup=MAIN_KEYBOARD,
    )


async def send_confirmation_reminders(bot: Bot):
    async with async_session() as db:
        now = datetime.now()
        target_time = (now + timedelta(hours=1)).time()
        today = now.date()

        result = await db.execute(
            select(Booking).where(
                and_(
                    Booking.booking_date == today,
                    Booking.start_time >= target_time,
                    Booking.start_time < time(target_time.hour, target_time.minute + 5) if target_time.minute < 55 else time(target_time.hour + 1, 0),
                    Booking.status == BookingStatus.PLANNED,
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
            booking.confirmed_by_client = True
            booking.status = BookingStatus.CONFIRMED
            await db.commit()
            await _log_event(db, "booking_confirmed", f"Запись #{booking_id} подтверждена клиентом")
    await callback.message.edit_text("✅ Спасибо! Ждём вас на занятии.", reply_markup=None)


@router.callback_query(F.data.startswith("confirm_no:"))
async def confirm_no(callback: CallbackQuery):
    booking_id = int(callback.data.split(":")[1])
    async with async_session() as db:
        result = await db.execute(select(Booking).where(Booking.id == booking_id))
        booking = result.scalar_one_or_none()
        if booking:
            booking.status = BookingStatus.CANCELLED
            await db.commit()
            await _log_event(db, "booking_cancelled", f"Запись #{booking_id} отменена клиентом")
    await callback.message.edit_text(
        "❌ Запись отменена. Если появится свободное время, мы всегда будем рады!",
        reply_markup=None,
    )


async def send_rating_requests(bot: Bot):
    async with async_session() as db:
        now = datetime.now()
        one_hour_ago = now - timedelta(hours=1)
        target_time = one_hour_ago.time()
        today = now.date()

        result = await db.execute(
            select(Booking).where(
                and_(
                    Booking.booking_date == today,
                    Booking.end_time <= target_time,
                    Booking.status.in_([BookingStatus.CONFIRMED, BookingStatus.COMPLETED]),
                    Booking.rating_sent == False,
                )
            )
        )
        bookings = result.scalars().all()

        for booking in bookings:
            client_result = await db.execute(select(Client).where(Client.id == booking.client_id))
            client = client_result.scalar_one_or_none()
            if client:
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="👍 Хорошо", callback_data=f"rate:good:{booking.id}")],
                    [InlineKeyboardButton(text="😐 Нормально", callback_data=f"rate:normal:{booking.id}")],
                    [InlineKeyboardButton(text="👎 Плохо", callback_data=f"rate:bad:{booking.id}")],
                ])
                try:
                    await bot.send_message(
                        int(client.telegram_id),
                        "Оцените как прошло вождение — это очень важно для нас.\n\n"
                        "Оно полностью анонимно и ни на что не влияет.\n"
                        "Это только для повышения качества обслуживания.",
                        reply_markup=kb,
                    )
                    booking.rating_sent = True
                    booking.status = BookingStatus.COMPLETED
                    await db.commit()
                    await _log_event(db, "rating_request_sent", f"Запись #{booking.id}, Клиент: {client.name}")
                except Exception as e:
                    logger.error(f"Failed to send rating to {client.telegram_id}: {e}")


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
        await _log_event(db, "rating_given", f"Инструктор: {instructor.name}, Оценка: {vote_labels[vote_str]}, Рейтинг: {instructor.rating}")

    labels = {"good": "👍 Хорошо", "normal": "😐 Нормально", "bad": "👎 Плохо"}
    await callback.message.edit_text(f"Спасибо за оценку: {labels[vote_str]}!", reply_markup=None)


@router.message(F.text == "🎁 Пригласи друга")
async def referral_command(message: Message):
    async with async_session() as db:
        result = await db.execute(select(Client).where(Client.telegram_id == str(message.from_user.id)))
        client = result.scalar_one_or_none()
        if not client:
            await message.answer("Сначала запишитесь на занятие, чтобы получить реферальную ссылку.", reply_markup=MAIN_KEYBOARD)
            return
        ref_count_result = await db.execute(
            select(func.count()).select_from(ReferralRecord).where(ReferralRecord.referrer_client_id == client.id)
        )
        ref_count = ref_count_result.scalar() or 0
    link = f"https://t.me/drivepvlbot?start={client.referral_code}"
    await message.answer(
        f"🎁 <b>Реферальная программа</b>\n\n"
        f"Пригласите друга и получите скидку 10% на следующее занятие!\n\n"
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
        client_result = await db.execute(select(Client).where(Client.telegram_id == str(message.from_user.id)))
        client = client_result.scalar_one_or_none()
        my_packages_text = ""
        if client:
            cp_result = await db.execute(
                select(ClientPackage).where(and_(ClientPackage.client_id == client.id, ClientPackage.remaining_sessions > 0))
            )
            cps = cp_result.scalars().all()
            if cps:
                lines = []
                for cp in cps:
                    pkg_result = await db.execute(select(Package).where(Package.id == cp.package_id))
                    pkg = pkg_result.scalar_one_or_none()
                    if pkg:
                        lines.append(f"• {pkg.name}: осталось {cp.remaining_sessions} занятий")
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

        client_result = await db.execute(select(Client).where(Client.telegram_id == str(message.from_user.id)))
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

        cert.activated_by_client_id = client.id
        await db.commit()
        await _log_event(db, "certificate_activated", f"Клиент: {client.name}, Код: {cert.code}, Номинал: {cert.nominal}₸")

    cert_info = (
        f"🎟️ Код: <code>{cert.code}</code>\n"
        f"💰 Номинал: {cert.nominal} ₸\n"
        f"💳 Остаток: {cert.remaining} ₸"
    )
    await state.update_data(cert_info=cert_info)

    used_text = ""
    if cert.nominal != cert.remaining:
        used_text = f"\n📝 Использовано: {cert.nominal - cert.remaining} ₸"

    await message.answer(
        f"✅ <b>Сертификат активирован!</b>\n\n"
        f"{cert_info}{used_text}\n\n"
        f"Сертификат будет автоматически применён при следующей записи на занятие.",
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
            f"Коробка: {booking.transmission.value}",
        )
    except Exception as e:
        logger.error(f"Failed to notify instructor {instructor.id}: {e}")


async def check_unconfirmed_bookings(bot: Bot):
    async with async_session() as db:
        cutoff = datetime.now() - timedelta(minutes=settings.CONFIRM_TIMEOUT_MINUTES)
        result = await db.execute(
            select(Booking).where(
                and_(
                    Booking.confirmation_sent == True,
                    Booking.confirmed_by_client == False,
                    Booking.status == BookingStatus.PLANNED,
                )
            )
        )
        bookings = result.scalars().all()
        for booking in bookings:
            if booking.created_at and booking.created_at < cutoff:
                booking.status = BookingStatus.NO_SHOW
                await db.commit()
                await _log_event(db, "no_show", f"Запись #{booking.id} отмечена как неявка")


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
