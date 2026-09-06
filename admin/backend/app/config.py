import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")
load_dotenv(BASE_DIR.parent.parent / ".env")


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


class Settings:
    ADMIN_USERNAME: str = _env("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD: str = _env("ADMIN_PASSWORD", "admin123")
    DATABASE_URL: str = _env("DATABASE_URL", f"sqlite+aiosqlite:///{BASE_DIR / 'nomad.db'}")
    SECRET_KEY: str = _env("SECRET_KEY", "nomad-secret-key-change-in-production")

    GROQ_API_KEY: str = _env("GROQ_API_KEY")
    GROQ_MODEL: str = _env("GROQ_MODEL", "openai/gpt-oss-120b")
    GROQ_BASE_URL: str = _env("GROQ_BASE_URL", "https://api.groq.com/openai/v1")

    NVIDIA_API_KEY: str = _env("NVIDIA_API_KEY")
    NVIDIA_MODEL: str = _env("NVIDIA_MODEL", "openai/gpt-oss-20b")
    NVIDIA_BASE_URL: str = _env("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")

    # Telegram bots
    INSTRUCTOR_BOT_TOKEN: str = _env("INSTRUCTOR_BOT_TOKEN", "")
    # Клиентский бот (уведомления клиенту о подтверждении/отклонении заявки)
    BOT_TOKEN: str = _env("TELEGRAM_BOT_TOKEN", "")

    # OneSignal push notifications
    ONESIGNAL_APP_ID: str = _env("ONESIGNAL_APP_ID", "51602607-2b29-467b-ac12-5a7921f05a7e")
    ONESIGNAL_REST_API_KEY: str = _env("ONESIGNAL_REST_API_KEY", "")

    MIN_RATING = 1.0
    TRAINING_DURATION_MINUTES = 60
    EXAM_DURATION_MINUTES = 20
    LOCATION_MAIN = "Циолковского 30"
    LOCATION_EXAM = "Циолковского 30"
    PRICE_TRAINING = 10000
    PRICE_TRAINING_NEW = 10000
    PRICE_EXAM = 5000
    MAX_CARS_EXAM_LOCATION = 6


settings = Settings()
