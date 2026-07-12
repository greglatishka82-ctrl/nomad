import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.database import init_db
from app.routers import chat, landing
from app.routers import mobile_auth, mobile_profile, mobile_bookings, mobile_extras, mobile_support
from app.routers import admin_support
# from app.bot.handlers import router as bot_router, send_confirmation_reminders, send_rating_requests, check_unconfirmed_bookings
from app.services.auth import hash_password

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"
ADMIN_UPLOADS_DIR = Path(__file__).resolve().parent.parent.parent / "admin" / "backend" / "uploads"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def _seed_admin():
    from sqlalchemy import select
    from app.database import async_session
    from app.models.models import Admin

    async with async_session() as db:
        result = await db.execute(select(Admin).where(Admin.username == settings.ADMIN_USERNAME))
        if not result.scalar_one_or_none():
            db.add(Admin(
                username=settings.ADMIN_USERNAME,
                password_hash=hash_password(settings.ADMIN_PASSWORD),
            ))
            await db.commit()
            logger.info(f"Default admin '{settings.ADMIN_USERNAME}' created")


async def _scheduler_loop():
    """
    Scheduler для периодических задач (отключён вместе с ботом)
    """
    pass
    # while True:
    #     try:
    #         await send_confirmation_reminders(bot)
    #         await send_rating_requests(bot)
    #         await check_unconfirmed_bookings(bot)
    #     except Exception as e:
    #         logger.error(f"Scheduler error: {e}")
    #     await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await _seed_admin()

    # Telegram бот отключён для тестирования мобильного приложения
    # Раскомментируй, если нужен бот
    # from aiogram import Bot, Dispatcher
    # bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
    # dp = Dispatcher()
    # dp.include_router(bot_router)

    # scheduler_task = asyncio.create_task(_scheduler_loop(bot))
    
    # await asyncio.sleep(2)
    
    # polling_task = asyncio.create_task(dp.start_polling(bot))

    logger.info("Backend started (Telegram bot disabled)")
    yield

    # polling_task.cancel()
    # try:
    #     await polling_task
    # except asyncio.CancelledError:
    #     pass
    
    # scheduler_task.cancel()
    # try:
    #     await scheduler_task
    # except asyncio.CancelledError:
    #     pass
    
    # await bot.session.close()
    # await asyncio.sleep(1)
    logger.info("Backend stopped")


app = FastAPI(title="NOMAD Site API", lifespan=lifespan)

# ── Middleware ─────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # уточнить до конкретных доменов на проде
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SessionMiddleware, secret_key=settings.SECRET_KEY)

# ── Роутеры ───────────────────────────────────────────────────────────────────
app.include_router(chat.router)
app.include_router(landing.router)

# Mobile API
app.include_router(mobile_auth.router)
app.include_router(mobile_profile.router)
app.include_router(mobile_bookings.router)
app.include_router(mobile_extras.router)
app.include_router(mobile_support.router)

# Admin support chat
app.include_router(admin_support.router)

# Frontend static files
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")

# Admin uploads (instructor avatars)
if ADMIN_UPLOADS_DIR.exists():
    app.mount("/uploads", StaticFiles(directory=str(ADMIN_UPLOADS_DIR)), name="uploads")


@app.get("/api")
async def root():
    return {"message": "NOMAD Site API", "docs": "/docs"}
