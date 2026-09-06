"""Interactive business reports for the owner-only Telegram bot."""
import calendar
from collections import defaultdict
from datetime import date, datetime, timedelta

from aiogram import F, Router, types
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.config import TIMEZONE, settings
from app.database import async_session
from app.models.models import Booking, Certificate, Client, ClientPackage, GenderAnalytics, Instructor

router = Router()

REPORT_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📊 Сегодня"), KeyboardButton(text="🗓 Выбрать дату")],
        [KeyboardButton(text="📈 За всё время")],
    ],
    resize_keyboard=True,
)
SHARE_OWNER_PHONE_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="Поделиться номером", request_contact=True)]],
    resize_keyboard=True,
    one_time_keyboard=True,
)

_allowed_user_ids: set[int] = set()
_MONTHS = ("", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь")
_WEEKDAYS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
_ACTIVE_STATUSES = {"pending", "planned", "confirmed", "reschedule_pending", "cancellation_pending"}
_ACTIVE_ASSIGNMENT_STATUSES = _ACTIVE_STATUSES | {"in_progress"}
_PROBLEM_STATUSES = {"conflict", "disputed"}
_ANALYTICS_LOAD_STATUSES = {"confirmed", "completed"}
_WEEKDAYS_FULL = ("Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье")
_MIN_YEAR, _MAX_YEAR = 2000, 2100


def _today() -> date:
    return datetime.now(TIMEZONE).date()


def _normalize_phone(phone: str | None) -> str:
    return "".join(filter(str.isdigit, phone or ""))


def _is_owner_phone(phone: str | None) -> bool:
    owner, current = _normalize_phone(settings.REPORT_OWNER_PHONE), _normalize_phone(phone)
    return len(owner) >= 10 and len(current) >= 10 and owner[-10:] == current[-10:]


def _money(value: int | float | None) -> str:
    return f"{int(value or 0):,}".replace(",", " ") + " ₸"


def _paid_amount(booking: Booking) -> int:
    if booking.paid_amount:
        return int(booking.paid_amount)
    return int(booking.price or 0) if booking.payment_status == "paid" else 0


def _expected_amount(booking: Booking) -> int:
    return 0 if booking.status == "cancelled" else int(booking.price or 0)


def _percent(part: int, total: int) -> int:
    return round(part * 100 / total) if total > 0 else 0


def _datetime_bounds(target: date) -> tuple[datetime, datetime]:
    start = datetime.combine(target, datetime.min.time())
    return start, start + timedelta(days=1)


def _calendar_keyboard(year: int, month: int) -> InlineKeyboardMarkup:
    today = _today()
    rows = [[InlineKeyboardButton(text=f"{_MONTHS[month]} {year}", callback_data="report:noop")]]
    rows.append([InlineKeyboardButton(text=name, callback_data="report:noop") for name in _WEEKDAYS])
    for week in calendar.Calendar(firstweekday=0).monthdayscalendar(year, month):
        buttons = []
        for day_number in week:
            if not day_number:
                buttons.append(InlineKeyboardButton(text="·", callback_data="report:noop"))
                continue
            selected = date(year, month, day_number)
            label = f"·{day_number}·" if selected == today else str(day_number)
            buttons.append(InlineKeyboardButton(text=label, callback_data=f"report:date:{selected.isoformat()}"))
        rows.append(buttons)
    previous_month = date(year, month, 1) - timedelta(days=1)
    next_month = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    rows.append([
        InlineKeyboardButton(text="◀️", callback_data=f"report:calendar:{previous_month:%Y-%m}"),
        InlineKeyboardButton(text="Сегодня", callback_data=f"report:date:{today.isoformat()}"),
        InlineKeyboardButton(text="▶️", callback_data=f"report:calendar:{next_month:%Y-%m}"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _summarize(
    bookings: list[Booking],
    instructors: list[Instructor],
    *,
    include_historical_unassigned: bool = True,
) -> dict:
    instructor_by_id = {item.id: item for item in instructors}
    per_instructor = defaultdict(lambda: {
        "name": "Без инструктора", "rating": None, "total": 0,
        "came": 0, "no_show": 0, "cancelled": 0, "paid": 0,
    })
    for instructor in instructors:
        per_instructor[instructor.id].update(name=instructor.name, rating=instructor.rating)

    statuses, services, transmissions, sources = (defaultdict(int) for _ in range(4))
    expected = paid = unpaid = certificate_discount = referral_discount = package_lessons = unassigned_active = 0
    for booking in bookings:
        instructor = booking.instructor or instructor_by_id.get(booking.instructor_id)
        missing_active_instructor = booking.instructor_id is None and booking.status in _ACTIVE_ASSIGNMENT_STATUSES
        historical_unassigned = booking.instructor_id is None and not missing_active_instructor
        include_instructor_row = not historical_unassigned or include_historical_unassigned
        row = None
        if include_instructor_row:
            instructor_key = booking.instructor_id if booking.instructor_id is not None else (
                "missing_active" if missing_active_instructor else "missing_historical"
            )
            row = per_instructor[instructor_key]
            row["name"] = instructor.name if instructor else (
                "Без инструктора" if missing_active_instructor else "История удалённого инструктора"
            )
            row["rating"] = instructor.rating if instructor else None
            row["total"] += 1
            if booking.status in ("completed", "in_progress"):
                row["came"] += 1
            elif booking.status == "no_show":
                row["no_show"] += 1
            elif booking.status == "cancelled":
                row["cancelled"] += 1
        statuses[booking.status or "unknown"] += 1
        services[booking.service_type or "unknown"] += 1
        transmissions[booking.transmission or "unknown"] += 1
        sources[(booking.source or "unknown").lower()] += 1
        expected_amount, paid_amount = _expected_amount(booking), _paid_amount(booking)
        expected += expected_amount
        paid += paid_amount
        if row is not None:
            row["paid"] += paid_amount
        unpaid += int(expected_amount > paid_amount)
        certificate_discount += int(booking.certificate_amount or 0)
        referral_discount += int(booking.referral_discount_amount or 0)
        package_lessons += int(booking.package_id is not None)
        unassigned_active += int(missing_active_instructor)

    came = statuses["completed"] + statuses["in_progress"]
    return {
        "statuses": statuses, "services": services, "transmissions": transmissions, "sources": sources,
        "instructors": sorted(per_instructor.values(), key=lambda item: (-item["total"], item["name"])),
        "expected": expected, "paid": paid, "due": max(expected - paid, 0), "unpaid": unpaid,
        "certificate_discount": certificate_discount, "referral_discount": referral_discount,
        "package_lessons": package_lessons, "unassigned_active": unassigned_active,
        "attendance": _percent(came, came + statuses["no_show"]),
    }


def _source_count(sources: dict, *names: str) -> int:
    return sum(sources[name] for name in names)


def _status_lines(summary: dict, total: int) -> list[str]:
    statuses = summary["statuses"]
    return [
        f"Всего: {total}",
        f"Ожидают/запланированы: {sum(statuses[item] for item in _ACTIVE_STATUSES)}",
        f"Завершены: {statuses['completed']}",
        f"Сейчас идут: {statuses['in_progress']}",
        f"Неявки: {statuses['no_show']}",
        f"Отменены: {statuses['cancelled']}",
        f"Конфликтные/спорные: {sum(statuses[item] for item in _PROBLEM_STATUSES)}",
        f"Явка: {summary['attendance']}%",
    ]


def _finance_lines(summary: dict) -> list[str]:
    lines = [
        f"Плановая сумма: {_money(summary['expected'])}",
        f"Оплачено: {_money(summary['paid'])}",
        f"К оплате: {_money(summary['due'])}",
        f"Неоплаченных записей: {summary['unpaid']}",
    ]
    if summary["certificate_discount"] > 0:
        lines.append(f"Скидка сертификатами: {_money(summary['certificate_discount'])}")
    if summary["referral_discount"] > 0:
        lines.append(f"Реферальные скидки: {_money(summary['referral_discount'])}")
    return lines


def _service_lines(summary: dict) -> list[str]:
    services, transmissions, sources = summary["services"], summary["transmissions"], summary["sources"]
    return [
        f"- Вождение: {services['training']}",
        f"- Пробный экзамен: {services['exam']}",
        f"- Механика: {transmissions['manual']}",
        f"- Автомат: {transmissions['automatic']}",
        f"- Telegram: {_source_count(sources, 'telegram')}",
        f"- Приложение: {_source_count(sources, 'mobile')}",
        f"- Администратор: {_source_count(sources, 'manual', 'admin', 'admin_offline', 'offline')}",
    ]


def _instructor_lines(summary: dict, limit: int | None = None) -> list[str]:
    rows = summary["instructors"]
    if limit is not None:
        rows = [row for row in rows if row["total"] > 0][:limit]
    if not rows:
        return ["- Записей нет"]
    result = []
    for index, row in enumerate(rows, start=1):
        rating = f"{row['rating']:.1f}" if row["rating"] is not None else "-"
        result.append(
            f"{index}. {row['name']} — {row['total']} зап., явок {row['came']}, "
            f"неявок {row['no_show']}, {_money(row['paid'])}, рейтинг {rating}"
        )
    return result


def _gender_analytics_lines(gender: GenderAnalytics | None) -> list[str]:
    if gender is None or int(gender.total_count or 0) <= 0:
        return ["Аналитика клиентов: данные ещё не рассчитаны"]
    lines = [
        "Аналитика клиентов по именам:",
        f"Мужчины: {int(gender.male_count or 0)}",
        f"Женщины: {int(gender.female_count or 0)}",
    ]
    if int(gender.unknown_count or 0) > 0:
        lines.append(f"Не определено: {int(gender.unknown_count)}")
    return lines


def _analytics_load_count(bookings: list[Booking]) -> int:
    return sum(booking.status in _ANALYTICS_LOAD_STATUSES for booking in bookings)


def _most_loaded_day(load_by_date: dict[date, int]) -> tuple[date, int] | None:
    if not load_by_date:
        return None
    return max(load_by_date.items(), key=lambda item: (item[1], item[0]))


def _daily_extra_lines(
    *,
    summary: dict,
    gender: GenderAnalytics | None,
    analytics_load: int,
    week_load_by_date: dict[date, int] | None,
    package_purchases: int,
    cert_created: int,
    cert_used: int,
) -> list[str]:
    lines = ["", "Аналитика:", *_gender_analytics_lines(gender)]
    lines.append(f"Нагрузка дня (подтверждённые и завершённые): {analytics_load}")
    if week_load_by_date is not None:
        busiest = _most_loaded_day(week_load_by_date)
        if busiest:
            busy_date, busy_count = busiest
            lines.append(
                f"Самый загруженный с понедельника: {_WEEKDAYS_FULL[busy_date.weekday()]} "
                f"({busy_date:%d.%m}) — {busy_count} записей"
            )
        else:
            lines.append("С понедельника нет подтверждённых или завершённых записей")

    package_and_certificate_lines = []
    if package_purchases > 0:
        package_and_certificate_lines.append(f"Активировано пакетов: {package_purchases}")
    if summary["package_lessons"] > 0:
        package_and_certificate_lines.append(f"Занятий по пакету: {summary['package_lessons']}")
    if cert_created > 0:
        package_and_certificate_lines.append(f"Создано сертификатов: {cert_created}")
    if cert_used > 0:
        package_and_certificate_lines.append(f"Использовано сертификатов: {cert_used}")
    if package_and_certificate_lines:
        lines += ["", "Пакеты и сертификаты:", *package_and_certificate_lines]
    return lines


async def _build_date_report(target: date, *, include_week_leader: bool = False) -> str:
    next_date = target + timedelta(days=1)
    day_start, day_end = _datetime_bounds(target)
    async with async_session() as db:
        bookings = (await db.execute(
            select(Booking).options(selectinload(Booking.client), selectinload(Booking.instructor))
            .where(Booking.booking_date == target).order_by(Booking.start_time)
        )).scalars().all()
        instructors = (await db.execute(
            select(Instructor).where(Instructor.is_active.is_(True)).order_by(Instructor.name)
        )).scalars().all()
        next_bookings = (await db.execute(
            select(Booking).options(selectinload(Booking.client), selectinload(Booking.instructor))
            .where(Booking.booking_date == next_date, Booking.status != "cancelled").order_by(Booking.start_time)
        )).scalars().all()
        new_clients = await db.scalar(select(func.count()).select_from(Client).where(
            Client.created_at >= day_start, Client.created_at < day_end, Client.is_deleted.is_(False)
        )) or 0
        package_purchases = await db.scalar(select(func.count()).select_from(ClientPackage).where(
            ClientPackage.purchased_at >= day_start, ClientPackage.purchased_at < day_end
        )) or 0
        cert_created = await db.scalar(select(func.count()).select_from(Certificate).where(
            Certificate.created_at >= day_start, Certificate.created_at < day_end
        )) or 0
        cert_used = await db.scalar(select(func.count()).select_from(Certificate).where(
            Certificate.used_at >= day_start, Certificate.used_at < day_end
        )) or 0
        gender_analytics = await db.get(GenderAnalytics, 1)
        week_load_by_date: dict[date, int] | None = None
        if include_week_leader:
            week_start = target - timedelta(days=target.weekday())
            week_rows = (await db.execute(
                select(Booking.booking_date, func.count(Booking.id))
                .where(
                    Booking.booking_date >= week_start,
                    Booking.booking_date <= target,
                    Booking.status.in_(_ANALYTICS_LOAD_STATUSES),
                )
                .group_by(Booking.booking_date)
            )).all()
            week_load_by_date = {booking_date: int(count) for booking_date, count in week_rows}

    # A daily report describes today's currently assigned staff. Historical
    # bookings whose deleted instructor is now NULL still count financially,
    # but must not create a fake instructor in the "Инструкторы" block.
    summary = _summarize(bookings, instructors, include_historical_unassigned=False)
    statuses = summary["statuses"]
    attention = []
    for count, label in (
        (statuses["no_show"], "Неявок"),
        (summary["unpaid"], "Неоплаченных записей"),
        (sum(statuses[item] for item in _PROBLEM_STATUSES), "Конфликтных/спорных"),
        (summary["unassigned_active"], "Активных записей без инструктора"),
    ):
        if count:
            attention.append(f"- {label}: {count}")
    if not attention:
        attention.append("- Критичных пунктов нет")

    lines = [f"📊 Отчёт за {target:%d.%m.%Y}", "", "Записи:"]
    lines += _status_lines(summary, len(bookings)) + [f"Новых клиентов: {new_clients}", "", "Финансы по записям:"]
    lines += _finance_lines(summary) + ["", "Услуги и каналы:"]
    lines += _service_lines(summary) + ["", "Инструкторы:"] + _instructor_lines(summary)
    loaded = [row for row in summary["instructors"] if row["total"] > 0]
    rated = [row for row in summary["instructors"] if row["rating"] is not None]
    if loaded:
        lines.append(f"Самый загруженный: {loaded[0]['name']}")
    if rated:
        best = max(rated, key=lambda row: (row["rating"], row["total"]))
        lines.append(f"Лучший рейтинг: {best['name']} {best['rating']:.1f}")
    lines += _daily_extra_lines(
        summary=summary,
        gender=gender_analytics,
        analytics_load=_analytics_load_count(bookings),
        week_load_by_date=week_load_by_date,
        package_purchases=package_purchases,
        cert_created=cert_created,
        cert_used=cert_used,
    ) + [
        "", "Требует внимания:", *attention,
        "", f"Следующий день ({next_date:%d.%m}):", f"Записей: {len(next_bookings)}",
    ]
    if next_bookings:
        nearest = next_bookings[0]
        instructor_name = nearest.instructor.name if nearest.instructor else "Без инструктора"
        client_name = nearest.client.name if nearest.client else "Без клиента"
        lines.append(f"Ближайшая: {nearest.start_time:%H:%M}, {instructor_name}, {client_name}")
    else:
        lines.append("Ближайшая: нет записей")
    return "\n".join(lines)


async def _build_today_report() -> str:
    """Preserve the previous helper contract while using Kazakhstan time."""
    return await _build_date_report(_today(), include_week_leader=True)


async def _build_all_time_report() -> str:
    async with async_session() as db:
        bookings = (await db.execute(
            select(Booking).options(selectinload(Booking.instructor)).order_by(Booking.booking_date, Booking.start_time)
        )).scalars().all()
        instructors = (await db.execute(
            select(Instructor).where(Instructor.is_active.is_(True)).order_by(Instructor.name)
        )).scalars().all()
        clients_total = await db.scalar(select(func.count()).select_from(Client).where(Client.is_deleted.is_(False))) or 0
        packages_total = await db.scalar(select(func.count()).select_from(ClientPackage)) or 0
        packages_active = await db.scalar(select(func.count()).select_from(ClientPackage).where(ClientPackage.is_active.is_(True))) or 0
        certificates_total = await db.scalar(select(func.count()).select_from(Certificate)) or 0
        certificates_used = await db.scalar(select(func.count()).select_from(Certificate).where(Certificate.is_used.is_(True))) or 0
        certificate_balance = await db.scalar(select(func.coalesce(func.sum(Certificate.remaining), 0))) or 0
        gender_analytics = await db.get(GenderAnalytics, 1)

    summary = _summarize(bookings, instructors)
    period = "записей пока нет"
    if bookings:
        period = f"{bookings[0].booking_date:%d.%m.%Y} — {bookings[-1].booking_date:%d.%m.%Y}"
    lines = [
        "📈 Отчёт за всё время", f"Период записей: {period}", "", "Бизнес:",
        f"Клиентов в базе: {clients_total}",
        f"Клиентов с записями: {len({booking.client_id for booking in bookings})}",
        f"Активных инструкторов: {len(instructors)}", "", "Записи:",
    ]
    lines += _status_lines(summary, len(bookings)) + ["", "Финансы по всем записям:"] + _finance_lines(summary)
    lines += ["", "Услуги и каналы:"] + _service_lines(summary)
    lines += ["", "Аналитика:", *_gender_analytics_lines(gender_analytics)]
    all_time_load_by_date: dict[date, int] = defaultdict(int)
    for booking in bookings:
        if booking.status in _ANALYTICS_LOAD_STATUSES:
            all_time_load_by_date[booking.booking_date] += 1
    busiest = _most_loaded_day(all_time_load_by_date)
    if busiest:
        busy_date, busy_count = busiest
        lines.append(f"Самый загруженный день: {busy_date:%d.%m.%Y} — {busy_count} записей")
    else:
        lines.append("Подтверждённых или завершённых записей пока нет")

    package_and_certificate_lines = []
    if packages_total > 0:
        package_and_certificate_lines.append(f"Активировано пакетов: {packages_total}")
    if packages_active > 0:
        package_and_certificate_lines.append(f"Активных пакетов: {packages_active}")
    if summary["package_lessons"] > 0:
        package_and_certificate_lines.append(f"Занятий по пакетам: {summary['package_lessons']}")
    if certificates_total > 0:
        package_and_certificate_lines.append(f"Сертификатов создано: {certificates_total}")
    if certificates_used > 0:
        package_and_certificate_lines.append(f"Сертификатов использовано: {certificates_used}")
    if certificate_balance > 0:
        package_and_certificate_lines.append(f"Остаток по сертификатам: {_money(certificate_balance)}")
    if package_and_certificate_lines:
        lines += ["", "Пакеты и сертификаты:", *package_and_certificate_lines]
    lines += ["", "Топ инструкторов по записям:"] + _instructor_lines(summary, limit=5)
    return "\n".join(lines)


def _split_report(text: str, limit: int = 4000) -> list[str]:
    chunks, current = [], ""
    for line in text.splitlines():
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        while len(line) > limit:
            chunks.append(line[:limit])
            line = line[limit:]
        current = line
    if current or not chunks:
        chunks.append(current)
    return chunks


async def _send_report(message: types.Message, text: str) -> None:
    chunks = _split_report(text)
    for index, chunk in enumerate(chunks):
        await message.answer(chunk, reply_markup=REPORT_KEYBOARD if index == len(chunks) - 1 else None)


async def _guard(message: types.Message) -> bool:
    if message.from_user and message.from_user.id in _allowed_user_ids:
        return True
    await message.answer("Для доступа подтвердите номер владельца.", reply_markup=SHARE_OWNER_PHONE_KEYBOARD)
    return False


async def _guard_callback(callback: types.CallbackQuery) -> bool:
    if callback.from_user and callback.from_user.id in _allowed_user_ids:
        return True
    await callback.answer("Сначала подтвердите номер владельца.", show_alert=True)
    if callback.message:
        await callback.message.answer("Для доступа подтвердите номер владельца.", reply_markup=SHARE_OWNER_PHONE_KEYBOARD)
    return False


@router.message(CommandStart())
async def start(message: types.Message):
    if message.from_user and message.from_user.id in _allowed_user_ids:
        await message.answer("Доступ открыт. Выберите отчёт.", reply_markup=REPORT_KEYBOARD)
        return
    await message.answer("Подтвердите номер владельца.", reply_markup=SHARE_OWNER_PHONE_KEYBOARD)


@router.message(F.contact)
async def handle_contact(message: types.Message):
    contact = message.contact
    if not message.from_user or contact.user_id != message.from_user.id or not _is_owner_phone(contact.phone_number):
        await message.answer("Доступ запрещен.")
        return
    _allowed_user_ids.add(message.from_user.id)
    await message.answer("Доступ открыт. Выберите отчёт.", reply_markup=REPORT_KEYBOARD)


@router.message(F.text.in_({"📊 Сегодня", "Сегодня", "Отчет", "отчет", "ОТЧЕТ", "Отчёт", "отчёт"}))
async def report_today(message: types.Message):
    if await _guard(message):
        await _send_report(message, await _build_today_report())


@router.message(F.text.in_({"🗓 Выбрать дату", "Выбрать дату"}))
async def choose_report_date(message: types.Message):
    if await _guard(message):
        today = _today()
        await message.answer("Выберите дату:", reply_markup=_calendar_keyboard(today.year, today.month))


@router.message(F.text.in_({"📈 За всё время", "За всё время", "За все время"}))
async def report_all_time(message: types.Message):
    if await _guard(message):
        await _send_report(message, await _build_all_time_report())


@router.callback_query(F.data == "report:noop")
async def calendar_noop(callback: types.CallbackQuery):
    await callback.answer()


@router.callback_query(F.data.startswith("report:calendar:"))
async def change_calendar_month(callback: types.CallbackQuery):
    if not await _guard_callback(callback):
        return
    try:
        year_text, month_text = callback.data.rsplit(":", 1)[1].split("-")
        year, month = int(year_text), int(month_text)
        if not (_MIN_YEAR <= year <= _MAX_YEAR and 1 <= month <= 12):
            raise ValueError
    except (AttributeError, TypeError, ValueError):
        await callback.answer("Неверная дата.", show_alert=True)
        return
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=_calendar_keyboard(year, month))
    await callback.answer()


@router.callback_query(F.data.startswith("report:date:"))
async def report_selected_date(callback: types.CallbackQuery):
    if not await _guard_callback(callback):
        return
    try:
        target = date.fromisoformat(callback.data.rsplit(":", 1)[1])
        if not (_MIN_YEAR <= target.year <= _MAX_YEAR):
            raise ValueError
    except (AttributeError, TypeError, ValueError):
        await callback.answer("Неверная дата.", show_alert=True)
        return
    await callback.answer()
    if callback.message:
        await _send_report(callback.message, await _build_date_report(target))
