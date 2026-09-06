import os
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


# Павлодар — именованная часовая зона Казахстана. Она не зависит от
# часового пояса сервера Render и используется всеми Telegram-ботами.
TIMEZONE = ZoneInfo("Asia/Almaty")


class Settings:
    TELEGRAM_BOT_TOKEN: str = _env("TELEGRAM_BOT_TOKEN")
    INSTRUCTOR_BOT_TOKEN: str = _env("INSTRUCTOR_BOT_TOKEN")
    REPORT_BOT_TOKEN: str = _env("REPORT_BOT_TOKEN")
    REPORT_OWNER_PHONE: str = _env("REPORT_OWNER_PHONE")
    ADMIN_USERNAME: str = _env("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD: str = _env("ADMIN_PASSWORD", "admin123")
    DATABASE_URL: str = _env("DATABASE_URL", f"sqlite+aiosqlite:///{BASE_DIR / 'nomad.db'}")
    SECRET_KEY: str = _env("SECRET_KEY", "nomad-secret-key-change-in-production")

    GROQ_API_KEY: str = _env("GROQ_API_KEY")
    GROQ_MODEL: str = _env("GROQ_MODEL", "openai/gpt-oss-20b")
    GROQ_BASE_URL: str = _env("GROQ_BASE_URL", "https://api.groq.com/openai/v1")

    NVIDIA_API_KEY: str = _env("NVIDIA_API_KEY")
    NVIDIA_MODEL: str = _env("NVIDIA_MODEL", "openai/gpt-oss-20b")
    NVIDIA_BASE_URL: str = _env("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")

    LOCATION_MAIN = "Циолковского 30"
    LOCATION_EXAM = "Циолковского 30"
    PRICE_TRAINING = 10000
    PRICE_TRAINING_NEW = 10000
    PRICE_EXAM = 5000
    TRAINING_DURATION_MINUTES = 60
    EXAM_DURATION_MINUTES = 20
    MAX_CARS_EXAM_LOCATION = 6    # максимум машин на Циолковского 30
    MAX_CARS_MAIN_LOCATION = 6    # оставлено для совместимости; площадка одна
    MAX_INSTRUCTORS_PER_LOCATION = 3
    WORKING_HOURS_START = 9
    WORKING_HOURS_END = 19
    LUNCH_START_HOUR = 13
    LUNCH_END_HOUR = 14
    MIN_RATING = 1.0
    RATING_STEP = 0.1
    MAX_ACTIVE_BOOKINGS = 2
    CONFIRM_TIMEOUT_MINUTES = 30


settings = Settings()
