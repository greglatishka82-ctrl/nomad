import os
import tempfile
import unittest
from datetime import date, datetime, time, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from sqlalchemy import select


_db_file = Path(tempfile.gettempdir()) / "nomad_backend_security_tests.sqlite3"
_db_file.unlink(missing_ok=True)
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_db_file.as_posix()}"
os.environ["SECRET_KEY"] = "test-secret-key"

from app.bot.handlers import (
    _build_date_buttons,
    _learning_history_page,
    _send_learning_history_page,
    my_bookings,
    process_phone,
    process_rating,
    relay_telegram_support_reply,
    send_lesson_reminders,
    send_rating_requests,
    support_end,
    support_start,
    support_send_message,
)
from app.bot.instructor_handlers import (
    client_arrived,
    client_arrived_confirmed,
    client_no_show,
    client_no_show_confirmed,
    lesson_done,
    run_automatic_lesson_transitions,
)
from app.bot.reporting import (
    _allowed_user_ids,
    _build_all_time_report,
    _build_date_report,
    _build_today_report,
    _calendar_keyboard,
    _guard_callback,
    _is_owner_phone,
    _split_report,
)
from app.config import TIMEZONE
from app.config import settings as app_settings
from app.database import async_session, init_db
from app.main import app
from app.models.models import (
    Booking,
    AuditLog,
    Certificate,
    Client,
    ClientBlock,
    ClientPackage,
    Event,
    GenderAnalytics,
    Instructor,
    MobileSession,
    Package,
    RatingRecord,
    SupportMessage,
    Vehicle,
    now_kz,
)
from app.services.booking_service import get_available_slots, has_available_vehicle, reserve_available_vehicle


class RecordingBot:
    def __init__(self):
        self.recipients = []
        self.messages = []

    async def send_message(self, chat_id, *_args, **_kwargs):
        self.recipients.append(chat_id)
        self.messages.append((chat_id, _args[0] if _args else _kwargs.get("text")))


class RecordingInstructorBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append((chat_id, text, kwargs))


class RecordingHistoryMessage:
    def __init__(self):
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))


class RecordingClientMessage(RecordingHistoryMessage):
    def __init__(self, telegram_id: str):
        super().__init__()
        self.from_user = type("User", (), {"id": telegram_id})()


class RecordingRatingCallback:
    def __init__(self, booking_id: int):
        self.data = f"rate:good:{booking_id}"
        self.answers = []
        self.message = None

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))


class RecordingInstructorMessage:
    def __init__(self, text="Карточка занятия"):
        self.text = text
        self.edits = []

    async def edit_text(self, text, **kwargs):
        self.text = text
        self.edits.append((text, kwargs))


class RecordingInstructorCallback:
    def __init__(self, action: str, booking_id: int, telegram_id: str):
        self.data = f"{action}:{booking_id}"
        self.from_user = type("User", (), {"id": telegram_id})()
        self.message = RecordingInstructorMessage()
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))


class RecordingSupportState:
    def __init__(self, data=None):
        self.states = []
        self.data = data or {}

    async def set_state(self, state):
        self.states.append(state)

    async def clear(self):
        self.states.clear()

    async def get_data(self):
        return self.data


class SecurityRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await init_db()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://testserver"
        )

    async def asyncTearDown(self):
        await self.client.aclose()

    async def _register(self):
        response = await self.client.post("/api/mobile/auth/register", json={
            "name": "Тестовый клиент",
            "phone": "+77000000001",
            "password": "Secret123!",
            "password_confirmation": "Secret123!",
        })
        if response.status_code == 400:
            response = await self.client.post("/api/mobile/auth/login", json={
                "phone": "+77000000001", "password": "Secret123!",
            })
        self.assertIn(response.status_code, (200, 201), response.text)
        return response.json()

    async def test_client_profile_and_support_actions_are_recorded_as_events(self):
        tokens = await self._register()
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        profile_response = await self.client.put(
            "/api/mobile/profile",
            json={"name": "Тестовый клиент"},
            headers=headers,
        )
        self.assertEqual(profile_response.status_code, 200, profile_response.text)

        support_response = await self.client.post(
            "/api/mobile/support/messages",
            json={"text": "Проверка журнала событий"},
            headers=headers,
        )
        self.assertEqual(support_response.status_code, 201, support_response.text)

        async with async_session() as db:
            user = (await db.execute(select(Client).where(
                Client.phone == "+77000000001"
            ))).scalar_one()
            events = (await db.execute(select(Event).where(
                Event.client_id == user.id,
                Event.source == "mobile",
            ))).scalars().all()
            event_types = {event.event_type for event in events}

        self.assertIn("client_profile_updated", event_types)
        self.assertIn("client_support_message", event_types)

    async def test_booking_date_buttons_never_search_beyond_five_day_window(self):
        db = MagicMock()
        query_result = MagicMock()
        query_result.scalars.return_value.all.return_value = []
        db.execute = AsyncMock(return_value=query_result)

        with patch("app.bot.handlers.has_available_instructors", new=AsyncMock(return_value=False)) as availability:
            buttons = await _build_date_buttons(db, "training", "automatic", "any")

        self.assertEqual(buttons, [])
        self.assertLessEqual(availability.await_count, 5)
        checked_dates = [call.args[1] for call in availability.await_args_list]
        self.assertLessEqual((max(checked_dates) - min(checked_dates)).days, 4)

    async def test_date_availability_stops_after_first_free_slot(self):
        instructor = MagicMock()
        instructor.working_hours_start = time(9)
        instructor.working_hours_end = time(19)

        with (
            patch("app.services.booking_service._get_active_instructors", new=AsyncMock(return_value=[instructor])),
            patch(
                "app.services.booking_service._get_effective_schedule",
                new=AsyncMock(return_value=(time(9), time(19), None, None)),
            ),
            patch("app.services.booking_service.get_training_location", new=AsyncMock(return_value="Циолковского 30")),
            patch("app.services.booking_service._count_available_instructors", new=AsyncMock(return_value=1)) as count_available,
        ):
            slots = await get_available_slots(
                MagicMock(), date.today() + timedelta(days=1), "training", "automatic",
                "Циолковского 30", stop_after_first=True,
            )

        self.assertEqual(slots, [time(9)])
        count_available.assert_awaited_once()

    async def test_owner_report_supports_calendar_day_and_all_time_summary(self):
        report_date = date(2034, 5, 17)
        week_start = report_date - timedelta(days=report_date.weekday())
        async with async_session() as db:
            instructor = Instructor(name="Инструктор отчёта", transmission="both", gender="any", rating=4.9)
            client = Client(
                name="Клиент отчёта", phone="+77000000217",
                created_at=datetime(2034, 5, 17, 9, 30),
            )
            package = Package(
                name="Пакет отчёта", sessions_count=6, price=55000,
                code="REPORT-20340517", validity_days=30,
            )
            certificate = Certificate(
                code="CERT-REPORT-20340517", nominal=15000, remaining=5000,
                is_used=True, created_at=datetime(2034, 5, 17, 8, 0),
                used_at=datetime(2034, 5, 17, 11, 0),
            )
            db.add_all([
                instructor, client, package, certificate,
                GenderAnalytics(
                    id=1, male_count=7, female_count=9, unknown_count=1,
                    total_count=17, model="test", updated_at=datetime(2034, 5, 17, 12, 0),
                ),
            ])
            await db.flush()
            db.add(ClientPackage(
                client_id=client.id, package_id=package.id, remaining_sessions=5,
                purchased_at=datetime(2034, 5, 17, 10, 0),
            ))
            db.add_all([
                Booking(
                    client_id=client.id, instructor_id=instructor.id, package_id=package.id,
                    service_type="training", transmission="automatic", location="Циолковского 30",
                    booking_date=report_date, start_time=time(10), end_time=time(11),
                    status="completed", price=10000, payment_status="paid", paid_amount=10000,
                    certificate_amount=0, referral_discount_amount=0, source="mobile",
                ),
                Booking(
                    client_id=client.id, instructor_id=instructor.id,
                    service_type="exam", transmission="manual", location="Циолковского 30",
                    booking_date=report_date, start_time=time(12), end_time=time(12, 20),
                    status="no_show", price=5000, payment_status="unpaid", paid_amount=0,
                    source="telegram",
                ),
                Booking(
                    client_id=client.id, instructor_id=instructor.id,
                    service_type="training", transmission="automatic", location="Циолковского 30",
                    booking_date=report_date + timedelta(days=1), start_time=time(9), end_time=time(10),
                    status="confirmed", price=10000, source="manual",
                ),
                Booking(
                    client_id=client.id, instructor_id=None,
                    service_type="training", transmission="automatic", location="Циолковского 30",
                    booking_date=report_date, start_time=time(13), end_time=time(14),
                    status="completed", price=10000, payment_status="paid", paid_amount=10000,
                    source="manual",
                ),
                *[
                    Booking(
                        client_id=client.id, instructor_id=instructor.id,
                        service_type="training", transmission="automatic", location="Циолковского 30",
                        booking_date=week_start, start_time=time(hour), end_time=time(hour + 1),
                        status="confirmed", price=10000, source="telegram",
                    )
                    for hour in (9, 10, 11)
                ],
            ])
            await db.commit()

        daily = await _build_date_report(report_date)
        self.assertIn("Отчёт за 17.05.2034", daily)
        self.assertIn("Всего: 3", daily)
        self.assertIn("Явка: 67%", daily)
        self.assertIn("Оплачено: 20 000 ₸", daily)
        self.assertIn("К оплате: 5 000 ₸", daily)
        self.assertIn("Новых клиентов: 1", daily)
        self.assertIn("Активировано пакетов: 1", daily)
        self.assertIn("Использовано сертификатов: 1", daily)
        self.assertIn("Приложение: 1", daily)
        self.assertIn("Мужчины: 7", daily)
        self.assertIn("Женщины: 9", daily)
        self.assertIn("Нагрузка дня (подтверждённые и завершённые): 2", daily)
        self.assertNotIn("История удалённого инструктора", daily)
        self.assertNotIn("Активных записей без инструктора", daily)
        self.assertNotIn("Скидка сертификатами:", daily)
        self.assertNotIn("Реферальные скидки:", daily)
        self.assertNotIn("Самый загруженный с понедельника:", daily)
        self.assertIn("Ближайшая: 09:00, Инструктор отчёта, Клиент отчёта", daily)

        with patch("app.bot.reporting._today", return_value=report_date):
            today_report = await _build_today_report()
        self.assertIn(
            f"Самый загруженный с понедельника: Понедельник ({week_start:%d.%m}) — 3 записей",
            today_report,
        )

        all_time = await _build_all_time_report()
        self.assertIn("Отчёт за всё время", all_time)
        self.assertIn("Период записей:", all_time)
        self.assertIn("Клиентов с записями:", all_time)
        self.assertIn("Пакеты и сертификаты:", all_time)
        self.assertIn("Мужчины: 7", all_time)
        self.assertIn("Женщины: 9", all_time)
        self.assertIn("История удалённого инструктора", all_time)
        self.assertIn(f"Самый загруженный день: {week_start:%d.%m.%Y} — 3 записей", all_time)

        calendar_markup = _calendar_keyboard(2034, 5)
        callbacks = {
            button.callback_data
            for row in calendar_markup.inline_keyboard
            for button in row
        }
        self.assertIn("report:date:2034-05-17", callbacks)
        self.assertIn("report:calendar:2034-04", callbacks)
        self.assertIn("report:calendar:2034-06", callbacks)

        previous_phone = app_settings.REPORT_OWNER_PHONE
        try:
            app_settings.REPORT_OWNER_PHONE = "+7 (702) 718-22-33"
            self.assertTrue(_is_owner_phone("87027182233"))
            self.assertFalse(_is_owner_phone("+77000000000"))
            self.assertFalse(_is_owner_phone("2233"))
        finally:
            app_settings.REPORT_OWNER_PHONE = previous_phone

        chunks = _split_report("строка\n" * 1500)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 4000 for chunk in chunks))

        class RecordingCallback:
            def __init__(self):
                self.from_user = type("User", (), {"id": 734001})()
                self.message = RecordingHistoryMessage()
                self.answers = []

            async def answer(self, text=None, **kwargs):
                self.answers.append((text, kwargs))

        callback = RecordingCallback()
        _allowed_user_ids.discard(callback.from_user.id)
        self.assertFalse(await _guard_callback(callback))
        self.assertTrue(callback.answers[0][1]["show_alert"])
        self.assertIn("подтвердите номер", callback.message.answers[0][0])
        _allowed_user_ids.add(callback.from_user.id)
        try:
            self.assertTrue(await _guard_callback(callback))
        finally:
            _allowed_user_ids.discard(callback.from_user.id)

    async def test_refresh_token_cannot_open_profile_and_logout_revokes_session(self):
        tokens = await self._register()
        refresh_as_access = await self.client.get(
            "/api/mobile/auth/me",
            headers={"Authorization": f"Bearer {tokens['refresh_token']}"},
        )
        self.assertEqual(refresh_as_access.status_code, 401)

        access_headers = {"Authorization": f"Bearer {tokens['access_token']}"}
        self.assertEqual((await self.client.get("/api/mobile/auth/me", headers=access_headers)).status_code, 200)
        self.assertEqual((await self.client.post("/api/mobile/auth/logout", headers=access_headers)).status_code, 200)
        self.assertEqual((await self.client.get("/api/mobile/auth/me", headers=access_headers)).status_code, 401)
        self.assertEqual((await self.client.post(
            "/api/mobile/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )).status_code, 401)

    async def test_single_manual_vehicle_hides_overlapping_manual_slot(self):
        """Several manual-capable instructors must not share one physical car."""
        slot_date = date(2032, 2, 3)
        async with async_session() as db:
            vehicle = (await db.execute(
                select(Vehicle).where(Vehicle.name == "Машина 1")
            )).scalar_one()
            first_instructor = Instructor(name="МКПП 1", transmission="both", days_off="")
            second_instructor = Instructor(name="МКПП 2", transmission="both", days_off="")
            client = Client(name="Клиент МКПП", phone="+77000000999")
            db.add_all([first_instructor, second_instructor, client])
            await db.flush()
            db.add(Booking(
                client_id=client.id, instructor_id=first_instructor.id, vehicle_id=vehicle.id,
                service_type="training", transmission="manual", location="Тестовая площадка",
                booking_date=slot_date, start_time=time(10, 0), end_time=time(11, 0),
                status="confirmed", price=10000, source="mobile",
            ))
            await db.flush()

            self.assertFalse(await has_available_vehicle(
                db, slot_date, time(10, 20), time(10, 40), "manual"
            ))
            self.assertIsNone(await reserve_available_vehicle(
                db, slot_date, time(10, 20), time(10, 40), "manual"
            ))
            await db.commit()

    async def test_vehicle_under_repair_is_not_available_for_site_bookings(self):
        """The public API and Telegram bots share this availability rule."""
        slot_date = date(2042, 2, 3)
        async with async_session() as db:
            vehicle = (await db.execute(
                select(Vehicle).where(Vehicle.name == "Машина 1")
            )).scalar_one()
            vehicle.is_under_repair = True
            self.assertFalse(await has_available_vehicle(
                db, slot_date, time(10, 0), time(11, 0), "manual"
            ))
            self.assertIsNone(await reserve_available_vehicle(
                db, slot_date, time(10, 0), time(11, 0), "manual"
            ))
            vehicle.is_under_repair = False
            await db.commit()

    async def test_deleted_client_session_is_rejected_by_mobile_api(self):
        tokens = await self._register()
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}
        async with async_session() as db:
            client = (await db.execute(
                select(Client).where(Client.phone == "+77000000001")
            )).scalar_one()
            original_name = client.name
            original_password_hash = client.password_hash
            original_telegram_id = client.telegram_id
            client.is_deleted = True
            client.telegram_id = "deleted-apk-owner"
            block = ClientBlock(
                client_id=client.id,
                blocked_until=now_kz() + timedelta(days=1),
                reason="Старое ограничение удалённого профиля",
            )
            db.add(block)
            await db.commit()
            block_id = block.id

        response = await self.client.get("/api/mobile/auth/me", headers=headers)
        self.assertEqual(response.status_code, 401, response.text)
        login = await self.client.post("/api/mobile/auth/login", json={
            "phone": "+77000000001", "password": "Secret123!",
        })
        self.assertEqual(login.status_code, 401, login.text)
        register = await self.client.post("/api/mobile/auth/register", json={
            "name": "Повторная регистрация", "phone": "+77000000001",
            "password": "secure1", "password_confirmation": "secure1",
        })
        self.assertEqual(register.status_code, 201, register.text)
        new_headers = {"Authorization": f"Bearer {register.json()['access_token']}"}
        self.assertEqual((await self.client.get("/api/mobile/auth/me", headers=new_headers)).status_code, 200)
        self.assertEqual((await self.client.get("/api/mobile/auth/me", headers=headers)).status_code, 401)

        # Other tests in this class intentionally reuse the same registered
        # fixture, so restore its credentials after all deleted-user checks.
        async with async_session() as db:
            client = (await db.execute(
                select(Client).where(Client.phone == "+77000000001")
            )).scalar_one()
            block = await db.get(ClientBlock, block_id)
            self.assertFalse(client.is_deleted)
            self.assertEqual(client.name, "Повторная регистрация")
            self.assertIsNone(client.telegram_id)
            self.assertLessEqual(block.blocked_until, now_kz())
            client.name = original_name
            client.password_hash = original_password_hash
            client.telegram_id = original_telegram_id
            await db.commit()

    async def test_deleted_client_can_register_again_through_telegram(self):
        async with async_session() as db:
            client = Client(
                name="Удалённый Telegram-клиент",
                phone="+77000000091",
                telegram_id="111111",
                password_hash="old-mobile-password",
                is_deleted=True,
            )
            db.add(client)
            await db.flush()
            db.add_all([
                ClientBlock(
                    client_id=client.id,
                    blocked_until=now_kz() + timedelta(days=1),
                    reason="Старое ограничение",
                ),
                MobileSession(
                    id="deleted-telegram-session",
                    client_id=client.id,
                    expires_at=now_kz() + timedelta(days=1),
                ),
            ])
            await db.commit()
            client_id = client.id

        message = RecordingClientMessage("222222")
        message.contact = None
        message.text = "+7 700 000 00 91"
        state = RecordingSupportState({"client_name": "Новый Telegram-клиент"})
        with patch("app.bot.handlers._finalize_booking", new=AsyncMock()) as finalize:
            await process_phone(message, state)

        self.assertFalse(any("удалён администратором" in text for text, _ in message.answers))
        finalize.assert_awaited_once()
        async with async_session() as db:
            client = await db.get(Client, client_id)
            block = (await db.execute(select(ClientBlock).where(
                ClientBlock.client_id == client_id,
            ))).scalar_one()
            session = await db.get(MobileSession, "deleted-telegram-session")
            self.assertFalse(client.is_deleted)
            self.assertEqual(client.name, "Новый Telegram-клиент")
            self.assertEqual(client.telegram_id, "222222")
            self.assertIsNone(client.password_hash)
            self.assertLessEqual(block.blocked_until, now_kz())
            self.assertFalse(session.is_active)

    async def test_plain_telegram_reply_is_saved_for_admin_support(self):
        """Ответ после сообщения администратора не зависит от FSM-состояния."""
        async with async_session() as db:
            client = Client(
                name="Клиент поддержки", phone="+77000000081", telegram_id="99887766",
                support_chat_opened_at=now_kz(),
            )
            db.add(client)
            await db.flush()
            db.add(SupportMessage(
                client_id=client.id, channel="client", sender="admin",
                text="Чем могу помочь?", is_admin_read=True,
            ))
            await db.commit()

        message = RecordingClientMessage("99887766")
        message.text = "Отвечаю прямо на сообщение администратора"
        await relay_telegram_support_reply(message)

        self.assertEqual(message.answers[0][0], "✅ Сообщение передано администратору.")
        async with async_session() as db:
            stored = (await db.execute(select(SupportMessage).where(
                SupportMessage.client_id == client.id,
                SupportMessage.sender == "user",
            ))).scalar_one()
        self.assertEqual(stored.channel, "telegram")
        self.assertEqual(stored.text, message.text)
        self.assertFalse(stored.is_admin_read)

    async def test_support_mode_message_keeps_existing_delivery_path(self):
        async with async_session() as db:
            db.add(Client(
                name="Клиент режима поддержки", phone="+77000000082", telegram_id="99887767",
            ))
            await db.commit()

        state = RecordingSupportState()
        await support_start(RecordingClientMessage("99887767"), state)
        self.assertTrue(state.states)
        async with async_session() as db:
            client = (await db.execute(select(Client).where(
                Client.telegram_id == "99887767",
            ))).scalar_one()
            self.assertIsNotNone(client.support_chat_opened_at)
            self.assertIsNone(client.support_chat_closed_at)

        message = RecordingClientMessage("99887767")
        message.text = "Сообщение через кнопку поддержки"
        await support_send_message(message, state)

        self.assertEqual(message.answers[0][0], "✅ Сообщение отправлено. Ожидайте ответа администратора.")
        async with async_session() as db:
            stored = (await db.execute(select(SupportMessage).where(
                SupportMessage.text == message.text,
                SupportMessage.sender == "user",
            ))).scalar_one()
        self.assertEqual(stored.channel, "telegram")
        self.assertFalse(stored.is_admin_read)

    async def test_client_can_close_support_chat_and_cannot_reply_afterwards(self):
        async with async_session() as db:
            db.add(Client(
                name="Клиент закрытия чата", phone="+77000000083", telegram_id="99887768",
                support_chat_opened_at=now_kz(),
            ))
            await db.commit()

        await support_end(RecordingClientMessage("99887768"), RecordingSupportState())
        async with async_session() as db:
            client = (await db.execute(select(Client).where(
                Client.telegram_id == "99887768",
            ))).scalar_one()
            self.assertIsNotNone(client.support_chat_closed_at)

        reply = RecordingClientMessage("99887768")
        reply.text = "Сообщение после закрытия"
        await relay_telegram_support_reply(reply)
        self.assertIn("Чат с администратором завершён", reply.answers[0][0])
        async with async_session() as db:
            stored = await db.scalar(select(SupportMessage).where(
                SupportMessage.text == reply.text,
            ))
        self.assertIsNone(stored)

    async def test_avatar_rejects_non_image_and_static_route_is_not_shadowed(self):
        tokens = await self._register()
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}
        response = await self.client.post(
            "/api/mobile/profile/avatar",
            headers=headers,
            json={"avatar_base64": "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg=="},
        )
        self.assertEqual(response.status_code, 400)
        slots = await self.client.get("/api/mobile/bookings/available-slots")
        self.assertEqual(slots.status_code, 200, slots.text)

    async def test_rating_request_is_sent_only_to_telegram_clients(self):
        completed_long_ago = now_kz() - timedelta(hours=2)
        completed_recently = now_kz() - timedelta(minutes=30)
        async with async_session() as db:
            instructor = Instructor(name="Инструктор для оценки", transmission="both", gender="any")
            app_client = Client(
                name="Клиент приложения", phone="+77000000021", password_hash="app-password-hash"
            )
            admin_client = Client(name="Клиент администратора", phone="+77000000023")
            telegram_client = Client(
                name="Клиент Telegram", phone="+77000000022", telegram_id="123456789"
            )
            hybrid_client = Client(
                name="Клиент двух каналов",
                phone="+77000000024",
                telegram_id="987654321",
                password_hash="app-password-hash",
            )
            old_app_client = Client(
                name="Клиент старого занятия",
                phone="+77000000025",
                password_hash="app-password-hash",
            )
            db.add_all([
                instructor, app_client, admin_client, telegram_client, hybrid_client, old_app_client
            ])
            await db.flush()

            booking_fields = {
                "instructor_id": instructor.id,
                "service_type": "training",
                "transmission": "manual",
                "location": "Тестовая площадка",
                "booking_date": date.today() - timedelta(days=1),
                "start_time": time(10, 0),
                "end_time": time(11, 0),
                "price": 1000,
            }
            completed_fields = {
                **booking_fields,
                "status": "completed",
                "completed_at": completed_long_ago,
            }
            app_booking = Booking(client_id=app_client.id, source="mobile", **completed_fields)
            admin_booking = Booking(client_id=admin_client.id, source="admin", **completed_fields)
            telegram_booking = Booking(client_id=telegram_client.id, source="telegram", **completed_fields)
            hybrid_booking = Booking(client_id=hybrid_client.id, source="telegram", **completed_fields)
            old_booking = Booking(
                client_id=old_app_client.id,
                source="mobile",
                **{**completed_fields, "booking_date": date.today() - timedelta(days=10)},
            )
            confirmed_booking = Booking(
                client_id=telegram_client.id, source="telegram",
                **{**booking_fields, "status": "confirmed"},
            )
            cancelled_booking = Booking(
                client_id=telegram_client.id, source="telegram",
                **{**booking_fields, "status": "cancelled", "completed_at": completed_long_ago},
            )
            rescheduled_booking = Booking(
                client_id=telegram_client.id, source="telegram",
                **{**booking_fields, "status": "reschedule_pending", "completed_at": completed_long_ago},
            )
            recent_booking = Booking(
                client_id=telegram_client.id, source="telegram",
                **{**booking_fields, "status": "completed", "completed_at": completed_recently},
            )
            completion_without_timestamp = Booking(
                client_id=telegram_client.id, source="telegram",
                **{**booking_fields, "status": "completed", "completed_at": None},
            )
            db.add_all([
                app_booking, admin_booking, telegram_booking, hybrid_booking, old_booking,
                confirmed_booking, cancelled_booking, rescheduled_booking, recent_booking,
                completion_without_timestamp,
            ])
            await db.commit()
            booking_ids = {
                "app": app_booking.id,
                "admin": admin_booking.id,
                "telegram": telegram_booking.id,
                "hybrid": hybrid_booking.id,
                "old": old_booking.id,
                "confirmed": confirmed_booking.id,
                "cancelled": cancelled_booking.id,
                "rescheduled": rescheduled_booking.id,
                "recent": recent_booking.id,
                "without_timestamp": completion_without_timestamp.id,
            }

        bot = RecordingBot()
        await send_rating_requests(bot)

        self.assertEqual(set(bot.recipients), {123456789, 987654321})
        self.assertEqual(len(bot.recipients), 2)
        async with async_session() as db:
            app_booking = await db.get(Booking, booking_ids["app"])
            admin_booking = await db.get(Booking, booking_ids["admin"])
            telegram_booking = await db.get(Booking, booking_ids["telegram"])
            hybrid_booking = await db.get(Booking, booking_ids["hybrid"])
            old_booking = await db.get(Booking, booking_ids["old"])
            confirmed_booking = await db.get(Booking, booking_ids["confirmed"])
            cancelled_booking = await db.get(Booking, booking_ids["cancelled"])
            rescheduled_booking = await db.get(Booking, booking_ids["rescheduled"])
            recent_booking = await db.get(Booking, booking_ids["recent"])
            completion_without_timestamp = await db.get(Booking, booking_ids["without_timestamp"])
            self.assertFalse(app_booking.rating_sent)
            self.assertFalse(admin_booking.rating_sent)
            self.assertTrue(telegram_booking.rating_sent)
            self.assertTrue(hybrid_booking.rating_sent)
            self.assertFalse(old_booking.rating_sent)
            self.assertFalse(confirmed_booking.rating_sent)
            self.assertFalse(cancelled_booking.rating_sent)
            self.assertFalse(rescheduled_booking.rating_sent)
            self.assertFalse(recent_booking.rating_sent)
            self.assertFalse(completion_without_timestamp.rating_sent)
            self.assertEqual(telegram_booking.status, "completed")
            self.assertEqual(telegram_booking.completed_at, completed_long_ago)
            self.assertEqual(confirmed_booking.status, "confirmed")
            self.assertEqual(cancelled_booking.status, "cancelled")
            self.assertEqual(rescheduled_booking.status, "reschedule_pending")
            # The module keeps one SQLite database for the whole class. Mark
            # these deliberately deferred fixtures as handled so a later test
            # that advances the clock cannot mistake them for its own rows.
            recent_booking.rating_sent = True
            completion_without_timestamp.rating_sent = True
            await db.commit()

        for booking_id in (booking_ids["cancelled"], booking_ids["rescheduled"]):
            callback = RecordingRatingCallback(booking_id)
            await process_rating(callback)
            self.assertIn("только после завершённого занятия", callback.answers[0][0])

        async with async_session() as db:
            invalid_ratings = (await db.execute(
                select(RatingRecord).where(
                    RatingRecord.booking_id.in_([
                        booking_ids["cancelled"], booking_ids["rescheduled"],
                    ])
                )
            )).scalars().all()
            self.assertEqual(invalid_ratings, [])

    async def test_rating_request_waits_one_hour_after_automatic_completion(self):
        completed_at = datetime(2035, 5, 17, 9, 10)

        async with async_session() as db:
            instructor = Instructor(name="Инструктор автооценки", transmission="both", gender="any")
            client = Client(
                name="Клиент автооценки", phone="+77000000026", telegram_id="123450026"
            )
            db.add_all([instructor, client])
            await db.flush()
            booking = Booking(
                client_id=client.id,
                instructor_id=instructor.id,
                service_type="training",
                transmission="manual",
                location="Тестовая площадка",
                booking_date=completed_at.date(),
                start_time=time(8, 0),
                end_time=time(9, 0),
                status="in_progress",
                price=1000,
                source="telegram",
            )
            db.add(booking)
            await db.commit()
            booking_id = booking.id

        with patch("app.bot.instructor_handlers.now_kz", return_value=completed_at):
            await run_automatic_lesson_transitions()

        async with async_session() as db:
            booking = await db.get(Booking, booking_id)
            self.assertEqual(booking.status, "completed")
            self.assertEqual(booking.completed_at, completed_at)
            self.assertFalse(booking.rating_sent)

        def datetime_at(moment):
            class FixedDateTime(datetime):
                @classmethod
                def now(cls, timezone=None):
                    return moment.replace(tzinfo=timezone) if timezone else moment

            return FixedDateTime

        early_bot = RecordingBot()
        with patch("app.bot.handlers.datetime", datetime_at(completed_at + timedelta(minutes=59))):
            await send_rating_requests(early_bot)
        self.assertNotIn(123450026, early_bot.recipients)

        due_bot = RecordingBot()
        with patch("app.bot.handlers.datetime", datetime_at(completed_at + timedelta(hours=1, minutes=1))):
            await send_rating_requests(due_bot)
        self.assertIn(123450026, due_bot.recipients)

        async with async_session() as db:
            booking = await db.get(Booking, booking_id)
            self.assertTrue(booking.rating_sent)
            self.assertEqual(booking.status, "completed")
            self.assertEqual(booking.completed_at, completed_at)

    async def test_instructor_buttons_cannot_change_booking_before_scheduled_times(self):
        starts_at = datetime(2035, 5, 18, 11, 0)
        ends_at = datetime(2035, 5, 18, 12, 0)
        async with async_session() as db:
            instructor = Instructor(
                name="Инструктор временных ограничений",
                telegram_id="900001",
                transmission="both",
                gender="any",
            )
            client = Client(name="Клиент временных ограничений", phone="+77000000901")
            db.add_all([instructor, client])
            await db.flush()
            booking = Booking(
                client_id=client.id,
                instructor_id=instructor.id,
                service_type="training",
                transmission="manual",
                location="Тестовая площадка",
                booking_date=starts_at.date(),
                start_time=starts_at.time(),
                end_time=ends_at.time(),
                status="confirmed",
                price=1000,
                source="telegram",
            )
            db.add(booking)
            await db.commit()
            booking_id = booking.id

        early_arrival = RecordingInstructorCallback("inst_arrived", booking_id, "900001")
        with patch("app.bot.instructor_handlers.now_kz", return_value=starts_at - timedelta(minutes=1)):
            await client_arrived(early_arrival)
        self.assertIn("не раньше 18.05.2035 11:00", early_arrival.answers[0][0])

        async with async_session() as db:
            booking = await db.get(Booking, booking_id)
            self.assertEqual(booking.status, "confirmed")
            early_actions = (await db.execute(
                select(AuditLog).where(AuditLog.details.contains(f"запись #{booking_id},"))
            )).scalars().all()
            self.assertEqual(early_actions, [])

        on_time_arrival = RecordingInstructorCallback("inst_arrived", booking_id, "900001")
        with patch("app.bot.instructor_handlers.now_kz", return_value=starts_at):
            await client_arrived(on_time_arrival)

        self.assertIn("Вы уверены, что клиент пришёл?", on_time_arrival.message.text)
        confirmed_arrival = RecordingInstructorCallback("inst_arrived_yes", booking_id, "900001")
        confirmed_arrival.message = on_time_arrival.message
        with patch("app.bot.instructor_handlers.now_kz", return_value=starts_at):
            await client_arrived_confirmed(confirmed_arrival)

        early_completion = RecordingInstructorCallback("inst_done", booking_id, "900001")
        with patch("app.bot.instructor_handlers.now_kz", return_value=ends_at - timedelta(minutes=1)):
            await lesson_done(early_completion)
        self.assertIn("не раньше 18.05.2035 12:00", early_completion.answers[0][0])

        async with async_session() as db:
            booking = await db.get(Booking, booking_id)
            self.assertEqual(booking.status, "in_progress")
            self.assertEqual(booking.payment_status, "unpaid")

        on_time_completion = RecordingInstructorCallback("inst_done", booking_id, "900001")
        with patch("app.bot.instructor_handlers.now_kz", return_value=ends_at):
            await lesson_done(on_time_completion)

        async with async_session() as db:
            booking = await db.get(Booking, booking_id)
            self.assertEqual(booking.status, "completed")
            self.assertEqual(booking.payment_status, "paid")
            self.assertEqual(booking.completed_at, ends_at)

    async def test_late_arrival_prompt_is_sent_once_and_no_arrival_is_not_automatic(self):
        starts_at = datetime(2035, 5, 19, 11, 0)
        async with async_session() as db:
            instructor = Instructor(
                name="Инструктор проверки опоздания",
                telegram_id="900002",
                transmission="both",
                gender="any",
            )
            client = Client(name="Клиент проверки опоздания", phone="+77000000902")
            db.add_all([instructor, client])
            await db.flush()
            booking = Booking(
                client_id=client.id,
                instructor_id=instructor.id,
                service_type="training",
                transmission="manual",
                location="Тестовая площадка",
                booking_date=starts_at.date(),
                start_time=starts_at.time(),
                end_time=time(12, 0),
                status="confirmed",
                price=1000,
                source="telegram",
            )
            db.add(booking)
            await db.commit()
            booking_id = booking.id

        instructor_bot = RecordingInstructorBot()
        with patch("app.bot.instructor_handlers.instructor_bot", instructor_bot), \
             patch("app.bot.instructor_handlers.now_kz", return_value=starts_at + timedelta(minutes=19)):
            await run_automatic_lesson_transitions()
        self.assertEqual(instructor_bot.messages, [])

        with patch("app.bot.instructor_handlers.instructor_bot", instructor_bot), \
             patch("app.bot.instructor_handlers.now_kz", return_value=starts_at + timedelta(minutes=20)):
            await run_automatic_lesson_transitions()
        self.assertEqual(len(instructor_bot.messages), 1)
        self.assertIn("через 20 минут", instructor_bot.messages[0][1])
        self.assertIsNotNone(instructor_bot.messages[0][2].get("reply_markup"))

        with patch("app.bot.instructor_handlers.instructor_bot", instructor_bot), \
             patch("app.bot.instructor_handlers.now_kz", return_value=starts_at + timedelta(minutes=21)):
            await run_automatic_lesson_transitions()
        self.assertEqual(len(instructor_bot.messages), 1)

        stale_done = RecordingInstructorCallback("inst_done", booking_id, "900002")
        with patch("app.bot.instructor_handlers.now_kz", return_value=starts_at + timedelta(hours=1, minutes=9)):
            await lesson_done(stale_done)
        self.assertIn("Сначала отметьте", stale_done.answers[0][0])

        async with async_session() as db:
            booking = await db.get(Booking, booking_id)
            self.assertEqual(booking.status, "confirmed")
            self.assertEqual(booking.payment_status, "unpaid")
            prompt_logs = (await db.execute(
                select(AuditLog).where(
                    AuditLog.action == "system_arrival_check_sent",
                    AuditLog.details.contains(f"запись #{booking_id}"),
                )
            )).scalars().all()
            self.assertEqual(len(prompt_logs), 1)

    async def test_confirmed_no_show_stops_completion_flow_and_marks_admin_status(self):
        starts_at = datetime(2035, 5, 20, 11, 0)
        async with async_session() as db:
            instructor = Instructor(
                name="Инструктор отметки неявки",
                telegram_id="900003",
                transmission="both",
                gender="any",
            )
            client = Client(name="Клиент неявки", phone="+77000000903")
            db.add_all([instructor, client])
            await db.flush()
            booking = Booking(
                client_id=client.id,
                instructor_id=instructor.id,
                service_type="training",
                transmission="manual",
                location="Тестовая площадка",
                booking_date=starts_at.date(),
                start_time=starts_at.time(),
                end_time=time(12, 0),
                status="confirmed",
                price=1000,
                source="telegram",
            )
            db.add(booking)
            await db.commit()
            booking_id = booking.id

        first_click = RecordingInstructorCallback("inst_no_show", booking_id, "900003")
        with patch("app.bot.instructor_handlers.now_kz", return_value=starts_at):
            await client_no_show(first_click)
        self.assertIn("Вы уверены, что клиент не пришёл?", first_click.message.text)

        confirmation = RecordingInstructorCallback("inst_no_show_yes", booking_id, "900003")
        confirmation.message = first_click.message
        with patch("app.bot.instructor_handlers.now_kz", return_value=starts_at + timedelta(minutes=20)):
            await client_no_show_confirmed(confirmation)

        async with async_session() as db:
            booking = await db.get(Booking, booking_id)
            self.assertEqual(booking.status, "no_show")
            self.assertEqual(booking.payment_status, "unpaid")
            self.assertIsNone(booking.completed_at)

        with patch("app.bot.instructor_handlers.now_kz", return_value=starts_at + timedelta(hours=1, minutes=9)):
            await run_automatic_lesson_transitions()
        stale_done = RecordingInstructorCallback("inst_done", booking_id, "900003")
        with patch("app.bot.instructor_handlers.now_kz", return_value=starts_at + timedelta(hours=1, minutes=9)):
            await lesson_done(stale_done)
        self.assertIn("не явившийся", stale_done.answers[0][0])
        async with async_session() as db:
            booking = await db.get(Booking, booking_id)
            self.assertEqual(booking.status, "no_show")
            self.assertEqual(booking.payment_status, "unpaid")
        self.assertIn("не явившийся", confirmation.message.text)
        self.assertIsNone(confirmation.message.edits[-1][1].get("reply_markup"))

    async def test_telegram_reminders_include_cash_notice(self):
        fixed_now = datetime(2030, 1, 10, 12, 0, tzinfo=TIMEZONE)

        class FixedDateTime(datetime):
            @classmethod
            def now(cls, _tz=None):
                return fixed_now

        async with async_session() as db:
            instructor = Instructor(name="Инструктор напоминаний", transmission="both", gender="any")
            client_24h = Client(name="Алина", phone="+77000000092", telegram_id="1001")
            client_1h = Client(name="Ерлан", phone="+77000000093", telegram_id="1002")
            db.add_all([instructor, client_24h, client_1h])
            await db.flush()
            db.add_all([
                Booking(
                    client_id=client_24h.id, instructor_id=instructor.id,
                    service_type="training", transmission="automatic", location="Циолковского 30",
                    booking_date=date(2030, 1, 11), start_time=time(12, 1), end_time=time(13, 1),
                    status="confirmed", price=10000, source="telegram",
                ),
                Booking(
                    client_id=client_1h.id, instructor_id=instructor.id,
                    service_type="training", transmission="automatic", location="Циолковского 30",
                    booking_date=date(2030, 1, 10), start_time=time(13, 1), end_time=time(14, 1),
                    status="confirmed", price=10000, source="telegram",
                ),
            ])
            await db.commit()

        bot = RecordingBot()
        with patch("app.bot.handlers.datetime", FixedDateTime):
            await send_lesson_reminders(bot)

        self.assertEqual(bot.messages, [
            (1001, "🔔 Алина, напоминаем!\n\n"
                   "Завтра у вас занятие:\n"
                   "📅 11.01.2030\n"
                   "🕐 12:01\n"
                   "📍 Циолковского 30\n\n"
                   "💵 Оплатить занятие можно наличными или через Kaspi QR.\n\n"
                   "Ждём вас! 🚗"),
            (1002, "⏰ Ерлан, через час ваше занятие!\n\n"
                   "🕐 13:01\n"
                   "📍 Циолковского 30\n\n"
                   "💵 Оплатить занятие можно наличными или через Kaspi QR.\n\n"
                   "Пора собираться! 🚗"),
        ])

    async def test_existing_booking_can_reschedule_after_profile_criteria_change(self):
        tokens = await self._register()
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}
        original_date = now_kz().date() + timedelta(days=1)
        new_date = now_kz().date() + timedelta(days=2)

        async with async_session() as db:
            client = (await db.execute(
                select(Client).where(Client.phone == "+77000000001")
            )).scalar_one()
            client.telegram_id = "history-test-client"
            instructor = Instructor(
                name="Изменённая специализация", transmission="manual",
                lesson_type="exam", gender="any", is_active=True,
                working_hours_start=time(9, 0), working_hours_end=time(20, 0),
                days_off="",
            )
            db.add(instructor)
            await db.flush()
            booking = Booking(
                client_id=client.id, instructor_id=instructor.id,
                service_type="training", transmission="automatic",
                location="Циолковского 30", booking_date=original_date,
                start_time=time(10, 0), end_time=time(11, 0),
                status="confirmed", price=10000, source="mobile",
                admin_confirmed=True,
            )
            db.add(booking)
            await db.commit()
            booking_id, instructor_id = booking.id, instructor.id

        legacy_app_slots = await self.client.get("/api/mobile/slots", params={
            "booking_date": new_date.isoformat(),
            "service_type": "training",
            "transmission": "automatic",
            "instructor_id": instructor_id,
        })
        self.assertEqual(legacy_app_slots.status_code, 200, legacy_app_slots.text)
        self.assertIn("11:00", legacy_app_slots.json()["slots"])

        response = await self.client.put(
            f"/api/mobile/bookings/{booking_id}/reschedule",
            headers=headers,
            json={"new_date": new_date.isoformat(), "new_start_time": "11:00"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "reschedule_pending")

        rejected_new_booking = await self.client.post(
            "/api/mobile/bookings",
            headers=headers,
            json={
                "instructor_id": instructor_id,
                "booking_date": new_date.isoformat(),
                "start_time": "14:00",
                "service_type": "training",
                "transmission": "automatic",
            },
        )
        self.assertEqual(rejected_new_booking.status_code, 400, rejected_new_booking.text)

        async with async_session() as db:
            booking = await db.get(Booking, booking_id)
            self.assertEqual(booking.status, "reschedule_pending")
            self.assertEqual(booking.instructor_id, instructor_id)
            self.assertEqual(booking.service_type, "training")
            self.assertEqual(booking.booking_date, original_date)
            self.assertEqual(booking.requested_reschedule_date, new_date)

    async def test_mobile_history_is_loaded_in_pages_of_seven(self):
        tokens = await self._register()
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}
        async with async_session() as db:
            client = (await db.execute(
                select(Client).where(Client.phone == "+77000000001")
            )).scalar_one()
            instructor = Instructor(name="Инструктор истории", transmission="automatic", gender="any")
            db.add(instructor)
            await db.flush()
            bookings = []
            for index in range(15):
                bookings.append(Booking(
                    client_id=client.id,
                    instructor_id=instructor.id,
                    service_type="training",
                    transmission="automatic",
                    location="Циолковского 30",
                    booking_date=now_kz().date() - timedelta(days=index + 1),
                    start_time=time(10, 0),
                    end_time=time(11, 0),
                    status="completed",
                    price=10000,
                ))
            db.add_all(bookings)
            await db.commit()
            expected_ids = [booking.id for booking in bookings]

        first = await self.client.get("/api/mobile/bookings/history?page=1", headers=headers)
        second = await self.client.get("/api/mobile/bookings/history?page=2", headers=headers)
        third = await self.client.get("/api/mobile/bookings/history?page=3", headers=headers)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(third.status_code, 200, third.text)

        first_data, second_data, third_data = first.json(), second.json(), third.json()
        self.assertEqual(len(first_data["items"]), 7)
        self.assertEqual(len(second_data["items"]), 7)
        self.assertEqual(len(third_data["items"]), 1)
        self.assertTrue(first_data["has_more"])
        self.assertTrue(second_data["has_more"])
        self.assertFalse(third_data["has_more"])
        returned_ids = [
            item["id"]
            for payload in (first_data, second_data, third_data)
            for item in payload["items"]
        ]
        self.assertEqual(returned_ids, expected_ids)
        self.assertEqual(len(returned_ids), len(set(returned_ids)))

        # Бот использует тот же размер страницы: семь карточек и только одну
        # кнопку для следующей страницы, а не всю историю за один запрос.
        async with async_session() as db:
            bot_bookings, bot_has_more = await _learning_history_page(db, client.id, page=1)
        self.assertEqual(len(bot_bookings), 7)
        self.assertTrue(bot_has_more)
        message = RecordingHistoryMessage()
        await _send_learning_history_page(message, bot_bookings, page=1, has_more=bot_has_more)
        self.assertEqual(len(message.answers), 8)
        button = message.answers[-1][1]["reply_markup"].inline_keyboard[0][0]
        self.assertEqual(button.callback_data, "learning_history:2")

    async def test_client_bot_booking_card_states_cash_or_kaspi_qr(self):
        telegram_id = "cash-only-card-client"
        async with async_session() as db:
            instructor = Instructor(name="Инструктор наличных", transmission="both", gender="any")
            client = Client(name="Клиент наличных", phone="+77000000031", telegram_id=telegram_id)
            db.add_all([instructor, client])
            await db.flush()
            db.add(Booking(
                client_id=client.id, instructor_id=instructor.id,
                service_type="training", transmission="manual", location="Циолковского 30",
                booking_date=now_kz().date() + timedelta(days=1),
                start_time=time(10, 0), end_time=time(11, 0),
                status="confirmed", price=10000, booking_number="900157",
            ))
            await db.commit()

        message = RecordingClientMessage(telegram_id)
        await my_bookings(message)
        self.assertEqual(len(message.answers), 1)
        self.assertIn("💰 10000 ₸ (оплата наличными или через Kaspi QR)", message.answers[0][0])

    async def test_client_bot_booking_card_shows_package_progress(self):
        telegram_id = "package-progress-card-client"
        async with async_session() as db:
            instructor = Instructor(name="Инструктор пакета", transmission="both", gender="any")
            client = Client(name="Клиент пакета", phone="+77000000032", telegram_id=telegram_id)
            package = Package(name="Пакет 6 занятий", sessions_count=6, price=55000)
            db.add_all([instructor, client, package])
            await db.flush()
            db.add(ClientPackage(
                client_id=client.id, package_id=package.id, remaining_sessions=4,
                expires_at=now_kz() + timedelta(days=30),
            ))
            db.add(Booking(
                client_id=client.id, instructor_id=instructor.id, package_id=package.id,
                service_type="training", transmission="manual", location="Циолковского 30",
                booking_date=now_kz().date() + timedelta(days=1),
                start_time=time(11, 0), end_time=time(12, 0),
                status="confirmed", price=0, booking_number="900158",
            ))
            await db.commit()

        message = RecordingClientMessage(telegram_id)
        await my_bookings(message)

        self.assertEqual(len(message.answers), 1)
        text = message.answers[0][0]
        self.assertIn("💰 0 ₸", text)
        self.assertNotIn("оплата наличными или через Kaspi QR", text)
        self.assertIn("📦 Пакет: использовано 2/6", text)
        self.assertIn("📦 Осталось занятий по пакету: 4 из 6", text)

if __name__ == "__main__":
    unittest.main()
