import os
import tempfile
import unittest
from datetime import date, datetime, time, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import jwt
from sqlalchemy import func, select


_db_file = Path(tempfile.gettempdir()) / "nomad_admin_offline_tests.sqlite3"
_db_file.unlink(missing_ok=True)
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_db_file.as_posix()}"
os.environ["SECRET_KEY"] = "test-secret-key"

from app.database import async_session, init_db
from app.main import app
from app.models.models import (
    Admin, AdminState, ArchivedLog, AuditLog, Booking, Certificate, CertificateRequest, Client, ClientBlock, ClientPackage,
    Event, FAQItem, GenderAnalytics, Instructor, InstructorDailySchedule, MobileAppReview,
    MobileBooking, MobileSession, MobileUser, MobileUserPackage, Package, ReferralRecord,
    NotificationSent, RatingRecord, SupportMessage, Vehicle, WaitingListEntry,
    now_kz,
)
from app.services.auth import hash_password
from app.config import settings
from app.services.gender_analytics import refresh_gender_analytics
from app.services.booking_service import has_available_vehicle
from app.routers.admin import _build_revenue_analytics


class FakeGroqResponse:
    status_code = 200
    text = ""

    def __init__(self, items):
        import json
        self._body = {"choices": [{"message": {"content": json.dumps({"items": items})}}]}

    def json(self):
        return self._body


class FakeGroqClient:
    last_url = None
    last_payload = None

    def __init__(self, *_args, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def post(self, _url, **kwargs):
        import json
        type(self).last_url = _url
        type(self).last_payload = kwargs["json"]
        rows = json.loads(kwargs["json"]["messages"][1]["content"])["items"]
        items = []
        for row in rows:
            name = row["name"].lower()
            gender = "female" if "мария" in name else "male" if "иван" in name else "unknown"
            items.append({"id": row["id"], "gender": gender})
        return FakeGroqResponse(items)


class RecordingTelegramClient:
    requests = []

    def __init__(self, *_args, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def post(self, url, **kwargs):
        type(self).requests.append({"url": url, "json": kwargs["json"]})
        return httpx.Response(200, json={"ok": True})


class OfflineIdempotencyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await init_db()
        async with async_session() as db:
            if not await db.scalar(select(Admin).where(Admin.username == "admin")):
                db.add(Admin(username="admin", password_hash=hash_password("admin123")))
                await db.commit()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://testserver"
        )
        login = await self.client.post(
            "/api/admin/login", json={"username": "admin", "password": "admin123"}
        )
        self.assertEqual(login.status_code, 200, login.text)

    async def asyncTearDown(self):
        await self.client.aclose()

    async def _repeat(self, path, body, key):
        headers = {"X-Idempotency-Key": key}
        first = await self.client.post(f"/api/admin{path}", json=body, headers=headers)
        second = await self.client.post(f"/api/admin{path}", json=body, headers=headers)
        self.assertTrue(200 <= first.status_code < 300, first.text)
        self.assertTrue(200 <= second.status_code < 300, second.text)
        return first.json(), second.json()

    async def test_audit_contains_only_real_admins_and_events_only_client_actions(self):
        async with async_session() as db:
            client = Client(name="Клиент журнала", phone="+77000009991")
            db.add(client)
            await db.flush()
            db.add_all([
                AuditLog(
                    admin_username="admin",
                    action="update_client",
                    details="Администратор отредактировал карточку клиента «Клиент журнала».",
                ),
                AuditLog(
                    admin_username="mobile",
                    action="new_booking",
                    details="CLIENT_ACTION_MUST_NOT_BE_IN_AUDIT",
                ),
                Event(
                    event_type="client_profile_updated",
                    source="mobile",
                    client_id=client.id,
                    message="VISIBLE_CLIENT_EVENT",
                ),
                Event(
                    event_type="reminder_sent",
                    source="telegram",
                    message="SYSTEM_EVENT_MUST_NOT_BE_VISIBLE",
                ),
                Event(
                    event_type="booking_confirmed",
                    source="admin",
                    client_id=client.id,
                    message="ADMIN_EVENT_MUST_NOT_BE_VISIBLE",
                ),
            ])
            await db.commit()

        audit_response = await self.client.get("/api/admin/audit-logs")
        self.assertEqual(audit_response.status_code, 200, audit_response.text)
        audit = audit_response.json()
        self.assertTrue(all(item["admin_username"] == "admin" for item in audit))
        self.assertTrue(any(item["action"] == "Отредактировал карточку клиента" for item in audit))
        self.assertFalse(any("CLIENT_ACTION_MUST_NOT_BE_IN_AUDIT" in (item["details"] or "") for item in audit))

        events_response = await self.client.get("/api/admin/notifications")
        self.assertEqual(events_response.status_code, 200, events_response.text)
        messages = {item["message"] for item in events_response.json()}
        self.assertIn("VISIBLE_CLIENT_EVENT", messages)
        self.assertNotIn("SYSTEM_EVENT_MUST_NOT_BE_VISIBLE", messages)
        self.assertNotIn("ADMIN_EVENT_MUST_NOT_BE_VISIBLE", messages)

    async def test_daily_logs_are_unlimited_and_old_rows_are_archived_and_exported(self):
        now = now_kz()
        async with async_session() as db:
            client = Client(name="Клиент архива логов", phone="+77000009992")
            db.add(client)
            await db.flush()
            db.add(AuditLog(
                admin_username="admin", action="update_client", details="OLD_AUDIT_LOG",
                created_at=now - timedelta(days=1),
            ))
            db.add(Event(
                event_type="booking_cancelled", source="mobile", client_id=client.id,
                message="OLD_CLIENT_EVENT", created_at=now - timedelta(days=1),
            ))
            db.add_all([
                AuditLog(
                    admin_username="admin", action="update_client",
                    details=f"TODAY_AUDIT_{index}", created_at=now,
                ) for index in range(205)
            ])
            db.add_all([
                Event(
                    event_type="client_profile_updated", source="mobile", client_id=client.id,
                    message=f"TODAY_EVENT_{index}", created_at=now,
                ) for index in range(105)
            ])
            await db.commit()

        audit_response = await self.client.get("/api/admin/audit-logs")
        self.assertEqual(audit_response.status_code, 200, audit_response.text)
        audit_details = {item["details"] for item in audit_response.json()}
        self.assertTrue(all(f"TODAY_AUDIT_{index}" in audit_details for index in range(205)))
        self.assertNotIn("OLD_AUDIT_LOG", audit_details)

        events_response = await self.client.get("/api/admin/notifications")
        self.assertEqual(events_response.status_code, 200, events_response.text)
        event_messages = {item["message"] for item in events_response.json()}
        self.assertTrue(all(f"TODAY_EVENT_{index}" in event_messages for index in range(105)))
        self.assertNotIn("OLD_CLIENT_EVENT", event_messages)

        async with async_session() as db:
            archived = (await db.execute(select(ArchivedLog).where(
                ArchivedLog.details == "OLD_AUDIT_LOG"
            ))).scalar_one()
            self.assertEqual(archived.source_type, "audit")
        self.assertTrue(await db.scalar(select(func.count()).select_from(ArchivedLog).where(
                ArchivedLog.message == "OLD_CLIENT_EVENT"
            )))

        archived_audit = await self.client.get("/api/admin/logs/archive/audit")
        self.assertEqual(archived_audit.status_code, 200, archived_audit.text)
        self.assertTrue(any(item["details"] == "OLD_AUDIT_LOG" for item in archived_audit.json()))
        self.assertTrue(all(item["event_type"] is None for item in archived_audit.json()))

        archived_events = await self.client.get("/api/admin/logs/archive/events")
        self.assertEqual(archived_events.status_code, 200, archived_events.text)
        self.assertTrue(any(item["message"] == "OLD_CLIENT_EVENT" for item in archived_events.json()))
        self.assertTrue(all(item["admin_username"] is None for item in archived_events.json()))

        exported = await self.client.get("/api/admin/logs/archive/export")
        self.assertEqual(exported.status_code, 200, exported.text)
        self.assertIn("attachment; filename=nomad_logs_", exported.headers["content-disposition"])
        csv_text = exported.content.decode("utf-8-sig")
        self.assertIn("OLD_AUDIT_LOG", csv_text)
        self.assertIn("OLD_CLIENT_EVENT", csv_text)

    async def test_00_full_backup_round_trip_preserves_fleet_and_new_booking_data(self):
        booking_date = now_kz().date() + timedelta(days=3)
        async with async_session() as db:
            vehicle = (await db.execute(select(Vehicle).where(Vehicle.name == "Машина 1"))).scalar_one()
            vehicle.is_under_repair = True
            instructor = Instructor(
                name="Инструктор резервной копии", phone="+77000000901", transmission="both",
                lesson_type="exam", is_duty=True, is_lead=True, avatar_url="instructor.png",
                offline_operation_id="backup-instructor-op", working_hours_start=time(9),
                working_hours_end=time(20), days_off="",
            )
            client = Client(
                name="Клиент резервной копии", phone="+77000000902", password_hash="hash",
                offline_operation_id="backup-client-op", reschedule_count_24h=1,
                reschedule_window_started_at=now_kz(),
                support_chat_opened_at=now_kz(),
            )
            package = Package(
                name="Пакет резервной копии", sessions_count=4, price=40000,
                code="BACKUP-PACK", offline_operation_id="backup-package-op",
            )
            mobile_user = MobileUser(
                name="Мобильный клиент", phone="+77000000903", password_hash="hash",
                referral_code="MOBILE-BACKUP",
            )
            db.add_all([instructor, client, package, mobile_user])
            await db.flush()
            booking = Booking(
                client_id=client.id, instructor_id=instructor.id, vehicle_id=vehicle.id,
                service_type="training", transmission="manual", location="Циолковского 30",
                booking_date=booking_date, start_time=time(10), end_time=time(11),
                status="reschedule_pending", price=10000, package_id=package.id,
                offline_operation_id="backup-booking-op", reschedule_previous_status="confirmed",
                requested_reschedule_date=booking_date + timedelta(days=1),
                requested_reschedule_start_time=time(12), requested_reschedule_end_time=time(13),
                reschedule_requested_at=now_kz(), completed_at=None,
            )
            db.add(booking)
            await db.flush()
            db.add_all([
                WaitingListEntry(name="Ожидающий", phone="+77000000904", transmission="manual",
                    instructor_id=instructor.id, offline_operation_id="backup-waiting-op"),
                ClientBlock(client_id=client.id, blocked_until=now_kz() + timedelta(days=1), reason="Тест"),
                SupportMessage(client_id=client.id, sender="user", text="Сообщение", is_admin_read=True,
                    offline_operation_id="backup-support-op"),
                MobileSession(id="backup-session", client_id=client.id, expires_at=now_kz() + timedelta(days=1)),
                MobileUserPackage(user_id=mobile_user.id, package_id=package.id, remaining_sessions=4),
                MobileAppReview(user_id=mobile_user.id, client_id=client.id, stars=5),
                GenderAnalytics(id=1, male_count=1, female_count=2, unknown_count=3, total_count=6, model="test"),
                ArchivedLog(
                    source_type="audit", source_log_id=987654, admin_username="admin",
                    action="update_client", details="ARCHIVED_BACKUP_LOG",
                    created_at=now_kz() - timedelta(days=1), archived_at=now_kz(),
                ),
            ])
            admin_state = await db.get(AdminState, 1)
            if admin_state:
                admin_state.notifications_viewed_id = 7
                admin_state.clients_viewed_id = 8
            else:
                db.add(AdminState(id=1, notifications_viewed_id=7, clients_viewed_id=8))
            await db.commit()

        exported = await self.client.get("/api/admin/export/full-backup?format=json")
        self.assertEqual(exported.status_code, 200, exported.text)
        backup = exported.json()
        self.assertEqual(backup["format_version"], 3)
        self.assertTrue(any(item["details"] == "ARCHIVED_BACKUP_LOG" for item in backup["archived_logs"]))
        self.assertEqual(len(backup["vehicles"]), 6)
        exported_vehicle = next(item for item in backup["vehicles"] if item["id"] == vehicle.id)
        self.assertTrue(exported_vehicle["is_under_repair"])
        exported_booking = next(item for item in backup["bookings"] if item[
            "offline_operation_id"
        ] == "backup-booking-op")
        self.assertEqual(exported_booking["vehicle_id"], vehicle.id)

        restored = await self.client.post("/api/admin/import/full-backup", json=backup)
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertTrue(restored.json()["ok"])
        self.assertEqual(restored.json()["stats"]["vehicles"], 6)
        async with async_session() as db:
            restored_vehicle = (await db.execute(select(Vehicle).where(
                Vehicle.name == "Машина 1"
            ))).scalar_one()
            self.assertTrue(restored_vehicle.is_under_repair)

        # Старые файлы не содержали автопарк: импорт должен восстановить
        # безопасный исходный набор машин, а не падать и не терять записи.
        legacy_backup = dict(backup)
        legacy_backup.pop("vehicles")
        legacy_restored = await self.client.post("/api/admin/import/full-backup", json=legacy_backup)
        self.assertEqual(legacy_restored.status_code, 200, legacy_restored.text)
        self.assertEqual(legacy_restored.json()["stats"]["vehicles"], 6)

        async with async_session() as db:
            restored_booking = (await db.execute(select(Booking).where(
                Booking.offline_operation_id == "backup-booking-op"
            ))).scalar_one()
            restored_vehicle = await db.get(Vehicle, restored_booking.vehicle_id)
            self.assertEqual(restored_vehicle.name, "Машина 1")
            self.assertFalse(restored_vehicle.is_under_repair)
            self.assertEqual(restored_booking.reschedule_previous_status, "confirmed")
            self.assertEqual(restored_booking.requested_reschedule_date, booking_date + timedelta(days=1))
            self.assertEqual(restored_booking.requested_reschedule_start_time, time(12))
            self.assertEqual(restored_booking.status, "reschedule_pending")
            self.assertEqual((await db.execute(select(Instructor).where(
                Instructor.offline_operation_id == "backup-instructor-op"
            ))).scalar_one().lesson_type, "exam")
            self.assertTrue((await db.execute(select(SupportMessage).where(
                SupportMessage.offline_operation_id == "backup-support-op"
            ))).scalar_one().is_admin_read)
            self.assertEqual(await db.scalar(select(func.count()).select_from(MobileUserPackage)), 1)
            self.assertEqual(await db.scalar(select(func.count()).select_from(MobileAppReview)), 1)
            self.assertIsNotNone(await db.get(MobileSession, "backup-session"))
            self.assertTrue(await db.scalar(select(func.count()).select_from(ArchivedLog).where(
                ArchivedLog.details == "ARCHIVED_BACKUP_LOG"
            )))
            self.assertEqual((await db.get(AdminState, 1)).notifications_viewed_id, 7)
            restored_client = (await db.execute(select(Client).where(
                Client.offline_operation_id == "backup-client-op"
            ))).scalar_one()
            self.assertIsNotNone(restored_client.support_chat_opened_at)
            self.assertIsNone(restored_client.support_chat_closed_at)

    async def test_every_offline_create_is_replay_safe(self):
        await self._repeat("/faq", {"question": "Q", "answer": "A", "sort_order": 0}, "faq-op")
        await self._repeat("/certificates", {"nominal": 5000}, "certificate-op")
        await self._repeat("/packages", {
            "name": "Пакет 6 занятий", "sessions_count": 6, "price": 55000,
            "description": "Тест", "validity_days": 30, "bonus_exam": True,
        }, "package-op")
        await self._repeat("/waiting-list", {
            "name": "Ожидающий", "phone": "+77000000002", "desired_date": None,
            "desired_time_start": None, "desired_time_end": None, "transmission": "automatic",
            "instructor_id": None, "instructor_gender": None, "notes": "Тест",
        }, "waiting-op")

        async with async_session() as db:
            for model, operation_id in (
                (FAQItem, "faq-op"),
                (Certificate, "certificate-op"),
                (Package, "package-op"),
                (WaitingListEntry, "waiting-op"),
            ):
                count = await db.scalar(select(func.count()).select_from(model).where(
                    model.offline_operation_id == operation_id
                ))
                self.assertEqual(count, 1)

    async def test_package_offers_accept_six_and_ten_sessions_and_reject_unknown(self):
        offers = (
            ("package-offer-6", "Пакет 6 занятий", 6, 55000),
            ("package-offer-10", "Пакет 10 занятий", 10, 90000),
        )
        for operation_id, name, sessions_count, price in offers:
            response = await self.client.post(
                "/api/admin/packages",
                headers={"X-Idempotency-Key": operation_id},
                json={
                    "name": name,
                    "sessions_count": sessions_count,
                    "price": price,
                    "description": f"{sessions_count} занятий на площадке",
                    "validity_days": 30,
                    "bonus_exam": True,
                },
            )
            self.assertEqual(response.status_code, 200, response.text)

        rejected = await self.client.post(
            "/api/admin/packages",
            json={
                "name": "Произвольный пакет",
                "sessions_count": 10,
                "price": 89999,
                "validity_days": 30,
                "bonus_exam": True,
            },
        )
        self.assertEqual(rejected.status_code, 400, rejected.text)
        self.assertIn("10 занятий за 90 000 ₸", rejected.json()["detail"])

        async with async_session() as db:
            saved = (await db.execute(
                select(Package).where(Package.offline_operation_id.in_(
                    [operation_id for operation_id, *_ in offers]
                )).order_by(Package.sessions_count)
            )).scalars().all()
        self.assertEqual(
            [(item.sessions_count, item.price, item.validity_days, item.bonus_exam) for item in saved],
            [(6, 55000, 30, True), (10, 90000, 30, True)],
        )

    async def test_mobile_token_cannot_open_admin_support(self):
        anonymous = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://testserver"
        )
        try:
            token = jwt.encode({"sub": "1", "type": "access"}, "test-secret-key", algorithm="HS256")
            response = await anonymous.get(
                "/api/admin/support/dialogs", headers={"Authorization": f"Bearer {token}"}
            )
            self.assertEqual(response.status_code, 401)
        finally:
            await anonymous.aclose()

    async def test_check_session_is_not_cacheable_and_requires_cookie(self):
        active = await self.client.get("/api/admin/check-session")
        self.assertEqual(active.status_code, 200, active.text)
        self.assertEqual(active.headers.get("cache-control"), "no-store, max-age=0")
        self.client.cookies.clear()
        logged_out = await self.client.get("/api/admin/check-session")
        self.assertEqual(logged_out.status_code, 401, logged_out.text)
        self.assertEqual(logged_out.headers.get("cache-control"), "no-store, max-age=0")

    async def test_offline_support_reply_is_delivered_once(self):
        async with async_session() as db:
            client = Client(name="Получатель", phone="+77000000009", referral_code="SUPPORT-TEST")
            db.add(client)
            await db.commit()
            await db.refresh(client)
            client_id = client.id
        await self._repeat(
            f"/support/dialogs/{client_id}/reply", {"text": "Только текст"}, "support-reply-op"
        )
        async with async_session() as db:
            count = await db.scalar(select(func.count()).select_from(SupportMessage).where(
                SupportMessage.client_id == client_id,
                SupportMessage.sender == "admin",
            ))
            self.assertEqual(count, 1)

    async def test_admin_reply_opens_and_closes_telegram_support_chat(self):
        async with async_session() as db:
            client = Client(
                name="Telegram-чат", phone="+77000000881", telegram_id="99880011",
            )
            db.add(client)
            await db.commit()
            client_id = client.id

        RecordingTelegramClient.requests.clear()
        with (
            patch("app.routers.support.httpx.AsyncClient", RecordingTelegramClient),
            patch("app.routers.support.send_push_to_user", new_callable=AsyncMock),
            patch.object(settings, "BOT_TOKEN", "test-token"),
        ):
            reply = await self.client.post(
                f"/api/admin/support/dialogs/{client_id}/reply", json={"text": "Здравствуйте"},
            )
            self.assertEqual(reply.status_code, 201, reply.text)
            close = await self.client.post(f"/api/admin/support/dialogs/{client_id}/close")
            self.assertEqual(close.status_code, 200, close.text)

        async with async_session() as db:
            client = await db.get(Client, client_id)
            self.assertIsNotNone(client.support_chat_opened_at)
            self.assertIsNotNone(client.support_chat_closed_at)
            message = (await db.execute(select(SupportMessage).where(
                SupportMessage.client_id == client_id,
                SupportMessage.sender == "admin",
            ))).scalar_one()
        self.assertEqual(message.channel, "telegram")
        self.assertEqual(len(RecordingTelegramClient.requests), 2)
        self.assertEqual(
            RecordingTelegramClient.requests[0]["json"]["reply_markup"]["keyboard"][0][0]["text"],
            "❌ Завершить чат",
        )
        self.assertEqual(
            RecordingTelegramClient.requests[1]["json"]["reply_markup"]["keyboard"][3][1]["text"],
            "💬 Поддержка",
        )
        dialog = await self.client.get(f"/api/admin/support/dialogs/{client_id}")
        self.assertFalse(dialog.json()["user"]["support_chat_is_open"])

    async def test_support_badge_is_cleared_only_for_opened_dialog(self):
        before_response = await self.client.get("/api/admin/notification-counts")
        self.assertEqual(before_response.status_code, 200, before_response.text)
        self.assertEqual(before_response.headers.get("cache-control"), "no-store, max-age=0")
        unread_before = before_response.json()["unread_support"]

        async with async_session() as db:
            first_client = Client(
                name="Первый непрочитанный", phone="+77000000121", referral_code="SUPPORT-BADGE-1"
            )
            second_client = Client(
                name="Второй непрочитанный", phone="+77000000122", referral_code="SUPPORT-BADGE-2"
            )
            instructor = Instructor(
                name="Инструктор непрочитанный", phone="+77000000123",
                transmission="automatic", gender="any", is_active=True,
            )
            db.add_all([first_client, second_client, instructor])
            await db.flush()
            first_client_id = first_client.id
            second_client_id = second_client.id
            instructor_id = instructor.id
            db.add_all([
                SupportMessage(
                    client_id=first_client_id, channel="client", sender="user",
                    text="Первое обращение", is_read=False, is_admin_read=False,
                ),
                SupportMessage(
                    client_id=second_client_id, channel="client", sender="user",
                    text="Второе обращение", is_read=False, is_admin_read=False,
                ),
                SupportMessage(
                    instructor_id=instructor_id, channel="instructor", sender="instructor",
                    text="Обращение инструктора", is_read=False, is_admin_read=False,
                ),
                # У старых строк мог остаться только legacy user_id либо вообще
                # не остаться владельца. Такого диалога нет в UI, поэтому он не
                # должен создавать вечный общий бейдж.
                SupportMessage(
                    channel="client", sender="user", text="Сиротское обращение клиента",
                    is_read=False, is_admin_read=False,
                ),
                SupportMessage(
                    channel="instructor", sender="instructor", text="Сиротское обращение инструктора",
                    is_read=False, is_admin_read=False,
                ),
            ])
            await db.commit()

        after_create = await self.client.get("/api/admin/notification-counts")
        self.assertEqual(after_create.status_code, 200, after_create.text)
        self.assertEqual(after_create.json()["unread_support"], unread_before + 3)

        dialogs_before = await self.client.get("/api/admin/support/dialogs")
        self.assertEqual(dialogs_before.status_code, 200, dialogs_before.text)
        unread_by_client = {item["user_id"]: item["unread_from_user"] for item in dialogs_before.json()}
        self.assertEqual(unread_by_client[first_client_id], 1)
        self.assertEqual(unread_by_client[second_client_id], 1)

        # Старый маршрут больше не может снять непрочитанность всей поддержки.
        legacy_viewed = await self.client.post("/api/admin/support/mark-viewed")
        self.assertEqual(legacy_viewed.status_code, 200, legacy_viewed.text)
        self.assertTrue(legacy_viewed.json()["deprecated"])
        after_legacy = await self.client.get("/api/admin/notification-counts")
        self.assertEqual(after_legacy.json()["unread_support"], unread_before + 3)

        opened_client = await self.client.get(f"/api/admin/support/dialogs/{first_client_id}")
        self.assertEqual(opened_client.status_code, 200, opened_client.text)
        after_client_open = await self.client.get("/api/admin/notification-counts")
        self.assertEqual(after_client_open.json()["unread_support"], unread_before + 2)

        dialogs_after_client_open = await self.client.get("/api/admin/support/dialogs")
        unread_by_client = {
            item["user_id"]: item["unread_from_user"] for item in dialogs_after_client_open.json()
        }
        self.assertEqual(unread_by_client[first_client_id], 0)
        self.assertEqual(unread_by_client[second_client_id], 1)

        opened_instructor = await self.client.get(
            f"/api/admin/support/instructors/dialogs/{instructor_id}"
        )
        self.assertEqual(opened_instructor.status_code, 200, opened_instructor.text)
        after_instructor_open = await self.client.get("/api/admin/notification-counts")
        self.assertEqual(after_instructor_open.json()["unread_support"], unread_before + 1)

        opened_second_client = await self.client.get(f"/api/admin/support/dialogs/{second_client_id}")
        self.assertEqual(opened_second_client.status_code, 200, opened_second_client.text)
        after_every_dialog_open = await self.client.get("/api/admin/notification-counts")
        self.assertEqual(after_every_dialog_open.json()["unread_support"], unread_before)

    async def test_clients_badge_stays_zero_after_viewing(self):
        async with async_session() as db:
            db.add(Client(
                name="Новый клиент для бейджа", phone="+77000000124",
                referral_code="CLIENT-BADGE-WATERMARK",
            ))
            await db.commit()

        before_view = await self.client.get("/api/admin/notification-counts")
        self.assertGreaterEqual(before_view.json()["new_clients"], 1)

        viewed = await self.client.post("/api/admin/clients/mark-viewed")
        self.assertEqual(viewed.status_code, 200, viewed.text)
        first_poll = await self.client.get("/api/admin/notification-counts")
        second_poll = await self.client.get("/api/admin/notification-counts")
        self.assertEqual(first_poll.json()["new_clients"], 0)
        self.assertEqual(second_poll.json()["new_clients"], 0)
        self.assertEqual(second_poll.headers.get("cache-control"), "no-store, max-age=0")

    async def test_pending_booking_badge_survives_opening_bookings_page(self):
        booking_date = now_kz().date() + timedelta(days=2)
        async with async_session() as db:
            instructor = Instructor(
                name="Инструктор бейджа заявок", transmission="automatic", gender="any",
                is_active=True, working_hours_start=time(9, 0), working_hours_end=time(20, 0), days_off="",
            )
            client = Client(name="Клиент бейджа заявок", phone="+77000000888")
            db.add_all([instructor, client])
            await db.flush()
            db.add(Booking(
                client_id=client.id, instructor_id=instructor.id,
                service_type="training", transmission="automatic", location="Циолковского 30",
                booking_date=booking_date, start_time=time(10, 0), end_time=time(11, 0),
                status="pending", admin_viewed=False, price=10000, source="mobile",
            ))
            await db.commit()

        before_view = await self.client.get("/api/admin/notification-counts")
        pending_before = before_view.json()["pending_applications_count"]
        self.assertGreaterEqual(pending_before, 1)
        viewed = await self.client.post("/api/admin/bookings/mark-viewed")
        self.assertEqual(viewed.status_code, 200, viewed.text)
        after_view = await self.client.get("/api/admin/notification-counts")
        self.assertEqual(after_view.json()["pending_applications_count"], pending_before)

    async def test_manual_booking_retry_does_not_create_a_copy(self):
        async with async_session() as db:
            instructor = Instructor(
                name="Инструктор", transmission="automatic", gender="any", is_active=True,
                working_hours_start=time(9, 0), working_hours_end=time(20, 0), days_off="",
            )
            db.add(instructor)
            await db.commit()
            await db.refresh(instructor)
            instructor_id = instructor.id
        booking_date = (now_kz().date() + timedelta(days=1)).isoformat()
        first, second = await self._repeat("/bookings/manual", {
            "client_name": "Ручной клиент", "client_phone": "+77000000008",
            "instructor_id": instructor_id, "service_type": "training",
            "transmission": "automatic", "booking_date": booking_date,
            "start_time": "10:00", "location": "Циолковского 30",
        }, "manual-booking-op")
        self.assertEqual(first["booking_id"], second["booking_id"])
        async with async_session() as db:
            count = await db.scalar(select(func.count()).select_from(Booking).where(
                Booking.offline_operation_id == "manual-booking-op"
            ))
            self.assertEqual(count, 1)

    async def test_manual_package_booking_notifies_client_of_used_lessons(self):
        booking_date = now_kz().date() + timedelta(days=1)
        async with async_session() as db:
            instructor = Instructor(
                name="Инструктор пакета", transmission="automatic", gender="any", is_active=True,
                working_hours_start=time(9, 0), working_hours_end=time(20, 0), days_off="",
            )
            client = Client(
                name="Клиент пакета", phone="+77000000085", telegram_id="package-progress-chat",
            )
            package = Package(name="Пакет 6 занятий", sessions_count=6, price=55000)
            db.add_all([instructor, client, package])
            await db.flush()
            db.add(ClientPackage(
                client_id=client.id, package_id=package.id, remaining_sessions=6,
                expires_at=now_kz() + timedelta(days=30),
            ))
            await db.commit()
            instructor_id = instructor.id
            package_id = package.id

        RecordingTelegramClient.requests = []
        with patch("app.routers.admin.httpx.AsyncClient", RecordingTelegramClient), patch.object(
            settings, "BOT_TOKEN", "test-bot-token"
        ):
            response = await self.client.post("/api/admin/bookings/manual", json={
                "client_name": "Клиент пакета", "client_phone": "+77000000085",
                "instructor_id": instructor_id, "service_type": "training",
                "transmission": "automatic", "booking_date": booking_date.isoformat(),
                "start_time": "10:00", "location": "Циолковского 30",
            })

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(RecordingTelegramClient.requests), 1)
        self.assertIn(
            "Запись оплачена пакетом — 1/6 занятий",
            RecordingTelegramClient.requests[0]["json"]["text"],
        )
        async with async_session() as db:
            purchase = (await db.execute(select(ClientPackage).where(
                ClientPackage.package_id == package_id,
            ))).scalar_one()
        self.assertEqual(purchase.remaining_sessions, 5)

    async def test_waiting_list_returns_cancelled_slot_matches_in_one_response(self):
        future_date = now_kz().date() + timedelta(days=1)
        async with async_session() as db:
            instructor = Instructor(name="Инструктор листа", transmission="automatic", gender="male")
            client = Client(
                name="Клиент листа", phone="+77000000985", telegram_id="waiting-list-client",
            )
            db.add_all([instructor, client])
            await db.flush()
            matching_waiter = WaitingListEntry(
                name="Подходит к слоту", phone=client.phone, desired_date=future_date,
                desired_time_start=time(10, 0), desired_time_end=time(12, 0),
                transmission="automatic", instructor_id=instructor.id, instructor_gender="male",
            )
            nonmatching_waiter = WaitingListEntry(
                name="Не подходит к слоту", phone="+77000000986", desired_date=future_date,
                transmission="manual",
            )
            future_cancelled = Booking(
                client_id=client.id, instructor_id=instructor.id, service_type="training",
                transmission="automatic", location="Циолковского 30", booking_date=future_date,
                start_time=time(11, 0), end_time=time(12, 0), status="cancelled", price=10000,
                source="mobile",
            )
            past_cancelled = Booking(
                client_id=client.id, instructor_id=instructor.id, service_type="training",
                transmission="automatic", location="Циолковского 30",
                booking_date=future_date - timedelta(days=2), start_time=time(11, 0), end_time=time(12, 0),
                status="cancelled", price=10000, source="mobile",
            )
            db.add_all([matching_waiter, nonmatching_waiter, future_cancelled, past_cancelled])
            await db.commit()
            matching_waiter_id = matching_waiter.id
            nonmatching_waiter_id = nonmatching_waiter.id
            future_cancelled_id = future_cancelled.id

        waiting = await self.client.get("/api/admin/waiting-list")
        self.assertEqual(waiting.status_code, 200, waiting.text)
        items_by_id = {item["id"]: item for item in waiting.json()["items"]}
        self.assertTrue(items_by_id[matching_waiter_id]["matches_cancelled_slot"])
        self.assertFalse(items_by_id[nonmatching_waiter_id]["matches_cancelled_slot"])
        self.assertEqual(items_by_id[matching_waiter_id]["client_source"], "telegram")

        matching = await self.client.get(f"/api/admin/waiting-list/matching/{future_cancelled_id}")
        self.assertEqual(matching.status_code, 200, matching.text)
        self.assertEqual([item["id"] for item in matching.json()["items"]], [matching_waiter_id])

    async def test_new_confirmation_sends_one_separate_cash_notice(self):
        booking_date = now_kz().date() + timedelta(days=1)
        async with async_session() as db:
            instructor = Instructor(
                name="Инструктор наличных", transmission="automatic", gender="any", is_active=True,
                working_hours_start=time(9, 0), working_hours_end=time(20, 0), days_off="",
            )
            client = Client(
                name="Клиент наличных", phone="+77000000065", telegram_id="cash-notice-chat",
            )
            db.add_all([instructor, client])
            await db.flush()
            booking = Booking(
                client_id=client.id, instructor_id=instructor.id,
                service_type="training", transmission="automatic", location="Циолковского 30",
                booking_date=booking_date, start_time=time(10, 0), end_time=time(11, 0),
                status="pending", price=10000, source="telegram",
            )
            db.add(booking)
            await db.commit()
            booking_id = booking.id

        RecordingTelegramClient.requests = []
        with patch("app.routers.admin.httpx.AsyncClient", RecordingTelegramClient), patch.object(
            settings, "BOT_TOKEN", "test-bot-token"
        ):
            confirmed = await self.client.post(
                f"/api/admin/bookings/{booking_id}/confirm", json={"action": "confirm"}
            )
            self.assertEqual(confirmed.status_code, 200, confirmed.text)
            repeated = await self.client.post(
                f"/api/admin/bookings/{booking_id}/confirm", json={"action": "confirm"}
            )
            self.assertEqual(repeated.status_code, 200, repeated.text)

        self.assertEqual(len(RecordingTelegramClient.requests), 2)
        self.assertIn("Ваша заявка подтверждена", RecordingTelegramClient.requests[0]["json"]["text"])
        self.assertEqual(
            RecordingTelegramClient.requests[1]["json"]["text"],
            (
                "📢 Уважаемые клиенты!\n"
                "💵 Обращаем ваше внимание: <b>оплатить занятие можно наличными или через Kaspi QR.</b>\n"
                "🙏 Пожалуйста, учитывайте это перед занятием.\n"
                "🤝 Спасибо!"
            ),
        )

    async def test_admin_reminder_text_includes_cash_only_notice(self):
        booking_date = now_kz().date() + timedelta(days=1)
        async with async_session() as db:
            instructor = Instructor(
                name="Артем", transmission="manual", gender="any", is_active=True,
                working_hours_start=time(9, 0), working_hours_end=time(20, 0), days_off="",
            )
            client = Client(name="Клиент напоминания", phone="+77000000066")
            db.add_all([instructor, client])
            await db.flush()
            booking = Booking(
                client_id=client.id, instructor_id=instructor.id,
                service_type="training", transmission="manual", location="Циолковского 30",
                booking_date=booking_date, start_time=time(18, 0), end_time=time(19, 0),
                status="confirmed", price=10000, source="telegram", booking_number="000222",
            )
            db.add(booking)
            await db.commit()
            booking_id = booking.id

        response = await self.client.get(f"/api/admin/bookings/{booking_id}/reminder-text")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json()["text"],
            (
                "🔔 Напоминание о записи!\n"
                "Ваше занятие уже через 1 час.\n"
                "📋 Номер записи: 000222\n"
                "📍 Адрес: Циолковского 30\n"
                "⏰ Время: 18:00\n"
                "🚗 Программа: Обучение вождению (Механика)\n"
                "👨‍🏫 Инструктор: Артем\n"
                "💵 Оплатить занятие можно наличными или через Kaspi QR.\n"
                "⏱️ Пожалуйста, не опаздывайте.\n"
                "🚦 Хорошего занятия!"
            ),
        )

    async def test_reconnect_marks_only_same_client_different_time_as_disputed(self):
        booking_date = now_kz().date() + timedelta(days=1)
        async with async_session() as db:
            instructor = Instructor(
                name="Инструктор спорной записи", transmission="automatic", gender="any", is_active=True,
                working_hours_start=time(9, 0), working_hours_end=time(20, 0), days_off="",
            )
            client = Client(name="Клиент спорной записи", phone="+77000000061")
            db.add_all([instructor, client])
            await db.flush()
            pending = Booking(
                client_id=client.id, instructor_id=instructor.id,
                service_type="training", transmission="automatic", location="Циолковского 30",
                booking_date=booking_date, start_time=time(11, 0), end_time=time(12, 0),
                status="pending", price=10000, source="mobile",
            )
            db.add(pending)
            await db.commit()
            pending_id, instructor_id = pending.id, instructor.id

        synced = await self.client.post("/api/admin/offline-sync", json=[{
            "id": "offline-dispute-op", "method": "POST", "path": "/bookings/manual",
            "body": {
                "client_name": "Клиент спорной записи", "client_phone": "+77000000061",
                "instructor_id": instructor_id, "service_type": "training", "transmission": "automatic",
                "booking_date": booking_date.isoformat(), "start_time": "13:00",
                "location": "Циолковского 30",
            },
        }])
        self.assertEqual(synced.status_code, 200, synced.text)
        self.assertEqual(synced.json()["results"][0]["status"], "ok")

        async with async_session() as db:
            pending = await db.get(Booking, pending_id)
            manual = (await db.execute(select(Booking).where(
                Booking.offline_operation_id == "offline-dispute-op"
            ))).scalar_one()
            self.assertEqual(pending.status, "disputed")
            self.assertIn("13:00", pending.conflict_reason)
            self.assertEqual(manual.status, "confirmed")
            self.assertEqual(manual.source, "admin_offline")
            self.assertEqual(manual.client_id, pending.client_id)

        conflicts = await self.client.get("/api/admin/bookings/conflicts")
        self.assertEqual(conflicts.status_code, 200, conflicts.text)
        conflicted = [
            booking for group in conflicts.json()["groups"] for booking in group["bookings"]
            if booking["id"] == pending_id
        ]
        self.assertEqual(len(conflicted), 1)
        self.assertIn("13:00", conflicted[0]["conflict_reason"])

        confirmed = await self.client.post(
            f"/api/admin/bookings/{pending_id}/confirm", json={"action": "confirm"}
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        async with async_session() as db:
            self.assertEqual((await db.get(Booking, pending_id)).status, "confirmed")

    async def test_reconnect_can_cancel_same_client_disputed_request(self):
        booking_date = now_kz().date() + timedelta(days=1)
        async with async_session() as db:
            instructor = Instructor(
                name="Инструктор отмены спора", transmission="automatic", gender="any", is_active=True,
                working_hours_start=time(9, 0), working_hours_end=time(20, 0), days_off="",
            )
            client = Client(
                name="Клиент отмены спора", phone="+77000000062",
                telegram_id="cancellation-chat",
            )
            db.add_all([instructor, client])
            await db.flush()
            pending = Booking(
                client_id=client.id, instructor_id=instructor.id,
                service_type="training", transmission="automatic", location="Циолковского 30",
                booking_date=booking_date, start_time=time(10, 0), end_time=time(11, 0),
                status="pending", price=10000, source="telegram",
            )
            db.add(pending)
            await db.commit()
            pending_id, instructor_id = pending.id, instructor.id

        synced = await self.client.post("/api/admin/offline-sync", json=[{
            "id": "offline-dispute-cancel-op", "method": "POST", "path": "/bookings/manual",
            "body": {
                "client_name": "Клиент отмены спора", "client_phone": "+77000000062",
                "instructor_id": instructor_id, "service_type": "training", "transmission": "automatic",
                "booking_date": booking_date.isoformat(), "start_time": "14:00",
                "location": "Циолковского 30",
            },
        }])
        self.assertEqual(synced.status_code, 200, synced.text)
        RecordingTelegramClient.requests = []
        with patch("app.routers.admin.httpx.AsyncClient", RecordingTelegramClient), patch.object(
            settings, "BOT_TOKEN", "test-bot-token"
        ):
            cancelled = await self.client.post(
                f"/api/admin/bookings/{pending_id}/confirm",
                json={"action": "reject", "rejection_reason": "Запись создана по ошибке"},
            )
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        self.assertEqual(len(RecordingTelegramClient.requests), 1)
        self.assertIn("Ваша заявка отклонена", RecordingTelegramClient.requests[0]["json"]["text"])
        self.assertNotIn("наличными", RecordingTelegramClient.requests[0]["json"]["text"])
        async with async_session() as db:
            self.assertEqual((await db.get(Booking, pending_id)).status, "cancelled")

    async def test_reconnect_merges_same_time_and_leaves_ordinary_online_requests_pending(self):
        booking_date = now_kz().date() + timedelta(days=1)
        async with async_session() as db:
            instructor = Instructor(
                name="Инструктор объединения", transmission="automatic", gender="any", is_active=True,
                working_hours_start=time(9, 0), working_hours_end=time(20, 0), days_off="",
            )
            duplicate_client = Client(name="Клиент объединения", phone="+77000000063")
            online_client = Client(name="Клиент двух заявок", phone="+77000000064")
            db.add_all([instructor, duplicate_client, online_client])
            await db.flush()
            same_time = Booking(
                client_id=duplicate_client.id, instructor_id=instructor.id,
                service_type="training", transmission="automatic", location="Циолковского 30",
                booking_date=booking_date, start_time=time(10, 0), end_time=time(11, 0),
                status="pending", price=10000, source="mobile",
            )
            ordinary_first = Booking(
                client_id=online_client.id, instructor_id=instructor.id,
                service_type="training", transmission="automatic", location="Циолковского 30",
                booking_date=booking_date, start_time=time(14, 0), end_time=time(15, 0),
                status="pending", price=10000, source="mobile",
            )
            ordinary_second = Booking(
                client_id=online_client.id, instructor_id=instructor.id,
                service_type="training", transmission="automatic", location="Циолковского 30",
                booking_date=booking_date, start_time=time(16, 0), end_time=time(17, 0),
                status="pending", price=10000, source="telegram",
            )
            db.add_all([same_time, ordinary_first, ordinary_second])
            await db.commit()
            duplicate_client_id = duplicate_client.id
            ordinary_ids = (ordinary_first.id, ordinary_second.id)
            instructor_id = instructor.id

        synced = await self.client.post("/api/admin/offline-sync", json=[{
            "id": "offline-merge-op", "method": "POST", "path": "/bookings/manual",
            "body": {
                "client_name": "Клиент объединения", "client_phone": "+77000000063",
                "instructor_id": instructor_id, "service_type": "training", "transmission": "automatic",
                "booking_date": booking_date.isoformat(), "start_time": "10:00",
                "location": "Циолковского 30",
            },
        }])
        self.assertEqual(synced.status_code, 200, synced.text)

        async with async_session() as db:
            duplicate_rows = (await db.execute(select(Booking).where(
                Booking.client_id == duplicate_client_id,
                Booking.booking_date == booking_date,
            ))).scalars().all()
            self.assertEqual(len(duplicate_rows), 1)
            self.assertEqual(duplicate_rows[0].source, "admin_offline")
            self.assertEqual(duplicate_rows[0].status, "confirmed")
            ordinary_statuses = [
                (await db.get(Booking, booking_id)).status for booking_id in ordinary_ids
            ]
            self.assertEqual(ordinary_statuses, ["pending", "pending"])

        repeated_check = await self.client.post("/api/admin/bookings/check-pending-conflicts", json={})
        self.assertEqual(repeated_check.status_code, 200, repeated_check.text)
        self.assertEqual(repeated_check.json()["conflicts_count"], 0)

    async def test_instructor_profile_change_grandfathers_existing_booking_only(self):
        booking_date = now_kz().date() + timedelta(days=1)
        async with async_session() as db:
            instructor = Instructor(
                name="Защита специализации", transmission="automatic",
                lesson_type="training", gender="any", is_active=True,
                working_hours_start=time(9, 0), working_hours_end=time(20, 0), days_off="",
            )
            client = Client(name="Клиент старой заявки", phone="+77000000041")
            db.add_all([instructor, client])
            await db.flush()
            booking = Booking(
                client_id=client.id, instructor_id=instructor.id,
                service_type="training", transmission="automatic",
                location="Циолковского 30", booking_date=booking_date,
                start_time=time(10, 0), end_time=time(11, 0), status="pending",
                price=10000, source="mobile", admin_confirmed=False,
            )
            db.add(booking)
            await db.commit()
            instructor_id, booking_id = instructor.id, booking.id

        update_response = await self.client.put(
            f"/api/admin/instructors/{instructor_id}", json={"lesson_type": "exam"}
        )
        self.assertEqual(update_response.status_code, 200, update_response.text)
        confirm_response = await self.client.post(
            f"/api/admin/bookings/{booking_id}/confirm", json={"action": "confirm"}
        )
        self.assertEqual(confirm_response.status_code, 200, confirm_response.text)

        rejected_new_booking = await self.client.post("/api/admin/bookings/manual", json={
            "client_name": "Новая несовместимая заявка",
            "client_phone": "+77000000042",
            "instructor_id": instructor_id,
            "service_type": "training",
            "transmission": "automatic",
            "booking_date": booking_date.isoformat(),
            "start_time": "12:00",
            "location": "Циолковского 30",
        })
        self.assertEqual(rejected_new_booking.status_code, 400, rejected_new_booking.text)

        async with async_session() as db:
            instructor = await db.get(Instructor, instructor_id)
            booking = await db.get(Booking, booking_id)
            self.assertEqual(instructor.lesson_type, "exam")
            self.assertEqual(booking.instructor_id, instructor_id)
            self.assertEqual(booking.service_type, "training")
            self.assertEqual(booking.status, "confirmed")

    async def test_instructor_delete_keeps_history_and_blocks_only_active_bookings(self):
        historical_date = now_kz().date() - timedelta(days=3)
        active_date = now_kz().date() + timedelta(days=1)
        async with async_session() as db:
            historical_instructor = Instructor(
                name="Удаляемый инструктор с историей", transmission="both", lesson_type="both",
                gender="any", is_active=True, working_hours_start=time(9, 0),
                working_hours_end=time(20, 0), days_off="",
            )
            active_instructor = Instructor(
                name="Инструктор с активной записью", transmission="both", lesson_type="both",
                gender="any", is_active=True, working_hours_start=time(9, 0),
                working_hours_end=time(20, 0), days_off="",
            )
            client = Client(name="Клиент истории инструктора", phone="+77000000046")
            mobile_user = MobileUser(
                name="Мобильный клиент истории", phone="+77000000047", password_hash="secret",
            )
            db.add_all([historical_instructor, active_instructor, client, mobile_user])
            await db.flush()
            historical_booking = Booking(
                client_id=client.id, instructor_id=historical_instructor.id,
                service_type="training", transmission="automatic", location="Циолковского 30",
                booking_date=historical_date, start_time=time(10, 0), end_time=time(11, 0),
                status="completed", price=10000, source="manual", admin_confirmed=True,
            )
            active_booking = Booking(
                client_id=client.id, instructor_id=active_instructor.id,
                service_type="training", transmission="automatic", location="Циолковского 30",
                booking_date=active_date, start_time=time(12, 0), end_time=time(13, 0),
                status="confirmed", price=10000, source="manual", admin_confirmed=True,
            )
            mobile_booking = MobileBooking(
                user_id=mobile_user.id, instructor_id=historical_instructor.id,
                booking_date=historical_date, start_time=time(14, 0), end_time=time(15, 0),
                service_type="training", transmission="automatic", location="Циолковского 30",
                status="completed", price=10000,
            )
            db.add_all([historical_booking, active_booking, mobile_booking])
            await db.flush()
            rating = RatingRecord(booking_id=historical_booking.id, instructor_id=historical_instructor.id, vote="good")
            event = Event(
                event_type="booking_completed", source="admin", instructor_id=historical_instructor.id,
                booking_id=historical_booking.id, message="Историческое событие инструктора",
            )
            notification = NotificationSent(instructor_id=historical_instructor.id, notification_type="lesson_reminder")
            message = SupportMessage(
                instructor_id=historical_instructor.id, channel="instructor", sender="admin", text="История переписки",
            )
            waiting = WaitingListEntry(
                name="Клиент листа ожидания", phone="+77000000048", instructor_id=historical_instructor.id,
            )
            db.add_all([rating, event, notification, message, waiting])
            await db.commit()
            ids = {
                "historical_instructor": historical_instructor.id,
                "active_instructor": active_instructor.id,
                "historical_booking": historical_booking.id,
                "mobile_booking": mobile_booking.id,
                "rating": rating.id,
                "event": event.id,
                "notification": notification.id,
                "message": message.id,
                "waiting": waiting.id,
            }

        active_delete = await self.client.delete(f"/api/admin/instructors/{ids['active_instructor']}")
        self.assertEqual(active_delete.status_code, 409, active_delete.text)
        self.assertIn("активные записи", active_delete.text)

        historical_delete = await self.client.delete(
            f"/api/admin/instructors/{ids['historical_instructor']}"
        )
        self.assertEqual(historical_delete.status_code, 200, historical_delete.text)

        async with async_session() as db:
            self.assertIsNone(await db.get(Instructor, ids["historical_instructor"]))
            self.assertIsNotNone(await db.get(Instructor, ids["active_instructor"]))
            self.assertIsNone((await db.get(Booking, ids["historical_booking"])).instructor_id)
            self.assertIsNone((await db.get(MobileBooking, ids["mobile_booking"])).instructor_id)
            self.assertIsNone((await db.get(RatingRecord, ids["rating"])).instructor_id)
            self.assertIsNone((await db.get(Event, ids["event"])).instructor_id)
            self.assertIsNone((await db.get(NotificationSent, ids["notification"])).instructor_id)
            self.assertIsNone((await db.get(SupportMessage, ids["message"])).instructor_id)
            self.assertIsNone((await db.get(WaitingListEntry, ids["waiting"])).instructor_id)

    async def test_client_delete_removes_only_owned_data_and_preserves_links(self):
        booking_date = now_kz().date() + timedelta(days=2)
        async with async_session() as db:
            instructor = Instructor(
                name="Инструктор с историей", transmission="both", lesson_type="both",
                gender="any", is_active=True, working_hours_start=time(9, 0),
                working_hours_end=time(20, 0), days_off="",
            )
            client = Client(
                name="Клиент с историей", phone="+77000000043",
                telegram_id="7000000043", password_hash="secret", avatar_url="avatar.png",
                referral_code="KEEP-REFERRAL",
            )
            referred_client = Client(
                name="Приглашённый клиент", phone="+77000000045",
                referred_by_client_id=None,
            )
            empty_instructor = Instructor(
                name="Пустая карточка инструктора", transmission="both", lesson_type="both",
                gender="any", is_active=True, working_hours_start=time(9, 0),
                working_hours_end=time(20, 0), days_off="",
            )
            empty_client = Client(name="Пустая карточка клиента", phone="+77000000044")
            package = Package(
                name="Пакет удаляемого клиента", sessions_count=6, price=55000,
                validity_days=30, bonus_exam=False, code="DELETE-PACKAGE",
            )
            certificate = Certificate(
                code="DELETE-CERTIFICATE", nominal=10000, remaining=10000,
                activated_by_client_id=None, used_by_user_id=None,
            )
            db.add_all([
                instructor, client, referred_client, empty_instructor, empty_client,
                package, certificate,
            ])
            await db.flush()
            referred_client.referred_by_client_id = client.id
            certificate.activated_by_client_id = client.id
            certificate.used_by_user_id = client.id
            booking = Booking(
                client_id=client.id, instructor_id=instructor.id,
                service_type="training", transmission="automatic",
                location="Циолковского 30", booking_date=booking_date,
                start_time=time(13, 0), end_time=time(14, 0), status="confirmed",
                price=10000, source="manual", admin_confirmed=True,
                certificate_id=certificate.id,
            )
            db.add(booking)
            await db.flush()
            db.add_all([
                SupportMessage(
                    client_id=client.id, channel="client", sender="user", text="История поддержки",
                ),
                Event(
                    event_type="booking_confirmed", source="mobile", client_id=client.id,
                    booking_id=booking.id, message="Событие клиента",
                ),
                ClientBlock(
                    client_id=client.id, blocked_until=now_kz() + timedelta(days=1), reason="Тест",
                ),
                MobileSession(
                    id="deleted-client-session", client_id=client.id,
                    expires_at=now_kz() + timedelta(days=1),
                ),
                ClientPackage(
                    client_id=client.id, package_id=package.id, remaining_sessions=6,
                    is_active=True,
                ),
                CertificateRequest(
                    client_id=client.id, code_entered=certificate.code,
                    matched_certificate_id=certificate.id, booking_id=booking.id,
                    status="approved",
                ),
                ReferralRecord(
                    referrer_client_id=client.id, referred_client_id=referred_client.id,
                ),
            ])
            await db.commit()
            protected_instructor_id, protected_client_id, booking_id = (
                instructor.id, client.id, booking.id
            )
            referred_client_id = referred_client.id
            certificate_id, package_id = certificate.id, package.id
            empty_instructor_id, empty_client_id = empty_instructor.id, empty_client.id

        instructor_delete = await self.client.delete(
            f"/api/admin/instructors/{protected_instructor_id}"
        )
        client_delete = await self.client.delete(f"/api/admin/clients/{protected_client_id}")
        self.assertEqual(instructor_delete.status_code, 409, instructor_delete.text)
        self.assertEqual(client_delete.status_code, 200, client_delete.text)
        self.assertIn("активные записи", instructor_delete.text)

        self.assertEqual(
            (await self.client.delete(f"/api/admin/instructors/{empty_instructor_id}")).status_code,
            200,
        )
        self.assertEqual(
            (await self.client.delete(f"/api/admin/clients/{empty_client_id}")).status_code,
            200,
        )

        async with async_session() as db:
            self.assertIsNotNone(await db.get(Instructor, protected_instructor_id))
            deleted_client = await db.get(Client, protected_client_id)
            self.assertIsNotNone(deleted_client)
            self.assertTrue(deleted_client.is_deleted)
            self.assertEqual(deleted_client.phone, "+77000000043")
            self.assertEqual(deleted_client.telegram_id, "7000000043")
            self.assertEqual(deleted_client.password_hash, "secret")
            self.assertEqual(deleted_client.avatar_url, "avatar.png")
            self.assertEqual(deleted_client.referral_code, "KEEP-REFERRAL")
            self.assertIsNone(await db.get(Booking, booking_id))
            self.assertEqual(await db.scalar(select(func.count()).select_from(SupportMessage).where(
                SupportMessage.client_id == protected_client_id,
            )), 0)
            self.assertEqual(await db.scalar(select(func.count()).select_from(Event).where(
                Event.client_id == protected_client_id,
            )), 1)
            preserved_event = (await db.execute(select(Event).where(
                Event.client_id == protected_client_id,
            ))).scalar_one()
            self.assertIsNone(preserved_event.booking_id)
            self.assertEqual(await db.scalar(select(func.count()).select_from(ClientBlock).where(
                ClientBlock.client_id == protected_client_id,
            )), 1)
            self.assertEqual(await db.scalar(select(func.count()).select_from(ReferralRecord).where(
                ReferralRecord.referrer_client_id == protected_client_id,
                ReferralRecord.referred_client_id == referred_client_id,
            )), 1)
            self.assertEqual(
                (await db.get(Client, referred_client_id)).referred_by_client_id,
                protected_client_id,
            )
            self.assertIsNone(await db.get(MobileSession, "deleted-client-session"))
            self.assertIsNone(await db.get(Certificate, certificate_id))
            self.assertEqual(await db.scalar(select(func.count()).select_from(ClientPackage).where(
                ClientPackage.client_id == protected_client_id,
            )), 0)
            self.assertEqual(await db.scalar(select(func.count()).select_from(CertificateRequest).where(
                CertificateRequest.client_id == protected_client_id,
            )), 0)
            self.assertIsNone(await db.get(Package, package_id))
            self.assertIsNone(await db.get(Instructor, empty_instructor_id))
            self.assertTrue((await db.get(Client, empty_client_id)).is_deleted)

        visible_clients = await self.client.get("/api/admin/clients")
        self.assertEqual(visible_clients.status_code, 200, visible_clients.text)
        visible_ids = {item["id"] for item in visible_clients.json()}
        self.assertNotIn(protected_client_id, visible_ids)
        self.assertNotIn(empty_client_id, visible_ids)

    async def test_deleted_clients_can_be_reactivated_by_admin_and_manual_booking(self):
        async with async_session() as db:
            form_client = Client(
                name="Удалённый клиент формы", phone="+77000000067",
                telegram_id="deleted-admin-form", password_hash="old-password", is_deleted=True,
            )
            booking_client = Client(
                name="Удалённый ручной клиент", phone="+77000000068",
                telegram_id="deleted-manual-booking", password_hash="old-password", is_deleted=True,
            )
            instructor = Instructor(
                name="Инструктор повторной записи", transmission="automatic",
                lesson_type="both", gender="any", is_active=True,
                working_hours_start=time(9, 0), working_hours_end=time(20, 0), days_off="",
            )
            db.add_all([form_client, booking_client, instructor])
            await db.flush()
            db.add_all([
                ClientBlock(
                    client_id=form_client.id, blocked_until=now_kz() + timedelta(days=1),
                    reason="Старое ограничение формы",
                ),
                ClientBlock(
                    client_id=booking_client.id, blocked_until=now_kz() + timedelta(days=1),
                    reason="Старое ограничение записи",
                ),
                MobileSession(
                    id="deleted-manual-session", client_id=booking_client.id,
                    expires_at=now_kz() + timedelta(days=1),
                ),
            ])
            await db.commit()
            form_client_id = form_client.id
            booking_client_id = booking_client.id
            instructor_id = instructor.id

        recreated = await self.client.post("/api/admin/clients", json={
            "name": "Клиент формы снова",
            "phone": "+7 700 000 00 67",
            "password": "NewPassword1",
        })
        self.assertEqual(recreated.status_code, 200, recreated.text)
        self.assertEqual(recreated.json()["client_id"], form_client_id)

        booking_window = (await self.client.get("/api/admin/booking-window")).json()
        manual = await self.client.post("/api/admin/bookings/manual", json={
            "client_name": "Ручной клиент снова",
            "client_phone": "+7 700 000 00 68",
            "instructor_id": instructor_id,
            "service_type": "training",
            "transmission": "automatic",
            "booking_date": booking_window["max_date"],
            "start_time": "18:00",
            "location": "Циолковского 30",
        })
        self.assertEqual(manual.status_code, 200, manual.text)
        self.assertEqual(manual.json()["client_id"], booking_client_id)

        async with async_session() as db:
            form_client = await db.get(Client, form_client_id)
            booking_client = await db.get(Client, booking_client_id)
            blocks = (await db.execute(select(ClientBlock).where(
                ClientBlock.client_id.in_([form_client_id, booking_client_id]),
            ))).scalars().all()
            session = await db.get(MobileSession, "deleted-manual-session")
            self.assertFalse(form_client.is_deleted)
            self.assertEqual(form_client.name, "Клиент формы снова")
            self.assertIsNone(form_client.telegram_id)
            self.assertFalse(booking_client.is_deleted)
            self.assertEqual(booking_client.name, "Ручной клиент снова")
            self.assertIsNone(booking_client.telegram_id)
            self.assertIsNone(booking_client.password_hash)
            self.assertTrue(all(block.blocked_until <= now_kz() for block in blocks))
            self.assertFalse(session.is_active)

    async def test_schedule_changes_cannot_hide_active_booking(self):
        booking_date = now_kz().date() + timedelta(days=3)
        async with async_session() as db:
            instructor = Instructor(
                name="Защита графика", transmission="automatic", lesson_type="both",
                gender="any", is_active=True, working_hours_start=time(9, 0),
                working_hours_end=time(19, 0), days_off="",
            )
            client = Client(name="Клиент графика", phone="+77000000070")
            db.add_all([instructor, client])
            await db.flush()
            booking = Booking(
                client_id=client.id, instructor_id=instructor.id,
                service_type="training", transmission="automatic",
                location="Циолковского 30", booking_date=booking_date,
                start_time=time(18, 0), end_time=time(19, 0), status="confirmed",
                price=10000, source="manual", admin_confirmed=True,
            )
            db.add(booking)
            await db.commit()
            instructor_id, booking_id = instructor.id, booking.id

        shorter_day = await self.client.put(
            f"/api/admin/instructors/{instructor_id}", json={"working_hours_end": "17:00"}
        )
        self.assertEqual(shorter_day.status_code, 409, shorter_day.text)
        self.assertIn("активными записями", shorter_day.text)

        conflicting_lunch = await self.client.put(
            f"/api/admin/instructors/{instructor_id}/daily-schedules",
            json={
                "schedule_date": booking_date.isoformat(), "is_day_off": False,
                "working_hours_start": "09:00", "working_hours_end": "19:00",
                "lunch_start": "17:30", "lunch_end": "18:30",
            },
        )
        self.assertEqual(conflicting_lunch.status_code, 409, conflicting_lunch.text)

        safe_extension = await self.client.put(
            f"/api/admin/instructors/{instructor_id}", json={"working_hours_end": "20:00"}
        )
        self.assertEqual(safe_extension.status_code, 200, safe_extension.text)

        async with async_session() as db:
            instructor = await db.get(Instructor, instructor_id)
            booking = await db.get(Booking, booking_id)
            daily_count = await db.scalar(select(func.count()).select_from(
                InstructorDailySchedule
            ).where(
                InstructorDailySchedule.instructor_id == instructor_id,
                InstructorDailySchedule.schedule_date == booking_date,
            ))
            self.assertEqual(instructor.working_hours_end, time(20, 0))
            self.assertEqual(booking.status, "confirmed")
            self.assertEqual(daily_count, 0)

    async def test_daily_schedule_override_cannot_be_removed_over_active_booking(self):
        booking_date = now_kz().date() + timedelta(days=5)
        async with async_session() as db:
            instructor = Instructor(
                name="Защита удаления графика", transmission="both", lesson_type="both",
                gender="any", is_active=True, working_hours_start=time(9, 0),
                working_hours_end=time(17, 0), days_off="",
            )
            client = Client(name="Клиент индивидуального графика", phone="+77000000049")
            db.add_all([instructor, client])
            await db.flush()
            schedule = InstructorDailySchedule(
                instructor_id=instructor.id, schedule_date=booking_date,
                is_day_off=False, working_hours_start=time(9, 0),
                working_hours_end=time(19, 0),
            )
            booking = Booking(
                client_id=client.id, instructor_id=instructor.id,
                service_type="training", transmission="automatic",
                location="Циолковского 30", booking_date=booking_date,
                start_time=time(18, 0), end_time=time(19, 0), status="confirmed",
                price=10000, source="manual", admin_confirmed=True,
            )
            db.add_all([schedule, booking])
            await db.commit()
            instructor_id, booking_id, schedule_id = instructor.id, booking.id, schedule.id

        response = await self.client.delete(
            f"/api/admin/instructors/{instructor_id}/daily-schedules/{booking_date.isoformat()}"
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("индивидуальный график", response.text)

        async with async_session() as db:
            self.assertIsNotNone(await db.get(InstructorDailySchedule, schedule_id))
            booking = await db.get(Booking, booking_id)
            self.assertEqual(booking.status, "confirmed")
            self.assertEqual(booking.start_time, time(18, 0))

    async def test_existing_assignment_survives_admin_edit_but_new_assignment_uses_current_card(self):
        booking_date = now_kz().date() + timedelta(days=4)
        async with async_session() as db:
            original = Instructor(
                name="Старое назначение", transmission="manual", lesson_type="exam",
                gender="any", is_active=True, working_hours_start=time(9, 0),
                working_hours_end=time(20, 0), days_off="",
            )
            incompatible_target = Instructor(
                name="Несовместимый новый", transmission="manual", lesson_type="exam",
                gender="any", is_active=True, working_hours_start=time(9, 0),
                working_hours_end=time(20, 0), days_off="",
            )
            client = Client(name="Клиент переназначения", phone="+77000000050")
            db.add_all([original, incompatible_target, client])
            await db.flush()
            booking = Booking(
                client_id=client.id, instructor_id=original.id,
                service_type="training", transmission="automatic",
                location="Циолковского 30", booking_date=booking_date,
                start_time=time(10, 0), end_time=time(11, 0), status="confirmed",
                price=10000, source="manual", admin_confirmed=True,
            )
            db.add(booking)
            await db.commit()
            booking_id = booking.id
            original_id, incompatible_id = original.id, incompatible_target.id

        same_assignment = await self.client.put(
            f"/api/admin/bookings/{booking_id}/edit",
            json={"new_start_time": "11:00", "new_transmission": "automatic"},
        )
        self.assertEqual(same_assignment.status_code, 200, same_assignment.text)

        rejected_reassignment = await self.client.put(
            f"/api/admin/bookings/{booking_id}/reassign",
            json={"new_instructor_id": incompatible_id},
        )
        self.assertEqual(rejected_reassignment.status_code, 409, rejected_reassignment.text)

        async with async_session() as db:
            booking = await db.get(Booking, booking_id)
            self.assertEqual(booking.instructor_id, original_id)
            self.assertEqual(booking.transmission, "automatic")
            self.assertEqual(booking.start_time, time(11, 0))
            self.assertEqual(booking.status, "confirmed")

    async def test_admin_can_confirm_reschedule_for_grandfathered_assignment(self):
        original_date = now_kz().date() + timedelta(days=3)
        requested_date = now_kz().date() + timedelta(days=4)
        async with async_session() as db:
            instructor = Instructor(
                name="Сохранённый перенос", transmission="manual", lesson_type="exam",
                gender="any", is_active=True, working_hours_start=time(9, 0),
                working_hours_end=time(20, 0), days_off="",
            )
            client = Client(
                name="Клиент сохранённого переноса", phone="+77000000051",
                telegram_id="reschedule-chat",
            )
            db.add_all([instructor, client])
            await db.flush()
            booking = Booking(
                client_id=client.id, instructor_id=instructor.id,
                service_type="training", transmission="automatic",
                location="Циолковского 30", booking_date=original_date,
                start_time=time(10, 0), end_time=time(11, 0),
                status="reschedule_pending", reschedule_previous_status="confirmed",
                requested_reschedule_date=requested_date,
                requested_reschedule_start_time=time(12, 0),
                requested_reschedule_end_time=time(13, 0),
                price=10000, source="mobile", admin_confirmed=True,
            )
            db.add(booking)
            await db.commit()
            booking_id, instructor_id = booking.id, instructor.id

        RecordingTelegramClient.requests = []
        with patch("app.routers.admin.httpx.AsyncClient", RecordingTelegramClient), patch.object(
            settings, "BOT_TOKEN", "test-bot-token"
        ):
            response = await self.client.post(
                f"/api/admin/bookings/{booking_id}/reschedule-request/resolve",
                json={"action": "confirm"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(RecordingTelegramClient.requests), 1)
        self.assertIn("заявка на перенос подтверждена", RecordingTelegramClient.requests[0]["json"]["text"])
        self.assertNotIn("наличными", RecordingTelegramClient.requests[0]["json"]["text"])

        async with async_session() as db:
            booking = await db.get(Booking, booking_id)
            self.assertEqual(booking.status, "confirmed")
            self.assertEqual(booking.instructor_id, instructor_id)
            self.assertEqual(booking.booking_date, requested_date)
            self.assertEqual(booking.start_time, time(12, 0))
            self.assertIsNone(booking.requested_reschedule_date)

    async def test_client_edit_preserves_booking_and_rejects_identity_collision(self):
        booking_date = now_kz().date() + timedelta(days=4)
        async with async_session() as db:
            instructor = Instructor(
                name="Инструктор клиента", transmission="both", lesson_type="both",
                gender="any", is_active=True, working_hours_start=time(9, 0),
                working_hours_end=time(20, 0), days_off="",
            )
            client = Client(
                name="Исходное имя", phone="+77000000046", referral_code="SAFE-CLIENT-46"
            )
            other = Client(
                name="Другой клиент", phone="+77000000047", referral_code="SAFE-CLIENT-47"
            )
            db.add_all([instructor, client, other])
            await db.flush()
            booking = Booking(
                client_id=client.id, instructor_id=instructor.id,
                service_type="exam", transmission="automatic",
                location="Циолковского 30", booking_date=booking_date,
                start_time=time(15, 0), end_time=time(15, 20), status="confirmed",
                price=5000, source="manual", admin_confirmed=True,
            )
            db.add(booking)
            await db.commit()
            client_id, booking_id = client.id, booking.id

        collision = await self.client.put(f"/api/admin/clients/{client_id}", json={
            "name": "Не должно сохраниться", "phone": "+77000000047",
        })
        self.assertEqual(collision.status_code, 409, collision.text)
        self.assertIn("объединение дублей", collision.text)

        valid_update = await self.client.put(f"/api/admin/clients/{client_id}", json={
            "name": "Исправленное имя", "phone": "+77000000048",
            "referral_code": "SAFE-CLIENT-48",
        })
        self.assertEqual(valid_update.status_code, 200, valid_update.text)

        async with async_session() as db:
            client = await db.get(Client, client_id)
            booking = await db.get(Booking, booking_id)
            self.assertEqual(client.name, "Исправленное имя")
            self.assertEqual(client.phone, "+77000000048")
            self.assertEqual(booking.client_id, client_id)
            self.assertEqual(booking.service_type, "exam")
            self.assertEqual(booking.status, "confirmed")

    async def test_waiting_list_offline_sync_uses_normal_validation_and_replays_once(self):
        async with async_session() as db:
            before = await db.scalar(select(func.count()).select_from(WaitingListEntry))
        operation = {
            "id": "sync-waiting-op", "method": "POST", "path": "/waiting-list",
            "body": {"name": "Из офлайна", "phone": "+77000000003", "desired_date": "2026-08-25",
                     "desired_time_start": "10:00", "desired_time_end": "11:00",
                     "transmission": "automatic", "instructor_id": None,
                     "instructor_gender": None, "notes": "Тест"},
        }
        first = await self.client.post("/api/admin/offline-sync", json=[operation])
        second = await self.client.post("/api/admin/offline-sync", json=[operation])
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(first.json()["results"][0]["status"], "ok")
        self.assertEqual(second.json()["results"][0]["status"], "ok")
        async with async_session() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(WaitingListEntry)), before + 1)

    async def test_booking_source_analytics_counts_actions_not_clients(self):
        before_response = await self.client.get("/api/admin/analytics/booking-sources")
        self.assertEqual(before_response.status_code, 200, before_response.text)
        before = before_response.json()

        async with async_session() as db:
            instructor = Instructor(
                name="Источник статистики", transmission="automatic", gender="any", is_active=True,
                working_hours_start=time(9, 0), working_hours_end=time(20, 0), days_off="",
            )
            client = Client(name="Один клиент трёх каналов", phone="+77000000019")
            db.add_all([instructor, client])
            await db.flush()
            common = {
                "client_id": client.id, "instructor_id": instructor.id,
                "service_type": "training", "transmission": "automatic",
                "location": "Циолковского 30", "booking_date": now_kz().date(),
                "start_time": time(9, 0), "end_time": time(10, 0),
                "status": "confirmed", "price": 10000,
            }
            db.add_all([
                Booking(source="telegram", **common), Booking(source="mobile", **common),
                Booking(source="manual", **common), Booking(source="admin_offline", **common),
            ])
            await db.commit()

        response = await self.client.get("/api/admin/analytics/booking-sources")
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(set(data), {"total", "telegram", "mobile", "manual", "unknown"})
        self.assertEqual(data["telegram"]["count"], before["telegram"]["count"] + 1)
        self.assertEqual(data["mobile"]["count"], before["mobile"]["count"] + 1)
        self.assertEqual(data["manual"]["count"], before["manual"]["count"] + 2)
        self.assertEqual(data["total"], before["total"] + 4)

    async def test_extended_booking_source_analytics_splits_today_and_all_time(self):
        before_response = await self.client.get("/api/admin/analytics/booking-sources/extended")
        self.assertEqual(before_response.status_code, 200, before_response.text)
        before = before_response.json()["periods"]

        async with async_session() as db:
            instructor = Instructor(
                name="Источник статистики по периодам", transmission="automatic", gender="any", is_active=True,
                working_hours_start=time(9, 0), working_hours_end=time(20, 0), days_off="",
            )
            client = Client(name="Клиент статистики по периодам", phone="+77000000190")
            db.add_all([instructor, client])
            await db.flush()
            common = {
                "client_id": client.id, "instructor_id": instructor.id,
                "service_type": "training", "transmission": "automatic",
                "location": "Циолковского 30", "booking_date": now_kz().date(),
                "start_time": time(10, 0), "end_time": time(11, 0),
                "status": "confirmed", "price": 10000,
            }
            db.add_all([
                Booking(source="telegram", **common), Booking(source="mobile", **common),
                Booking(source="manual", **common), Booking(source="admin_offline", **common),
                Booking(source="telegram", created_at=now_kz() - timedelta(days=2), **common),
            ])
            await db.commit()

        response = await self.client.get("/api/admin/analytics/booking-sources/extended")
        self.assertEqual(response.status_code, 200, response.text)
        periods = response.json()["periods"]
        self.assertEqual(periods["all"]["telegram"]["count"], before["all"]["telegram"]["count"] + 2)
        self.assertEqual(periods["all"]["mobile"]["count"], before["all"]["mobile"]["count"] + 1)
        self.assertEqual(periods["all"]["manual"]["count"], before["all"]["manual"]["count"] + 2)
        self.assertEqual(periods["all"]["total"], before["all"]["total"] + 5)
        self.assertEqual(periods["today"]["telegram"]["count"], before["today"]["telegram"]["count"] + 1)
        self.assertEqual(periods["today"]["mobile"]["count"], before["today"]["mobile"]["count"] + 1)
        self.assertEqual(periods["today"]["manual"]["count"], before["today"]["manual"]["count"] + 2)
        self.assertEqual(periods["today"]["total"], before["today"]["total"] + 4)

    async def test_booking_source_analytics_reports_completed_lessons_per_client(self):
        async with async_session() as db:
            instructor = Instructor(
                name="Инструктор клиентской статистики", transmission="automatic", gender="any", is_active=True,
                working_hours_start=time(9, 0), working_hours_end=time(20, 0), days_off="",
            )
            clients = [
                Client(name="Клиент с двумя занятиями", phone="+77000000191"),
                Client(name="Клиент с тремя занятиями", phone="+77000000192"),
                Client(name="Клиент без завершённых занятий", phone="+77000000193"),
            ]
            db.add_all([instructor, *clients])
            await db.flush()
            common = {
                "instructor_id": instructor.id, "service_type": "training", "transmission": "automatic",
                "location": "Циолковского 30", "booking_date": now_kz().date(),
                "start_time": time(11, 0), "end_time": time(12, 0), "price": 10000, "source": "manual",
            }
            db.add_all([
                *[Booking(client_id=clients[0].id, status="completed", **common) for _ in range(2)],
                *[Booking(client_id=clients[1].id, status="completed", **common) for _ in range(3)],
                Booking(client_id=clients[2].id, status="confirmed", **common),
            ])
            await db.commit()

        response = await self.client.get("/api/admin/analytics/booking-sources/extended")
        self.assertEqual(response.status_code, 200, response.text)
        metrics = response.json()["client_lessons"]
        async with async_session() as db:
            lesson_rows = (await db.execute(
                select(Booking.client_id, func.count(Booking.id))
                .where(Booking.status == "completed")
                .group_by(Booking.client_id)
            )).all()
        expected_counts = [int(count) for _, count in lesson_rows]
        self.assertEqual(metrics["completed_lessons"], sum(expected_counts))
        self.assertEqual(metrics["clients_counted"], len(expected_counts))
        self.assertEqual(metrics["average_per_client"], round(sum(expected_counts) / len(expected_counts), 1))
        self.assertEqual(metrics["maximum_per_client"], max(expected_counts))

    async def test_revenue_analytics_uses_period_scales_and_non_cumulative_values(self):
        current = datetime(2034, 5, 17, 20, 45)
        rows = [
            (date(2034, 5, 17), time(1, 15), 1000),
            (date(2034, 5, 17), time(4, 30), 2000),
            (date(2034, 5, 16), time(5, 0), 3000),
            (date(2034, 5, 7), time(10, 0), 4000),
            (date(2034, 5, 17), time(22, 0), 9000),  # Ещё не состоялась.
        ]

        data = _build_revenue_analytics(rows, current)

        self.assertEqual(data["refresh_interval_hours"], 3)
        self.assertEqual(data["periods"]["day"]["total_revenue"], 3000)
        self.assertEqual(data["periods"]["week"]["total_revenue"], 6000)
        self.assertEqual(data["periods"]["month"]["total_revenue"], 10000)
        self.assertEqual(data["periods"]["all"]["total_revenue"], 10000)
        day_points = data["periods"]["day"]["points"]
        self.assertEqual(len(day_points), 21)
        self.assertEqual(
            datetime.fromisoformat(day_points[1]["timestamp"]) - datetime.fromisoformat(day_points[0]["timestamp"]),
            timedelta(hours=1),
        )
        self.assertEqual([day_points[1]["revenue"], day_points[4]["revenue"], day_points[5]["revenue"]], [1000, 2000, 0])
        self.assertGreater(day_points[4]["revenue"], day_points[5]["revenue"])
        self.assertEqual(data["periods"]["day"]["granularity"], "hour")
        self.assertEqual(data["periods"]["week"]["granularity"], "day")
        self.assertEqual(data["periods"]["month"]["granularity"], "week")
        self.assertEqual(data["periods"]["all"]["granularity"], "day")
        self.assertEqual(data["profitable_hours"][0]["label"], "10:00–11:00")
        self.assertEqual(data["profitable_hours"][0]["revenue"], 4000)

        response = await self.client.get("/api/admin/analytics/revenue")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["refresh_interval_hours"], 3)
        self.assertEqual(set(response.json()["periods"]), {"day", "week", "month", "all"})

    async def test_archive_and_offline_snapshot_keep_only_intended_bookings(self):
        now = now_kz()
        today = now.date()
        async with async_session() as db:
            instructor = Instructor(
                name="Инструктор архива", transmission="automatic", gender="any", is_active=True,
                working_hours_start=time(9, 0), working_hours_end=time(20, 0), days_off="",
            )
            client = Client(name="Клиент архива", phone="+77000000053")
            mobile_user = MobileUser(
                name="Мобильный клиент архива", phone="+77000000054", password_hash="test-hash",
            )
            db.add_all([instructor, client, mobile_user])
            await db.flush()
            common = {
                "client_id": client.id, "instructor_id": instructor.id,
                "service_type": "training", "transmission": "automatic",
                "location": "Циолковского 30", "start_time": time(10, 0),
                "end_time": time(11, 0), "price": 10000,
            }
            created_long_ago = Booking(
                booking_date=today, status="completed",
                created_at=now - timedelta(days=8), completed_at=now, **common,
            )
            archived_old_lesson = Booking(
                booking_date=today - timedelta(days=8), status="completed",
                created_at=now - timedelta(days=6), **common,
            )
            recent_completed = Booking(
                booking_date=today - timedelta(days=7), status="completed",
                created_at=now - timedelta(days=6), completed_at=now, **common,
            )
            recent_completed_earlier = Booking(
                booking_date=today - timedelta(days=7), status="completed",
                start_time=time(9, 0), end_time=time(10, 0),
                created_at=now - timedelta(days=6), completed_at=now - timedelta(minutes=1),
                **{key: value for key, value in common.items() if key not in {"start_time", "end_time"}},
            )
            cancelled = Booking(
                booking_date=today - timedelta(days=9), status="cancelled",
                created_at=now - timedelta(days=9), **common,
            )
            no_show = Booking(
                booking_date=today - timedelta(days=9), status="no_show",
                created_at=now - timedelta(days=8), **common,
            )
            active = Booking(booking_date=today + timedelta(days=1), status="confirmed", **common)
            mobile_completed = MobileBooking(
                user_id=mobile_user.id, instructor_id=instructor.id, booking_date=today - timedelta(days=8),
                start_time=time(12, 0), end_time=time(13, 0), service_type="training",
                transmission="automatic", location="Циолковского 30", status="completed", price=10000,
            )
            mobile_cancelled = MobileBooking(
                user_id=mobile_user.id, instructor_id=instructor.id, booking_date=today - timedelta(days=8),
                start_time=time(14, 0), end_time=time(15, 0), service_type="training",
                transmission="automatic", location="Циолковского 30", status="cancelled", price=10000,
            )
            mobile_active = MobileBooking(
                user_id=mobile_user.id, instructor_id=instructor.id, booking_date=today + timedelta(days=1),
                start_time=time(16, 0), end_time=time(17, 0), service_type="training",
                transmission="automatic", location="Циолковского 30", status="confirmed", price=10000,
            )
            db.add_all([
                created_long_ago, archived_old_lesson, recent_completed, recent_completed_earlier,
                cancelled, no_show, active,
                mobile_completed, mobile_cancelled, mobile_active,
                SupportMessage(client_id=client.id, channel="client", sender="user", text="Не класть в снимок"),
            ])
            await db.commit()
            ids = {
                "created_long_ago": created_long_ago.id, "recent_completed": recent_completed.id,
                "recent_completed_earlier": recent_completed_earlier.id,
                "archived_old_lesson": archived_old_lesson.id,
                "cancelled": cancelled.id, "no_show": no_show.id, "active": active.id,
                "mobile_completed": mobile_completed.id, "mobile_cancelled": mobile_cancelled.id,
                "mobile_active": mobile_active.id,
            }

        archive_response = await self.client.get("/api/admin/bookings/archive")
        self.assertEqual(archive_response.status_code, 200, archive_response.text)
        archive_ids = {item["id"] for item in archive_response.json()}
        self.assertIn(ids["archived_old_lesson"], archive_ids)
        self.assertIn(ids["no_show"], archive_ids)
        self.assertNotIn(ids["created_long_ago"], archive_ids)
        self.assertNotIn(ids["recent_completed"], archive_ids)
        self.assertNotIn(ids["cancelled"], archive_ids)

        repeated_archive_response = await self.client.get("/api/admin/bookings/archive")
        self.assertEqual(repeated_archive_response.status_code, 200, repeated_archive_response.text)
        self.assertEqual(
            [item["id"] for item in archive_response.json()],
            [item["id"] for item in repeated_archive_response.json()],
        )
        async with async_session() as db:
            self.assertIsNotNone((await db.get(Booking, ids["archived_old_lesson"])).archived_at)
            self.assertIsNotNone((await db.get(Booking, ids["no_show"])).archived_at)
            self.assertIsNone((await db.get(Booking, ids["created_long_ago"])).archived_at)

        completed_response = await self.client.get(
            "/api/admin/bookings?status=completed,no_show&page=1&page_size=100"
        )
        self.assertEqual(completed_response.status_code, 200, completed_response.text)
        completed_tab_ids = {item["id"] for item in completed_response.json()["items"]}
        self.assertNotIn(ids["archived_old_lesson"], completed_tab_ids)
        self.assertNotIn(ids["no_show"], completed_tab_ids)
        self.assertIn(ids["created_long_ago"], completed_tab_ids)
        self.assertIn(ids["recent_completed"], completed_tab_ids)
        self.assertIn(ids["recent_completed_earlier"], completed_tab_ids)

        ordered_response = await self.client.get(
            f"/api/admin/bookings?status=completed&date_from={today - timedelta(days=7)}"
            f"&date_to={today - timedelta(days=7)}&page=1&page_size=100"
        )
        ordered_ids = [item["id"] for item in ordered_response.json()["items"]]
        self.assertLess(
            ordered_ids.index(ids["recent_completed"]),
            ordered_ids.index(ids["recent_completed_earlier"]),
        )

        snapshot_response = await self.client.get("/api/admin/offline-snapshot")
        self.assertEqual(snapshot_response.status_code, 200, snapshot_response.text)
        snapshot = snapshot_response.json()
        self.assertGreaterEqual(snapshot["version"], 10)
        self.assertNotIn("/support-messages", snapshot["data"])
        snapshot_booking_ids = {item["id"] for item in snapshot["data"]["/bookings"]}
        self.assertIn(ids["active"], snapshot_booking_ids)
        self.assertNotIn(ids["archived_old_lesson"], snapshot_booking_ids)
        self.assertNotIn(ids["created_long_ago"], snapshot_booking_ids)
        self.assertNotIn(ids["recent_completed"], snapshot_booking_ids)
        self.assertNotIn(ids["cancelled"], snapshot_booking_ids)
        self.assertNotIn(ids["no_show"], snapshot_booking_ids)
        snapshot_mobile_ids = {item["id"] for item in snapshot["data"]["/offline-mobile-bookings"]}
        self.assertIn(f"mobile-{ids['mobile_active']}", snapshot_mobile_ids)
        self.assertNotIn(f"mobile-{ids['mobile_completed']}", snapshot_mobile_ids)
        self.assertNotIn(f"mobile-{ids['mobile_cancelled']}", snapshot_mobile_ids)

    async def test_visible_cancelled_pagination_excludes_hidden_rejections_before_counting(self):
        async with async_session() as db:
            instructor = Instructor(
                name="Инструктор пагинации отмен", transmission="automatic", gender="any",
                is_active=True, working_hours_start=time(9), working_hours_end=time(20), days_off="",
            )
            client = Client(name="Клиент пагинации отмен", phone="+77000000069")
            db.add_all([instructor, client])
            await db.flush()
            common = {
                "client_id": client.id, "instructor_id": instructor.id,
                "service_type": "training", "transmission": "automatic",
                "location": "Площадка пагинации отмен", "booking_date": now_kz().date(),
                "start_time": time(10), "end_time": time(11), "price": 10000,
                "status": "cancelled",
            }
            hidden = [Booking(admin_confirmed=False, **common) for _ in range(16)]
            visible = [Booking(admin_confirmed=True, **common) for _ in range(16)]
            db.add_all(hidden + visible)
            await db.commit()
            hidden_ids = {booking.id for booking in hidden}
            visible_ids = {booking.id for booking in visible}

        first = await self.client.get(
            "/api/admin/bookings?status=cancelled&location=Площадка%20пагинации%20отмен&page=1&page_size=15"
        )
        second = await self.client.get(
            "/api/admin/bookings?status=cancelled&location=Площадка%20пагинации%20отмен&page=2&page_size=15"
        )
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        first_data, second_data = first.json(), second.json()
        returned_ids = {item["id"] for item in first_data["items"] + second_data["items"]}
        self.assertEqual(first_data["pagination"]["total"], 16)
        self.assertEqual(first_data["pagination"]["total_pages"], 2)
        self.assertEqual(len(first_data["items"]), 15)
        self.assertEqual(len(second_data["items"]), 1)
        self.assertEqual(returned_ids, visible_ids)
        self.assertTrue(returned_ids.isdisjoint(hidden_ids))

    async def test_purge_cancelled_bookings_removes_only_visible_cancelled_rows(self):
        async with async_session() as db:
            instructor = Instructor(
                name="Инструктор очистки отменённых", transmission="automatic", gender="any", is_active=True,
                working_hours_start=time(9, 0), working_hours_end=time(20, 0), days_off="",
            )
            client = Client(name="Клиент очистки отменённых", phone="+77000000056")
            db.add_all([instructor, client])
            await db.flush()
            common = {
                "client_id": client.id, "instructor_id": instructor.id,
                "service_type": "training", "transmission": "automatic",
                "location": "Циолковского 30", "booking_date": now_kz().date(),
                "start_time": time(10, 0), "end_time": time(11, 0), "price": 10000,
            }
            cancelled_first = Booking(status="cancelled", admin_confirmed=True, **common)
            cancelled_second = Booking(status="cancelled", admin_confirmed=True, **common)
            hidden_cancelled = Booking(status="cancelled", admin_confirmed=False, **common)
            completed = Booking(status="completed", **common)
            db.add_all([cancelled_first, cancelled_second, hidden_cancelled, completed])
            await db.flush()
            cancelled_rating = RatingRecord(
                booking_id=cancelled_first.id, instructor_id=instructor.id, vote="good",
            )
            completed_rating = RatingRecord(
                booking_id=completed.id, instructor_id=instructor.id, vote="good",
            )
            db.add_all([cancelled_rating, completed_rating])
            await db.commit()
            ids = {
                "cancelled_first": cancelled_first.id,
                "cancelled_second": cancelled_second.id,
                "hidden_cancelled": hidden_cancelled.id,
                "completed": completed.id,
                "cancelled_rating": cancelled_rating.id,
                "completed_rating": completed_rating.id,
            }

        response = await self.client.delete("/api/admin/bookings/cancelled")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"ok": True, "deleted": 2})
        async with async_session() as db:
            self.assertIsNone(await db.get(Booking, ids["cancelled_first"]))
            self.assertIsNone(await db.get(Booking, ids["cancelled_second"]))
            self.assertIsNotNone(await db.get(Booking, ids["hidden_cancelled"]))
            self.assertIsNotNone(await db.get(Booking, ids["completed"]))
            self.assertIsNone(await db.get(RatingRecord, ids["cancelled_rating"]))
            self.assertIsNotNone(await db.get(RatingRecord, ids["completed_rating"]))

        repeated = await self.client.delete("/api/admin/bookings/cancelled")
        self.assertEqual(repeated.status_code, 200, repeated.text)
        self.assertEqual(repeated.json(), {"ok": True, "deleted": 0})

        individual = await self.client.delete(f"/api/admin/bookings/{ids['hidden_cancelled']}")
        self.assertEqual(individual.status_code, 200, individual.text)
        async with async_session() as db:
            self.assertIsNone(await db.get(Booking, ids["hidden_cancelled"]))

    async def test_vehicle_repair_status_blocks_admin_capacity_and_can_be_restored(self):
        slot_date = date(2041, 1, 10)
        async with async_session() as db:
            vehicle = (await db.execute(
                select(Vehicle).where(Vehicle.name == "Машина 1")
            )).scalar_one()
            vehicle_id = vehicle.id

        repair_response = await self.client.put(
            f"/api/admin/vehicles/{vehicle_id}/repair", json={"is_under_repair": True}
        )
        self.assertEqual(repair_response.status_code, 200, repair_response.text)
        self.assertTrue(repair_response.json()["is_under_repair"])
        fleet_response = await self.client.get("/api/admin/vehicles")
        self.assertEqual(fleet_response.status_code, 200, fleet_response.text)
        self.assertTrue(next(item for item in fleet_response.json() if item["id"] == vehicle_id)["is_under_repair"])
        snapshot_response = await self.client.get("/api/admin/offline-snapshot")
        self.assertEqual(snapshot_response.status_code, 200, snapshot_response.text)
        snapshot_vehicle = next(item for item in snapshot_response.json()["data"]["/vehicles"] if item["id"] == vehicle_id)
        self.assertTrue(snapshot_vehicle["is_under_repair"])
        async with async_session() as db:
            self.assertFalse(await has_available_vehicle(
                db, slot_date, time(10, 0), time(11, 0), "manual"
            ))

        missing_vehicle = await self.client.put(
            "/api/admin/vehicles/999999/repair", json={"is_under_repair": True}
        )
        self.assertEqual(missing_vehicle.status_code, 404, missing_vehicle.text)

        return_response = await self.client.put(
            f"/api/admin/vehicles/{vehicle_id}/repair", json={"is_under_repair": False}
        )
        self.assertEqual(return_response.status_code, 200, return_response.text)
        self.assertFalse(return_response.json()["is_under_repair"])
        async with async_session() as db:
            self.assertTrue(await has_available_vehicle(
                db, slot_date, time(10, 0), time(11, 0), "manual"
            ))

    async def test_marking_booking_completed_records_exact_completion_time(self):
        async with async_session() as db:
            instructor = Instructor(
                name="Инструктор времени завершения", transmission="automatic", gender="any", is_active=True,
                working_hours_start=time(9, 0), working_hours_end=time(20, 0), days_off="",
            )
            client = Client(name="Клиент времени завершения", phone="+77000000055")
            db.add_all([instructor, client])
            await db.flush()
            booking = Booking(
                client_id=client.id, instructor_id=instructor.id,
                service_type="training", transmission="automatic", location="Циолковского 30",
                booking_date=now_kz().date(), start_time=time(10, 0), end_time=time(11, 0),
                status="confirmed", price=10000,
            )
            db.add(booking)
            await db.commit()
            booking_id = booking.id

        before = now_kz()
        completed = await self.client.put(
            f"/api/admin/bookings/{booking_id}/status", json={"status": "completed"}
        )
        after = now_kz()
        self.assertEqual(completed.status_code, 200, completed.text)
        async with async_session() as db:
            booking = await db.get(Booking, booking_id)
            self.assertIsNotNone(booking.completed_at)
            self.assertGreaterEqual(booking.completed_at, before - timedelta(seconds=1))
            self.assertLessEqual(booking.completed_at, after + timedelta(seconds=1))

        restored = await self.client.put(
            f"/api/admin/bookings/{booking_id}/status", json={"status": "confirmed"}
        )
        self.assertEqual(restored.status_code, 200, restored.text)
        async with async_session() as db:
            self.assertIsNone((await db.get(Booking, booking_id)).completed_at)

    async def test_gender_analytics_returns_cached_percentages(self):
        async with async_session() as db:
            cached = await db.get(GenderAnalytics, 1)
            if cached is None:
                cached = GenderAnalytics(id=1)
                db.add(cached)
            cached.male_count = 3
            cached.female_count = 1
            cached.unknown_count = 1
            cached.total_count = 5
            cached.updated_at = now_kz()
            await db.commit()

        response = await self.client.get("/api/admin/analytics/gender")
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["status"], "ready")
        self.assertEqual(data["male"], {"count": 3, "percent": 75.0})
        self.assertEqual(data["female"], {"count": 1, "percent": 25.0})
        self.assertEqual(data["unknown"], {"count": 1, "percent": 20.0})

        snapshot = await self.client.get("/api/admin/offline-snapshot")
        self.assertEqual(snapshot.status_code, 200, snapshot.text)
        snapshot_data = snapshot.json()
        self.assertGreaterEqual(snapshot_data["version"], 10)
        self.assertIn("/analytics/booking-sources", snapshot_data["data"])
        self.assertIn("/analytics/gender", snapshot_data["data"])

    async def test_gender_refresh_reads_active_clients_and_keeps_all_rows_unchanged(self):
        async with async_session() as db:
            db.add_all([
                Client(name="Иван Проверочный", phone="+77000000031"),
                Client(name="Мария Проверочная", phone="+77000000032"),
            ])
            await db.commit()
            before = (await db.execute(select(Client.id, Client.name).order_by(Client.id))).all()
            active_before = (await db.execute(
                select(Client.id, Client.name)
                .where(Client.is_deleted == False)
                .order_by(Client.id)
            )).all()

        with patch.object(settings, "NVIDIA_API_KEY", ""), patch.object(
            settings, "GROQ_API_KEY", "test-key"
        ), patch(
            "app.services.gender_analytics.httpx.AsyncClient", FakeGroqClient
        ):
            self.assertTrue(await refresh_gender_analytics(force=True))

        async with async_session() as db:
            cached = await db.get(GenderAnalytics, 1)
            after = (await db.execute(select(Client.id, Client.name).order_by(Client.id))).all()
            self.assertEqual(before, after)
            self.assertEqual(cached.total_count, len(active_before))
            self.assertGreaterEqual(cached.male_count, 1)
            self.assertGreaterEqual(cached.female_count, 1)
            self.assertEqual(
                cached.male_count + cached.female_count + cached.unknown_count,
                cached.total_count,
            )

    async def test_nvidia_variables_take_priority_for_gender_analytics(self):
        with patch.object(settings, "GROQ_API_KEY", "groq-key"), patch.object(
            settings, "NVIDIA_API_KEY", "nvidia-key"
        ), patch.object(settings, "NVIDIA_BASE_URL", "https://nvidia.example/v1"), patch.object(
            settings, "NVIDIA_MODEL", "openai/gpt-oss-20b"
        ), patch("app.services.gender_analytics.httpx.AsyncClient", FakeGroqClient):
            self.assertTrue(await refresh_gender_analytics(force=True))

        self.assertEqual(FakeGroqClient.last_url, "https://nvidia.example/v1/chat/completions")
        self.assertNotIn("response_format", FakeGroqClient.last_payload)
        async with async_session() as db:
            cached = await db.get(GenderAnalytics, 1)
            self.assertEqual(cached.model, "NVIDIA: openai/gpt-oss-20b")


if __name__ == "__main__":
    unittest.main()
