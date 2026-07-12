import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


class Settings:
    TELEGRAM_BOT_TOKEN: str = _env("TELEGRAM_BOT_TOKEN")
    INSTRUCTOR_BOT_TOKEN: str = _env("INSTRUCTOR_BOT_TOKEN")
    ADMIN_USERNAME: str = _env("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD: str = _env("ADMIN_PASSWORD", "admin123")
    DATABASE_URL: str = _env("DATABASE_URL", f"sqlite+aiosqlite:///{BASE_DIR / 'nomad.db'}")
    SECRET_KEY: str = _env("SECRET_KEY", "nomad-secret-key-change-in-production")

    GROQ_API_KEY: str = _env("GROQ_API_KEY")
    GROQ_MODEL: str = _env("GROQ_MODEL", "openai/gpt-oss-120b")
    GROQ_BASE_URL: str = _env("GROQ_BASE_URL", "https://api.groq.com/openai/v1")

    NVIDIA_API_KEY: str = _env("NVIDIA_API_KEY")
    NVIDIA_MODEL: str = _env("NVIDIA_MODEL", "meta/llama-3.3-70b-instruct")
    NVIDIA_BASE_URL: str = _env("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")

    # SMTP для мобильного приложения (восстановление пароля, welcome email)
    SMTP_HOST: str = _env("SMTP_HOST", "smtp.gmail.com")
    SMTP_PORT: str = _env("SMTP_PORT", "465")
    SMTP_USER: str = _env("SMTP_USER", "")
    SMTP_PASSWORD: str = _env("SMTP_PASSWORD", "")

    LOCATION_MAIN = "Циолковского 28/1"
    LOCATION_EXAM = "Циолковского 30"
    PRICE_TRAINING = 6000
    PRICE_EXAM = 5000
    TRAINING_DURATION_MINUTES = 60
    EXAM_DURATION_MINUTES = 15
    MAX_INSTRUCTORS_PER_LOCATION = 3
    WORKING_HOURS_START = 9
    WORKING_HOURS_END = 19
    MIN_RATING = 1.0
    RATING_STEP = 0.1
    MAX_ACTIVE_BOOKINGS = 2
    CONFIRM_TIMEOUT_MINUTES = 30


settings = Settings()
