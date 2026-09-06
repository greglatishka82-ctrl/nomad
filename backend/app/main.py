import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.config import settings
from app.database import engine, init_db, verify_database_ready
from app.routers import chat, landing, mobile_auth, mobile_bookings, mobile_support, mobile_extras, mobile_profile
from app.bot.handlers import router as bot_router, send_confirmation_reminders, send_rating_requests, check_unconfirmed_bookings, send_lesson_reminders
from app.bot.instructor_handlers import router as instructor_router, run_automatic_lesson_transitions
from app.bot.report_handlers import router as report_router
from app.services.auth import hash_password

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def _seed_admin():
    from sqlalchemy import select, update
    from sqlalchemy.sql import func
    from app.database import async_session
    from app.models.models import Admin, FAQItem

    FAQ_DATA = [
        ("Сколько стоит одно занятие по вождению?", "Стоимость одного занятия составляет 6 000 тенге. Также доступны пакеты занятий — чем больше занятий в пакете, тем выгоднее цена за каждое.", 1),
        ("Сколько длится одно занятие?", "Одно занятие по вождению длится 1 час. Пробный экзамен — 20 минут.", 2),
        ("Где проходят занятия?", "Занятия по вождению и пробный экзамен проходят по адресу Циолковского 30.", 3),
        ("Как записаться на занятие?", "Записаться можно через наш Telegram-бот https://t.me/nomadrive_bot или через мобильное приложение. Выберите тип занятия, коробку передач, удобную дату и время — инструктор назначается автоматически.", 4),
        ("Можно ли выбрать конкретного инструктора?", "На данный момент инструктор назначается системой автоматически — выбирается свободный инструктор с наивысшим рейтингом. Вы можете указать предпочтение по полу инструктора.", 5),
        ("Как отменить или перенести запись?", "Отменить или перенести запись можно через Telegram-бот в разделе «Мои записи» или в мобильном приложении.", 6),
        ("Что такое пробный экзамен?", "Пробный экзамен — это репетиция реального экзамена на автодроме. Вы едете по экзаменационному маршруту, инструктор фиксирует ошибки. Помогает понять слабые места перед официальным экзаменом.", 7),
        ("На каком автомобиле проходят занятия?", "Занятия проводятся как на механике так и на автомате — в зависимости от выбора при записи.", 8),
        ("Сколько занятий нужно чтобы сдать экзамен?", "В среднем ученики готовы к экзамену после 10–15 занятий. Наши инструкторы дадут рекомендацию по готовности лично.", 9),
        ("Есть ли скидки или акции?", "Да! Доступны пакеты занятий со скидкой, подарочные сертификаты, а также скидка 1000 ₸ на первое занятие по реферальному коду друга.", 10),
        ("Как работает реферальная программа?", "Попросите у друга его реферальный код и введите его при регистрации в приложении. Вы получите скидку 1000 ₸ на своё первое занятие.", 11),
        ("Что такое подарочный сертификат?", "Подарочный сертификат — это код с номиналом в тенге. Активируется в боте или приложении. Сумма автоматически вычитается из стоимости занятия.", 12),
        ("Как связаться с автошколой?", "Позвоните нам: +77027182233. Также отвечаем через Telegram-бот https://t.me/nomadrive_bot и в разделе «Поддержка» в мобильном приложении.", 13),
        ("В какое время работает автошкола?", "Занятия проводятся с 9:00 до 20:00. Последнее занятие начинается в 19:00. Актуальные слоты видны при записи.", 14),
        ("Можно ли записаться на несколько занятий подряд?", "Да, можно записаться максимум на 2 занятия подряд в один день.", 15),
    ]

    async with async_session() as db:
        result = await db.execute(select(Admin).where(Admin.username == settings.ADMIN_USERNAME))
        if not result.scalar_one_or_none():
            db.add(Admin(
                username=settings.ADMIN_USERNAME,
                password_hash=hash_password(settings.ADMIN_PASSWORD),
            ))
            await db.commit()
            logger.info(f"Default admin '{settings.ADMIN_USERNAME}' created")

        faq_result = await db.execute(select(FAQItem))
        existing_faqs = faq_result.scalars().all()
        
        if not existing_faqs:
            # Создаем FAQ если их нет
            for question, answer, sort_order in FAQ_DATA:
                db.add(FAQItem(question=question, answer=answer, sort_order=sort_order, is_active=True))
            await db.commit()
            logger.info(f"FAQ наполнен {len(FAQ_DATA)} вопросами")
        else:
            # Обновляем FAQ если нашли старое имя бота
            await db.execute(
                update(FAQItem)
                .where(FAQItem.answer.like('%@drivenomad_bot%'))
                .values(answer=func.replace(FAQItem.answer, '@drivenomad_bot', 'https://t.me/nomadrive_bot'))
            )
            await db.execute(
                update(FAQItem)
                .where(FAQItem.answer.like('%@nomadrive_bot%'))
                .values(answer=func.replace(FAQItem.answer, '@nomadrive_bot', 'https://t.me/nomadrive_bot'))
            )
            await db.commit()
            logger.info("FAQ обновлены: заменено имя бота на полную ссылку https://t.me/nomadrive_bot")


async def _scheduler_loop(bot):
    while True:
        try:
            await run_automatic_lesson_transitions()
            await send_lesson_reminders(bot)
            await send_confirmation_reminders(bot)
            await send_rating_requests(bot)
            await check_unconfirmed_bookings(bot)
        except Exception as e:
            logger.error(f"Scheduler error: {e}")
        await asyncio.sleep(60)


_BOT_POLLING_LOCK_ID = 2026082201


async def _wait_for_bot_leader_lock(stop_event: asyncio.Event):
    """Hold one PostgreSQL advisory lock for all Telegram polling tasks.

    Render may briefly run an old and a new web instance at the same time
    during a restart. Telegram permits only one getUpdates consumer per bot
    token, so the new instance waits instead of causing 409 conflicts.
    """
    if engine.dialect.name != "postgresql":
        # Local SQLite development has one process and does not support
        # PostgreSQL advisory locks.
        return None, True

    while not stop_event.is_set():
        connection = await engine.connect()
        acquired = await connection.scalar(
            text("SELECT pg_try_advisory_lock(:lock_id)"),
            {"lock_id": _BOT_POLLING_LOCK_ID},
        )
        if acquired:
            logger.info("Acquired Telegram polling leader lock")
            return connection, True

        await connection.close()
        logger.info("Another instance owns Telegram polling; waiting for it to stop")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=3)
        except asyncio.TimeoutError:
            pass

    return None, False


async def _release_bot_leader_lock(connection) -> None:
    if connection is None:
        return
    try:
        await connection.execute(
            text("SELECT pg_advisory_unlock(:lock_id)"),
            {"lock_id": _BOT_POLLING_LOCK_ID},
        )
    finally:
        await connection.close()


async def _run_bot_workers(stop_event: asyncio.Event) -> None:
    """Run bot polling only while this service instance owns the DB lock."""
    lock_connection, is_leader = await _wait_for_bot_leader_lock(stop_event)
    if not is_leader:
        return

    bot = None
    instructor_bot = None
    report_bot = None
    tasks: list[asyncio.Task] = []
    try:
        if not settings.TELEGRAM_BOT_TOKEN:
            logger.error("Client Telegram bot is disabled: TELEGRAM_BOT_TOKEN is not set")
            return

        from aiogram import Bot, Dispatcher
        import app.bot.handlers as client_handlers

        bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
        dp = Dispatcher()
        dp.include_router(bot_router)

        if settings.INSTRUCTOR_BOT_TOKEN:
            instructor_bot = Bot(token=settings.INSTRUCTOR_BOT_TOKEN)
            instructor_dp = Dispatcher()
            instructor_dp.include_router(instructor_router)
            tasks.append(asyncio.create_task(instructor_dp.start_polling(instructor_bot)))
            logger.info("Instructor bot started")
        else:
            logger.warning("INSTRUCTOR_BOT_TOKEN not set, instructor bot disabled")

        if settings.REPORT_BOT_TOKEN:
            report_bot = Bot(token=settings.REPORT_BOT_TOKEN)
            report_dp = Dispatcher()
            report_dp.include_router(report_router)
            tasks.append(asyncio.create_task(report_dp.start_polling(report_bot)))
            logger.info("Report bot started")
        else:
            logger.warning("REPORT_BOT_TOKEN not set, report bot disabled")

        # Notifications use the correct bot after the leader has started it.
        client_handlers.instructor_bot = instructor_bot
        import app.bot.instructor_handlers as instructor_handlers
        instructor_handlers.client_bot = bot
        instructor_handlers.instructor_bot = instructor_bot

        tasks.append(asyncio.create_task(_scheduler_loop(bot)))
        tasks.append(asyncio.create_task(dp.start_polling(bot)))
        logger.info("Telegram bot workers started")

        await stop_event.wait()
    finally:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if bot:
            await bot.session.close()
        if instructor_bot:
            await instructor_bot.session.close()
        if report_bot:
            await report_bot.session.close()
        await _release_bot_leader_lock(lock_connection)
        logger.info("Telegram bot workers stopped")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting lifespan...")
    
    # Проверяем критичные переменные окружения
    if not settings.TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set!")
    if not settings.INSTRUCTOR_BOT_TOKEN:
        logger.error("INSTRUCTOR_BOT_TOKEN not set!")
    if not settings.REPORT_BOT_TOKEN:
        logger.warning("REPORT_BOT_TOKEN not set, report bot disabled")
    if not settings.GROQ_API_KEY and not settings.NVIDIA_API_KEY:
        logger.warning("No AI API keys configured - chat will not work")
    
    await init_db()
    logger.info("Database initialized")
    
    await _seed_admin()
    logger.info("Admin seeded")

    stop_bot_workers = asyncio.Event()
    bot_workers_task = asyncio.create_task(_run_bot_workers(stop_bot_workers))
    try:
        yield
    finally:
        stop_bot_workers.set()
        await bot_workers_task


from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="NOMAD Site API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(landing.router)
app.include_router(chat.router)
app.include_router(mobile_auth.router)
app.include_router(mobile_bookings.router)
app.include_router(mobile_support.router)
app.include_router(mobile_extras.router)
app.include_router(mobile_profile.router)

# Раздача статических файлов (аватарки)
from pathlib import Path
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/")
async def root():
    return {"message": "NOMAD Site API", "docs": "/docs"}


@app.get("/health")
async def health():
    try:
        await verify_database_ready()
    except Exception as exc:
        logger.error("Health check failed: %s", exc)
        raise HTTPException(status_code=503, detail="Database is not ready")
    return {"status": "ok"}
