import os
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _env_bool(key: str, default: bool = False) -> bool:
    value = os.getenv(key)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


# Павлодар — именованная часовая зона Казахстана. Она не зависит от
# часового пояса сервера и используется всеми Telegram-ботами.
TIMEZONE = ZoneInfo("Asia/Almaty")


class Settings:
    TELEGRAM_BOT_TOKEN: str = _env("TELEGRAM_BOT_TOKEN")
    INSTRUCTOR_BOT_TOKEN: str = _env("INSTRUCTOR_BOT_TOKEN")
    REPORT_BOT_TOKEN: str = _env("REPORT_BOT_TOKEN")
    REPORT_OWNER_PHONE: str = _env("REPORT_OWNER_PHONE")
    MOBILE_BOOKING_ENABLED: bool = _env_bool("MOBILE_BOOKING_ENABLED", True)
    # Пробный экзамен временно не продаём: услуга скрыта на сайте, в боте и
    # приложении. Старые записи с типом EXAM продолжают читаться.
    EXAM_ENABLED: bool = _env_bool("EXAM_ENABLED", False)
    # Рассылки клиентам из бота (напоминания, подтверждения, оценки).
    # По умолчанию выключены: новый бот не должен писать по старым записям.
    BOT_NOTIFICATIONS_ENABLED: bool = _env_bool("BOT_NOTIFICATIONS_ENABLED", False)
    ADMIN_USERNAME: str = _env("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD: str = _env("ADMIN_PASSWORD", "admin123")
    DATABASE_URL: str = _env("DATABASE_URL", f"sqlite+aiosqlite:///{BASE_DIR / 'nomad.db'}")
    SECRET_KEY: str = _env("SECRET_KEY", "nomad-secret-key-change-in-production")

    GROQ_API_KEY: str = _env("GROQ_API_KEY")
    GROQ_BASE_URL: str = _env("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
    GROQ_MODEL: str = _env("GROQ_MODEL", "openai/gpt-oss-20b")

    # Бренд
    BRAND_NAME = "GREENTIME"
    BRAND_TAGLINE = "Решил - действуй"
    BRAND_SUBTITLE = "Школа практического вождения"
    PHONE = "+7 707 881 08 48"
    PHONE_RAW = "+77078810848"
    TELEGRAM_BOT_URL = "https://t.me/green_time_drive_bot"
    PUBLIC_SITE_URL: str = _env(
        "PUBLIC_SITE_URL", "https://greentime.57-129-153-111.sslip.io"
    )

    LOCATION_MAIN = "Лесная 5"
    LOCATION_EXAM = "Лесная 5"
    PRICE_TRAINING = 7000
    PRICE_TRAINING_NEW = 7000
    PRICE_EXAM = 3500
    TRAINING_DURATION_MINUTES = 60
    EXAM_DURATION_MINUTES = 20
    MAX_CARS_EXAM_LOCATION = 6    # максимум машин на Лесной 5
    MAX_CARS_MAIN_LOCATION = 6    # оставлено для совместимости; площадка одна
    MAX_INSTRUCTORS_PER_LOCATION = 3
    # Рабочие часы школы — только для показа клиенту (лендинг, приложение, бот).
    # На выбор слотов они НЕ влияют: границу задаёт график инструктора.
    WORKING_HOURS_START = 8
    WORKING_HOURS_END = 22
    # Последний час старта пробного экзамена. Это единственное ограничение
    # для клиента, заданное бизнес-правилом; 0 или None — без ограничения,
    # тогда экзамен идёт по графику инструктора, как и вождение.
    EXAM_LAST_SLOT_HOUR = 20
    LUNCH_START_HOUR = 13
    LUNCH_END_HOUR = 14
    MIN_RATING = 1.0
    RATING_STEP = 0.1
    MAX_ACTIVE_BOOKINGS = 2
    CONFIRM_TIMEOUT_MINUTES = 30


settings = Settings()
