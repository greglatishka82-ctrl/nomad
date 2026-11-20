import asyncio
import logging
from datetime import timedelta, datetime
from fastapi import HTTPException, Request
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from app.database import async_session
from app.models.models import Booking, Event
from app.routers import admin

logger = logging.getLogger(__name__)
WAIT_SECONDS = 30
EVENT_FOR_STATUS = {
    'pending': 'new_booking',
    'reschedule_pending': 'booking_reschedule_requested',
    'cancellation_pending': 'booking_cancellation_requested',
}
AUTO_USER = 'Автоматическое подтверждение'

def _request():
    return Request({'type':'http','method':'POST','path':'/internal/auto-confirm',
                    'headers':[], 'query_string':b'',
                    'session':{'admin_username':AUTO_USER}})

async def _mark_failed(db, event_id, booking_id, expected_status, reason):
    await db.rollback()
    current = (await db.execute(select(Booking).where(Booking.id == booking_id).with_for_update())).scalar_one_or_none()
    if not current or current.status != expected_status:
        return
    latest_id = (await db.execute(select(Event.id).where(Event.booking_id == booking_id,
        Event.event_type == EVENT_FOR_STATUS[expected_status]).order_by(Event.id.desc()).limit(1))).scalar_one_or_none()
    if latest_id != event_id:
        return
    inserted = (await db.execute(text("""
      INSERT INTO auto_booking_failures(event_id, booking_id, reason)
      VALUES (:event_id, :booking_id, :reason)
      ON CONFLICT (event_id) DO NOTHING RETURNING event_id
    """), {'event_id':event_id, 'booking_id':booking_id, 'reason':str(reason)[:500]})).scalar_one_or_none()
    client_id = current.client_id
    if inserted:
        db.add(Event(event_type='auto_booking_needs_admin', source='system',
                     client_id=client_id, instructor_id=current.instructor_id,
                     booking_id=booking_id,
                     message='Не удалось автоматически обработать заявку. Требуется проверка администратора: '+str(reason)[:350]))
    await db.commit()
    if inserted and client_id:
        client = await db.get(admin.Client, client_id)
        if client and client.telegram_id and admin.settings.BOT_TOKEN:
            try:
                async with admin.httpx.AsyncClient(timeout=10) as c:
                    await c.post(f"https://api.telegram.org/bot{admin.settings.BOT_TOKEN}/sendMessage",
                        json={"chat_id":client.telegram_id.strip(),"text":"Заявка требует дополнительной проверки. Администратор рассмотрит её и сообщит вам результат."})
            except Exception:
                logger.exception('Could not notify client of review, booking_id=%s', booking_id)

async def _handle_booking(booking_id):
    async with async_session() as db:
        booking = (await db.execute(
            select(Booking).where(Booking.id == booking_id).with_for_update(skip_locked=True)
        )).scalar_one_or_none()
        if not booking or booking.status not in EVENT_FOR_STATUS:
            return
        status = booking.status
        if status == 'pending' and booking.source != 'telegram':
            return
        event = (await db.execute(
            select(Event).where(Event.booking_id == booking.id,
                                Event.event_type == EVENT_FOR_STATUS[status])
            .order_by(Event.id.desc()).limit(1)
        )).scalar_one_or_none()
        enabled_at = (await db.execute(text("SELECT enabled_at FROM auto_booking_settings WHERE id=1"))).scalar_one_or_none()
        if (not event or event.source != 'telegram' or not event.created_at or not enabled_at
                or event.created_at < enabled_at
                or event.created_at > admin.now_kz() - timedelta(seconds=WAIT_SECONDS)):
            return
        already_failed = (await db.execute(text(
            "SELECT 1 FROM auto_booking_failures WHERE event_id=:id"
        ), {'id':event.id})).scalar_one_or_none()
        if already_failed:
            return
        event_id, current_booking_id = event.id, booking.id
        try:
            if status == 'pending':
                if (booking.completed_at is not None or not booking.instructor_id or
                    datetime.combine(booking.booking_date, booking.start_time) <= admin.now_kz()):
                    raise HTTPException(status_code=409, detail='Время прошло или инструктор не назначен')
                instructor = await db.get(admin.Instructor, booking.instructor_id)
                if not instructor or not await admin.is_instructor_available(
                    db, instructor, booking.booking_date, booking.start_time, booking.end_time,
                    booking.transmission, None, service_type=str(getattr(booking.service_type,'value',booking.service_type)),
                    preserve_existing_assignment=True):
                    raise HTTPException(status_code=409, detail='Инструктор недоступен на выбранное время')
                if not await admin.slot_has_capacity(
                    db, booking.booking_date, booking.start_time, booking.end_time,
                    booking.location, booking.transmission,
                    str(getattr(booking.service_type,'value',booking.service_type)),
                    exclude_booking_id=booking.id):
                    raise HTTPException(status_code=409, detail='Нет свободной машины на выбранное время')
                await admin.confirm_booking(_request(), booking.id, admin.ConfirmBookingRequest(action='confirm'), db)
            elif status == 'reschedule_pending':
                await admin.resolve_reschedule_request(_request(), booking.id, admin.RescheduleResolveRequest(action='confirm'), db)
            else:
                await admin.update_booking_status(_request(), booking.id, admin.UpdateBookingStatus(status='cancelled'), db)
            logger.info('Automatic booking processed: booking_id=%s request=%s', current_booking_id, status)
        except HTTPException as exc:
            if exc.status_code in (400, 404, 409, 422):
                await _mark_failed(db, event_id, current_booking_id, status, exc.detail)
            else:
                raise
        except IntegrityError:
            await _mark_failed(db, event_id, current_booking_id, status, "Выбранное время уже занято")

async def process_due():
    async with async_session() as db:
        ids = (await db.execute(text("""
          SELECT b.id FROM bookings b
          JOIN LATERAL (
            SELECT e.id, e.source, e.created_at FROM events e
            WHERE e.booking_id=b.id AND e.event_type =
              CASE b.status WHEN 'pending' THEN 'new_booking'
                WHEN 'reschedule_pending' THEN 'booking_reschedule_requested'
                WHEN 'cancellation_pending' THEN 'booking_cancellation_requested' END
            ORDER BY e.id DESC LIMIT 1
          ) e ON true
          JOIN auto_booking_settings s ON s.id=1
          WHERE b.status IN ('pending','reschedule_pending','cancellation_pending')
            AND (b.status <> 'pending' OR b.source='telegram')
            AND e.source='telegram' AND e.created_at>=s.enabled_at
            AND e.created_at <= (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty') - interval '30 seconds'
            AND NOT EXISTS (SELECT 1 FROM auto_booking_failures f WHERE f.event_id=e.id)
          ORDER BY e.created_at, e.id LIMIT 25
        """))).scalars().all()
    for booking_id in ids:
        try:
            await _handle_booking(booking_id)
        except Exception:
            logger.exception('Auto booking processing failed, booking_id=%s', booking_id)

async def ensure_auto_booking_schema():
    async with async_session() as db:
        await db.execute(text("CREATE TABLE IF NOT EXISTS auto_booking_settings (id integer PRIMARY KEY, enabled_at timestamp NOT NULL)"))
        await db.execute(text("INSERT INTO auto_booking_settings(id,enabled_at) VALUES(1,TIMESTAMP '9999-12-31') ON CONFLICT (id) DO NOTHING"))
        await db.execute(text("CREATE TABLE IF NOT EXISTS auto_booking_failures (event_id bigint PRIMARY KEY, booking_id bigint NOT NULL, reason text NOT NULL)"))
        await db.commit()

async def auto_booking_loop(stop_event):
    logger.info("Automatic booking review worker started")
    while not stop_event.is_set():
        try:
            await process_due()
        except Exception:
            logger.exception('Auto booking polling failed')
        try:
            await asyncio.wait_for(stop_event.wait(),timeout=1)
        except asyncio.TimeoutError:
            pass
