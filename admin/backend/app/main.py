import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.database import async_session, init_db, verify_database_ready
from app.routers import admin, support, public
from app.services.auth import hash_password
from app.services.gender_analytics import refresh_gender_analytics

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
KZ_TZ = ZoneInfo("Asia/Almaty")


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


async def _gender_analytics_loop(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        await refresh_gender_analytics()
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=3600)
        except asyncio.TimeoutError:
            pass


async def _booking_archive_loop(stop_event: asyncio.Event) -> None:
    """Persist due archive transitions even when nobody has the tab open."""
    while not stop_event.is_set():
        try:
            async with async_session() as db:
                archived_count = await admin.archive_due_completed_bookings(db)
            if archived_count:
                logger.info("Archived %s completed bookings", archived_count)
        except Exception:
            logger.exception("Automatic booking archive pass failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=3600)
        except asyncio.TimeoutError:
            pass


def _seconds_until_next_kz_midnight() -> float:
    now = datetime.now(KZ_TZ)
    next_midnight = datetime.combine(
        now.date() + timedelta(days=1), time.min, tzinfo=KZ_TZ,
    )
    return max(1.0, (next_midnight - now).total_seconds())


async def _log_archive_loop(stop_event: asyncio.Event) -> None:
    """Archive completed calendar days at midnight Kazakhstan time."""
    while not stop_event.is_set():
        try:
            async with async_session() as db:
                archived_count = await admin.archive_previous_day_logs(db)
            if archived_count:
                logger.info("Archived %s audit/event logs", archived_count)
        except Exception:
            logger.exception("Automatic log archive pass failed")
        try:
            await asyncio.wait_for(
                stop_event.wait(), timeout=_seconds_until_next_kz_midnight(),
            )
        except asyncio.TimeoutError:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await _seed_admin()
    stop_event = asyncio.Event()
    gender_task = asyncio.create_task(_gender_analytics_loop(stop_event))
    archive_task = asyncio.create_task(_booking_archive_loop(stop_event))
    log_archive_task = asyncio.create_task(_log_archive_loop(stop_event))
    logger.info("Admin backend started")
    try:
        yield
    finally:
        stop_event.set()
        await asyncio.gather(
            gender_task, archive_task, log_archive_task, return_exceptions=True,
        )
        logger.info("Admin backend stopped")


from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="NOMAD Admin API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://adminomad.vercel.app",
        "https://nomadmin.pages.dev",
        "http://localhost:3000",
        "http://localhost:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(
    SessionMiddleware, 
    secret_key=settings.SECRET_KEY,
    same_site="none",
    https_only=True
)

app.include_router(admin.router)
app.include_router(support.router)
app.include_router(public.router)  # Подключаем после admin, чтобы /api/admin/* обрабатывался первым

app.mount("/static", StaticFiles(directory="app/static"), name="static")

templates = Jinja2Templates(directory="app/templates")


@app.get("/")
async def root():
    return {"message": "NOMAD Admin API", "docs": "/docs", "admin": "/admin"}


@app.get("/health")
async def health():
    try:
        await verify_database_ready()
    except Exception as exc:
        logger.error("Health check failed: %s", exc)
        raise HTTPException(status_code=503, detail="Database is not ready")
    return {"status": "ok"}


@app.get("/sw.js", include_in_schema=False)
async def service_worker():
    """Expose the admin worker at the origin root so it controls /admin."""
    return FileResponse("app/static/sw.js", media_type="application/javascript")


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    return templates.TemplateResponse("admin/index.html", {"request": request})
