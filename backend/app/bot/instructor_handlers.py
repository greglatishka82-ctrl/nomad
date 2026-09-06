"""
Бот инструкторов — только handlers/router.
Запускается из backend/app/main.py через lifespan.
"""
import logging
from datetime import date, datetime, timedelta
from typing import Optional

from aiogram import Bot, F, Router, types
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from sqlalchemy import and_, select, func
from sqlalchemy.orm import selectinload

from app.database import async_session
from app.models.models import (
    AuditLog, Booking, BookingStatus, Client, Instructor, ServiceType,
    SupportMessage, now_kz,
)
from app.services.phone_utils import normalize_phone

logger = logging.getLogger(__name__)

router = Router()
# The client bot is injected at startup.  The instructor bot cannot send a
# message on behalf of the client bot, so referral notifications use this one.
client_bot: Optional[Bot] = None
# The scheduler uses the instructor bot for the one-time late-arrival check.
instructor_bot: Optional[Bot] = None

_ARRIVAL_CHECK_ACTION = "system_arrival_check_sent"

INSTRUCTOR_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="📅 Сегодня"),
            KeyboardButton(text="📆 Завтра"),
            KeyboardButton(text="🗓️ Вся неделя"),
        ]
    ],
    resize_keyboard=True,
)

SHARE_PHONE_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="📱 Поделиться номером телефона", request_contact=True)]],
    resize_keyboard=True,
    one_time_keyboard=True,
)


async def _log_instructor_action(instructor: Instructor, action: str, details: str) -> None:
    """Persist instructor-bot actions in the same audit feed as admin actions."""
    async with async_session() as db:
        db.add(AuditLog(
            admin_username=f"Инструктор: {instructor.name}",
            action=action,
            details=details,
            created_at=now_kz(),
        ))
        await db.commit()


async def _get_instructor(message) -> Optional[Instructor]:
    async with async_session() as db:
        if message.from_user.id:
            result = await db.execute(
                select(Instructor).where(Instructor.telegram_id == str(message.from_user.id))
            )
            instructor = result.scalar_one_or_none()
            if instructor:
                return instructor

        if message.from_user.username:
            result = await db.execute(
                select(Instructor).where(Instructor.telegram_username == message.from_user.username)
            )
            instructor = result.scalar_one_or_none()
            if instructor:
                instructor.telegram_id = str(message.from_user.id)
                await db.commit()
                return instructor

    return None


async def _get_instructor_by_phone(phone: str) -> Optional[Instructor]:
    normalized = ''.join(filter(str.isdigit, phone))
    async with async_session() as db:
        result = await db.execute(select(Instructor).where(Instructor.phone.isnot(None)))
        instructors = result.scalars().all()
        for inst in instructors:
            if inst.phone:
                inst_phone = ''.join(filter(str.isdigit, inst.phone))
                if inst_phone[-10:] == normalized[-10:]:
                    return inst
    return None


async def _get_bookings(instructor_id: int, date_from: date, date_to: date):
    async with async_session() as db:
        result = await db.execute(
            select(Booking).options(
                selectinload(Booking.client),
                selectinload(Booking.instructor),
                selectinload(Booking.certificate),
            ).where(
                and_(
                    Booking.instructor_id == instructor_id,
                    Booking.booking_date >= date_from,
                    Booking.booking_date <= date_to,
                    Booking.status.in_([
                        "confirmed",
                        "planned",
                        "in_progress",
                    ]),
                )
            ).order_by(Booking.booking_date, Booking.start_time)
        )
        # The scheduler below handles missed instructor marks.  Do not turn a
        # real booking into a no-show merely because its card was not pressed.
        return list(result.scalars().all())


def _scheduled_start(booking: Booking) -> datetime:
    """Return the exact start stored for this booking."""
    return datetime.combine(booking.booking_date, booking.start_time)


def _scheduled_end(booking: Booking) -> datetime:
    """Return the exact end stored for this booking, not button-press time."""
    return datetime.combine(booking.booking_date, booking.end_time)


def _booking_slot(booking: Booking) -> str:
    return f"{booking.booking_date.strftime('%d.%m.%Y')} {booking.start_time.strftime('%H:%M')}–{_scheduled_end(booking).strftime('%H:%M')}"


def _attendance_keyboard(booking_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Пришёл", callback_data=f"inst_arrived:{booking_id}"),
        InlineKeyboardButton(text="❌ Не пришёл", callback_data=f"inst_no_show:{booking_id}"),
    ]])


def _attendance_confirmation_keyboard(booking_id: int, arrived: bool) -> InlineKeyboardMarkup:
    action = "пришёл" if arrived else "не пришёл"
    callback_data = f"inst_arrived_yes:{booking_id}" if arrived else f"inst_no_show_yes:{booking_id}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Да, клиент {action}", callback_data=callback_data)],
        [InlineKeyboardButton(text="Нет, отмена", callback_data=f"inst_attendance_cancel:{booking_id}")],
    ])


async def _apply_referral_bonus(db, booking: Booking) -> Optional[Client]:
    """Apply the first-paid-lesson referral bonus once and return the referrer."""
    client = booking.client
    if not client or not client.referred_by_client_id or booking.referral_discount_amount <= 0:
        return None

    from app.models.models import ReferralRecord
    from sqlalchemy import func

    paid_bookings_count = (await db.execute(
        select(func.count())
        .select_from(Booking)
        .where(
            Booking.client_id == client.id,
            Booking.payment_status == "paid",
            Booking.status == "completed",
            Booking.id != booking.id,
        )
    )).scalar() or 0
    if paid_bookings_count:
        return None

    client.referral_discount_available = False
    referrer = await db.get(Client, client.referred_by_client_id)
    if not referrer:
        return None

    referrer.referral_discount_available = True
    ref_rec = (await db.execute(
        select(ReferralRecord).where(
            ReferralRecord.referrer_client_id == referrer.id,
            ReferralRecord.referred_client_id == client.id,
        )
    )).scalar_one_or_none()
    if ref_rec:
        ref_rec.discount_applied = True
    logger.info("Referral bonus applied: referrer_id=%s, referred_id=%s", referrer.id, client.id)
    return referrer


async def _complete_booking(db, booking: Booking) -> tuple[bool, Optional[Client]]:
    """Complete a booking without ever adding a second payment.

    A Booking has one payment total, so this routine deliberately does nothing
    when an automatic completion was already stored before an instructor taps
    the old Telegram button.
    """
    if booking.status == "completed" and booking.payment_status == "paid":
        return False, None

    booking.status = "completed"
    if booking.completed_at is None:
        booking.completed_at = now_kz()
    booking.archived_at = None
    booking.payment_status = "paid"
    booking.paid_amount = booking.price
    if booking.paid_at is None:
        booking.paid_at = now_kz()
    referrer = await _apply_referral_bonus(db, booking)
    return True, referrer


async def run_automatic_lesson_transitions() -> None:
    """Ask once about a late client and complete confirmed lessons if needed.

    No arrival is inferred automatically. Twenty minutes after a scheduled
    start, the instructor receives one confirmation request if the booking is
    still planned/confirmed. Completion/payment is due nine minutes after the
    stored service end, but only for a booking the instructor marked as
    in-progress.
    """
    now = now_kz()
    async with async_session() as db:
        result = await db.execute(
            select(Booking)
            .options(selectinload(Booking.client))
            .where(
                Booking.booking_date <= now.date(),
                Booking.status.in_(["planned", "confirmed", "in_progress"]),
            )
            .with_for_update(skip_locked=True)
        )
        changed = False
        for booking in result.scalars().all():
            starts_at = _scheduled_start(booking)
            arrival_check_due = starts_at + timedelta(minutes=20)
            completion_due = _scheduled_end(booking) + timedelta(minutes=9)
            client_name = booking.client.name if booking.client else "Клиент"

            if (
                now >= arrival_check_due
                and booking.status in ("planned", "confirmed")
                and instructor_bot
                and booking.instructor_id
            ):
                instructor = await db.get(Instructor, booking.instructor_id)
                prompt_sent = (await db.execute(
                    select(AuditLog.id).where(
                        AuditLog.action == _ARRIVAL_CHECK_ACTION,
                        # Include the comma after the id so booking #12 does
                        # not accidentally match the audit row for #123.
                        AuditLog.details.like(f"%запись #{booking.id},%"),
                    ).limit(1)
                )).scalar_one_or_none()
                if instructor and instructor.telegram_id and prompt_sent is None:
                    try:
                        await instructor_bot.send_message(
                            int(instructor.telegram_id),
                            f"⏰ Клиент {client_name} не отметил приход через 20 минут после начала.\n\n"
                            f"Запись: {_booking_slot(booking)}\n"
                            f"📍 {booking.location}\n\n"
                            "Клиент пришёл?",
                            reply_markup=_attendance_keyboard(booking.id),
                        )
                    except Exception as exc:
                        logger.warning("Could not send arrival check for booking %s: %s", booking.id, exc)
                    else:
                        db.add(AuditLog(
                            admin_username="Система",
                            action=_ARRIVAL_CHECK_ACTION,
                            details=f"Отправлен вопрос о приходе клиента {client_name}, запись #{booking.id}, слот {_booking_slot(booking)}",
                            created_at=now,
                        ))
                        changed = True

            if now >= completion_due and booking.status == "in_progress":
                completed, _ = await _complete_booking(db, booking)
                if completed:
                    db.add(AuditLog(
                        admin_username="Система",
                        action="system_lesson_completed_auto",
                        details=(f"Автоматически завершено и оплачено: клиент {client_name}, "
                                 f"запись #{booking.id}, слот {_booking_slot(booking)}, сумма {booking.paid_amount} ₸"),
                        created_at=now,
                    ))
                    changed = True

        if changed:
            await db.commit()


async def purge_expired_booking_history() -> None:
    """Keep a rolling 60-day booking history while preserving every client.

    Only a past booking is removed. Client rows and their profiles, packages,
    certificates and support history are intentionally untouched.
    """
    now = now_kz()
    cutoff = now - timedelta(days=60)
    async with async_session() as db:
        result = await db.execute(
            select(Booking).where(
                Booking.created_at < cutoff,
                Booking.booking_date < now.date(),
            )
        )
        expired = result.scalars().all()
        if not expired:
            return
        deleted_count = len(expired)
        for booking in expired:
            await db.delete(booking)
        db.add(AuditLog(
            admin_username="Система",
            action="system_booking_history_purged",
            details=f"Удалено записей старше 60 дней: {deleted_count}. Карточки клиентов сохранены.",
            created_at=now,
        ))
        await db.commit()


def _booking_card_text(b) -> str:
    service = "🚗 Обучение" if b.service_type == ServiceType.TRAINING else "🏁 Экзамен"
    trans = "Механика" if b.transmission == "manual" else "Автомат"
    status_labels = {
        "planned": "📋 Запланирована",
        "confirmed": "✅ Подтверждена",
        "in_progress": "🔄 В процессе",
    }
    status_text = status_labels.get(b.status, b.status)
    package_paid = b.package_id is not None
    cert_paid = b.certificate_id is not None

    pay_lines = []
    if package_paid:
        pay_lines.append("📦 ОПЛАЧЕНО ПАКЕТОМ — деньги НЕ брать!")
    elif cert_paid:
        pay_lines.append("🎟️ ОПЛАЧЕНО СЕРТИФИКАТОМ — деньги НЕ брать!")
    elif b.price and b.price > 0:
        if b.referral_discount_amount and b.referral_discount_amount > 0:
            pay_lines.append(f"🎁 Скидка по реферальному коду: {b.referral_discount_amount} ₸")
            pay_lines.append(f"💰 К оплате: {b.price} ₸")
        else:
            pay_lines.append(f"💰 К оплате: {b.price} ₸")
    else:
        pay_lines.append("💰 К оплате: 0 ₸")

    pay_block = "\n".join(pay_lines)
    text = (
        f"{status_text}\n\n"
        f"📅 {b.booking_date.strftime('%d.%m.%Y')} в {str(b.start_time)[:5]}\n"
        f"{service} ({trans})\n"
        f"📍 {b.location}\n"
        f"👤 {b.client.name}"
    )
    if b.client.phone:
        text += f" ({b.client.phone})"
    text += f"\n\n{pay_block}"
    return text


RU_DAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]


def _day_header(target_date: date) -> str:
    """Формирует заголовок вида: 📆 Записи на завтра: / на сегодня, / на: + день недели."""
    today = now_kz().date()
    delta = (target_date - today).days
    day_name = RU_DAYS[target_date.weekday()]
    if delta == 0:
        return f"📆 Записи на сегодня,\n──  {day_name}  ──"
    elif delta == 1:
        return f"📆 Записи на завтра:\n──  {day_name}  ──"
    else:
        return f"📆 Записи на:\n──  {day_name}  ──"


async def _show_bookings(message, instructor, date_from: date, date_to: date, title: str):
    bookings = await _get_bookings(instructor.id, date_from, date_to)
    if not bookings:
        # Если один день — красивый заголовок, иначе переданный title
        if date_from == date_to:
            header = _day_header(date_from)
        else:
            header = title
        await message.answer(f"{header}\n\nНет активных записей.", reply_markup=INSTRUCTOR_KEYBOARD)
        return

    # Группируем по дням
    from collections import defaultdict
    by_day = defaultdict(list)
    for b in bookings:
        by_day[b.booking_date].append(b)

    first_day = True
    for day in sorted(by_day.keys()):
        day_bookings = by_day[day]

        # Заголовок дня
        day_header = _day_header(day)

        for idx, b in enumerate(day_bookings):
            package_paid = b.package_id is not None
            cert_paid = b.certificate_id is not None
            kb = InlineKeyboardMarkup(inline_keyboard=[])
            if b.status in ("planned", "confirmed"):
                kb = _attendance_keyboard(b.id)
            elif b.status == "in_progress":
                btn_text = "✅ Занятие окончено" if (package_paid or cert_paid) else "💰 Занятие окончено (оплата получена)"
                kb.inline_keyboard.append([
                    InlineKeyboardButton(text=btn_text, callback_data=f"inst_done:{b.id}")
                ])

            # Первой записи дня — добавляем заголовок
            card = _booking_card_text(b)
            if idx == 0:
                card = f"{day_header}\n\n{card}"

            # К первому сообщению первого дня прикрепляем навигационную клавиатуру
            # если у карточки нет inline-кнопок; иначе шлём навигацию отдельно
            if first_day and idx == 0:
                if kb.inline_keyboard:
                    await message.answer(card, reply_markup=kb)
                    await message.answer("👇 Выберите период:", reply_markup=INSTRUCTOR_KEYBOARD)
                else:
                    await message.answer(card, reply_markup=INSTRUCTOR_KEYBOARD)
                first_day = False
            else:
                await message.answer(card, reply_markup=kb if kb.inline_keyboard else None)

    # Если first_day всё ещё True — значит bookings было пусто (обработано выше)
    pass


@router.message(CommandStart())
async def start(message: types.Message):
    instructor = await _get_instructor(message)
    if not instructor:
        await message.answer(
            "Для доступа к боту инструктора поделитесь своим номером телефона:",
            reply_markup=SHARE_PHONE_KEYBOARD,
        )
        return
    today = now_kz().date()
    await _log_instructor_action(instructor, "instructor_bot_opened", "Инструктор открыл бот")
    await message.answer(
        f"👋 Здравствуйте, {instructor.name}!\nВыберите период:",
        reply_markup=INSTRUCTOR_KEYBOARD,
    )
    await _show_bookings(message, instructor, today, today, _day_header(today))


@router.message(F.contact)
async def handle_contact(message: types.Message):
    contact = message.contact
    if contact.user_id != message.from_user.id:
        await message.answer("Пожалуйста, поделитесь своим номером телефона.", reply_markup=SHARE_PHONE_KEYBOARD)
        return

    instructor = await _get_instructor_by_phone(contact.phone_number)
    if not instructor:
        await message.answer(
            "❌ Этот номер телефона не найден в системе.\n"
            "Обратитесь к администратору для добавления вас в систему.",
            reply_markup=types.ReplyKeyboardRemove(),
        )
        return

    async with async_session() as db:
        result = await db.execute(select(Instructor).where(Instructor.id == instructor.id))
        inst = result.scalar_one_or_none()
        if inst:
            inst.telegram_id = str(message.from_user.id)
            if message.from_user.username:
                inst.telegram_username = message.from_user.username
            await db.commit()

    await message.answer(
        f"✅ Добро пожаловать, {instructor.name}!\nВыберите период:",
        reply_markup=INSTRUCTOR_KEYBOARD,
    )
    today = now_kz().date()
    await _log_instructor_action(instructor, "instructor_bot_authorized", "Инструктор подтвердил номер телефона в боте")
    await _show_bookings(message, instructor, today, today, _day_header(today))


@router.message(F.text == "📅 Сегодня")
async def today_bookings(message: types.Message):
    instructor = await _get_instructor(message)
    if not instructor:
        return
    today = now_kz().date()
    await _log_instructor_action(instructor, "instructor_schedule_viewed", "Инструктор открыл расписание на сегодня")
    await _show_bookings(message, instructor, today, today, _day_header(today))


@router.message(F.text == "📆 Завтра")
async def tomorrow_bookings(message: types.Message):
    instructor = await _get_instructor(message)
    if not instructor:
        return
    tomorrow = now_kz().date() + timedelta(days=1)
    await _log_instructor_action(instructor, "instructor_schedule_viewed", "Инструктор открыл расписание на завтра")
    await _show_bookings(message, instructor, tomorrow, tomorrow, _day_header(tomorrow))


@router.message(F.text == "🗓️ Вся неделя")
async def week_bookings(message: types.Message):
    instructor = await _get_instructor(message)
    if not instructor:
        return
    today = now_kz().date()
    week_end = today + timedelta(days=6)
    await _log_instructor_action(instructor, "instructor_schedule_viewed", "Инструктор открыл расписание на неделю")
    await _show_bookings(message, instructor, today, week_end, f"🗓️ Записи на 7 дней ({today.strftime('%d.%m')}–{week_end.strftime('%d.%m')})")


@router.message(F.text)
async def instructor_support_message(message: types.Message):
    instructor = await _get_instructor(message)
    if not instructor:
        return
    if message.text in {"📅 Сегодня", "📆 Завтра", "🗓️ Вся неделя"}:
        return
    if len(message.text) > 2000:
        await message.answer("Сообщение слишком длинное. Максимум 2000 символов.")
        return
    async with async_session() as db:
        recent = (await db.execute(
            select(func.count()).select_from(SupportMessage).where(
                SupportMessage.instructor_id == instructor.id,
                SupportMessage.sender == "instructor",
                SupportMessage.created_at >= now_kz() - timedelta(minutes=1),
            )
        )).scalar() or 0
        if recent >= 5:
            await message.answer("Слишком много сообщений. Подождите минуту.")
            return
        db.add(SupportMessage(
            instructor_id=instructor.id,
            channel="instructor",
            sender="instructor",
            text=message.text,
            is_read=False,
            is_admin_read=False,
            created_at=now_kz(),
        ))
        db.add(AuditLog(
            admin_username=f"Инструктор: {instructor.name}",
            action="instructor_support_message",
            details="Инструктор отправил сообщение в поддержку",
            created_at=now_kz(),
        ))
        await db.commit()
    await message.answer("Сообщение передано администратору.")


async def _load_instructor_booking(db, callback: types.CallbackQuery, booking_id: int):
    result = await db.execute(
        select(Booking)
        .options(selectinload(Booking.client))
        .where(Booking.id == booking_id)
        .with_for_update()
    )
    booking = result.scalar_one_or_none()
    if not booking:
        await callback.answer("Запись не найдена", show_alert=True)
        return None, None
    instructor = await db.get(Instructor, booking.instructor_id)
    if not instructor or instructor.telegram_id != str(callback.from_user.id):
        await callback.answer("Эта карточка принадлежит другому инструктору", show_alert=True)
        return None, None
    return booking, instructor


async def _ask_attendance_confirmation(callback: types.CallbackQuery, arrived: bool) -> None:
    booking_id = int(callback.data.split(":")[1])
    async with async_session() as db:
        booking, _ = await _load_instructor_booking(db, callback, booking_id)
        if not booking:
            return
        if booking.status in ("cancelled", "cancellation_pending", "reschedule_pending"):
            await callback.answer("Запись отменена или переносится", show_alert=True)
            return
        if booking.status not in ("planned", "confirmed"):
            await callback.answer("По этой записи уже принято решение", show_alert=True)
            return
        starts_at = _scheduled_start(booking)
        if now_kz() < starts_at:
            await callback.answer(
                f"Отметить приход можно не раньше {starts_at.strftime('%d.%m.%Y %H:%M')}",
                show_alert=True,
            )
            return

        action = "клиент пришёл" if arrived else "клиент не пришёл"
        await callback.message.edit_text(
            f"⚠️ Подтвердите действие:\n\nВы уверены, что {action}?\n\n{callback.message.text}",
            reply_markup=_attendance_confirmation_keyboard(booking.id, arrived),
        )
        await callback.answer("Подтвердите выбор кнопкой «Да»")


@router.callback_query(F.data.startswith("inst_arrived:"))
async def client_arrived(callback: types.CallbackQuery):
    await _ask_attendance_confirmation(callback, arrived=True)


@router.callback_query(F.data.startswith("inst_no_show:"))
async def client_no_show(callback: types.CallbackQuery):
    await _ask_attendance_confirmation(callback, arrived=False)


async def _confirm_client_arrived(callback: types.CallbackQuery) -> None:
    booking_id = int(callback.data.split(":")[1])
    async with async_session() as db:
        booking, instructor = await _load_instructor_booking(db, callback, booking_id)
        if not booking:
            return
        if booking.status in ("cancelled", "cancellation_pending", "reschedule_pending", "no_show"):
            await callback.answer("Запись отменена или уже отмечена как неявка", show_alert=True)
            return
        if booking.status == "completed":
            await callback.answer("Занятие уже завершено", show_alert=True)
            return
        starts_at = _scheduled_start(booking)
        if now_kz() < starts_at:
            await callback.answer(
                f"Отметить приход можно не раньше {starts_at.strftime('%d.%m.%Y %H:%M')}",
                show_alert=True,
            )
            return

        previous_status = booking.status
        if previous_status in ("planned", "confirmed"):
            booking.status = "in_progress"
        db.add(AuditLog(
            admin_username=f"Инструктор: {instructor.name}",
            action="instructor_client_arrived",
            details=(f"Инструктор нажал «Клиент пришёл»: клиент {booking.client.name if booking.client else 'Клиент'}, "
                     f"запись #{booking.id}, слот {_booking_slot(booking)}, прежний статус: {previous_status}"),
            created_at=now_kz(),
        ))
        await db.commit()

    await callback.message.edit_text(
        f"{callback.message.text}\n\n✅ Клиент отмечен как пришедший",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="✅ Занятие окончено" if (booking.package_id or booking.certificate_id) else "💰 Занятие окончено (оплата получена)",
                callback_data=f"inst_done:{booking.id}",
            )
        ]]),
    )
    await callback.answer("Отметка сохранена")


@router.callback_query(F.data.startswith("inst_arrived_yes:"))
async def client_arrived_confirmed(callback: types.CallbackQuery):
    await _confirm_client_arrived(callback)


async def _confirm_client_no_show(callback: types.CallbackQuery) -> None:
    booking_id = int(callback.data.split(":")[1])
    async with async_session() as db:
        booking, instructor = await _load_instructor_booking(db, callback, booking_id)
        if not booking:
            return
        if booking.status in ("cancelled", "cancellation_pending", "reschedule_pending"):
            await callback.answer("Запись отменена или переносится", show_alert=True)
            return
        if booking.status not in ("planned", "confirmed"):
            await callback.answer("По этой записи уже принято решение", show_alert=True)
            return
        starts_at = _scheduled_start(booking)
        if now_kz() < starts_at:
            await callback.answer(
                f"Отметить неявку можно не раньше {starts_at.strftime('%d.%m.%Y %H:%M')}",
                show_alert=True,
            )
            return

        booking.status = "no_show"
        db.add(AuditLog(
            admin_username=f"Инструктор: {instructor.name}",
            action="instructor_client_no_show",
            details=(f"Инструктор подтвердил «Клиент не пришёл»: клиент {booking.client.name if booking.client else 'Клиент'}, "
                     f"запись #{booking.id}, слот {_booking_slot(booking)}"),
            created_at=now_kz(),
        ))
        await db.commit()

    await callback.message.edit_text(
        f"{callback.message.text}\n\n🚫 Клиент отмечен как не явившийся.",
        reply_markup=None,
    )
    await callback.answer("Неявка сохранена")


@router.callback_query(F.data.startswith("inst_no_show_yes:"))
async def client_no_show_confirmed(callback: types.CallbackQuery):
    await _confirm_client_no_show(callback)


@router.callback_query(F.data.startswith("inst_attendance_cancel:"))
async def attendance_confirmation_cancel(callback: types.CallbackQuery):
    booking_id = int(callback.data.split(":")[1])
    async with async_session() as db:
        booking, _ = await _load_instructor_booking(db, callback, booking_id)
        if not booking:
            return
        if booking.status not in ("planned", "confirmed"):
            await callback.answer("По этой записи уже принято решение", show_alert=True)
            return
        marker = "⚠️ Подтвердите действие:\n\n"
        original_text = callback.message.text
        if original_text.startswith(marker):
            original_text = original_text[len(marker):]
        await callback.message.edit_text(
            original_text,
            reply_markup=_attendance_keyboard(booking.id),
        )
    await callback.answer("Действие отменено")


@router.callback_query(F.data.startswith("inst_done:"))
async def lesson_done(callback: types.CallbackQuery):
    booking_id = int(callback.data.split(":")[1])
    async with async_session() as db:
        result = await db.execute(
            select(Booking).options(selectinload(Booking.client))
            .where(Booking.id == booking_id)
            .with_for_update()
        )
        booking = result.scalar_one_or_none()
        if not booking:
            await callback.answer("Запись не найдена", show_alert=True)
            return
        instructor = await db.get(Instructor, booking.instructor_id)
        if not instructor or instructor.telegram_id != str(callback.from_user.id):
            await callback.answer("Эта карточка принадлежит другому инструктору", show_alert=True)
            return
        if booking.status in ("cancelled", "cancellation_pending", "reschedule_pending"):
            await callback.answer("Запись отменена или переносится", show_alert=True)
            return
        if booking.status == "no_show":
            await callback.answer("Клиент отмечен как не явившийся", show_alert=True)
            return
        if booking.status not in ("in_progress", "completed"):
            await callback.answer("Сначала отметьте, что клиент пришёл", show_alert=True)
            return
        ends_at = _scheduled_end(booking)
        if now_kz() < ends_at:
            await callback.answer(
                f"Завершить занятие можно не раньше {ends_at.strftime('%d.%m.%Y %H:%M')}",
                show_alert=True,
            )
            return

        completed, referrer = await _complete_booking(db, booking)
        db.add(AuditLog(
            admin_username=f"Инструктор: {instructor.name}",
            action="instructor_lesson_completed",
            details=(f"Инструктор нажал «Занятие окончено»: клиент {booking.client.name if booking.client else 'Клиент'}, "
                     f"запись #{booking.id}, слот {_booking_slot(booking)}, "
                     f"{'оплата зафиксирована' if completed else 'уже было автоматически зафиксировано, дубль не создан'}"),
            created_at=now_kz(),
        ))
        await db.commit()

    if completed and referrer and referrer.telegram_id and client_bot:
        try:
            await client_bot.send_message(
                int(referrer.telegram_id),
                "🎁 Ваш друг завершил первое занятие. Вам доступна скидка 1000 ₸ на следующее занятие.",
            )
        except Exception as exc:
            logger.warning("Could not send referral notification to client %s: %s", referrer.id, exc)
    
    if completed:
        await callback.message.edit_text(
            f"{callback.message.text}\n\n💰 Занятие завершено.",
            reply_markup=None,
        )
        await callback.answer("Занятие завершено!")
    else:
        await callback.answer("Уже завершено системой; оплата не дублировалась", show_alert=True)
