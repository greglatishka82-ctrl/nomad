import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


class Settings:
    ADMIN_USERNAME: str = _env("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD: str = _env("ADMIN_PASSWORD", "admin123")
    DATABASE_URL: str = _env("DATABASE_URL", f"sqlite+aiosqlite:///{BASE_DIR / 'nomad.db'}")
    SECRET_KEY: str = _env("SECRET_KEY", "nomad-secret-key-change-in-production")

    MIN_RATING = 1.0
    TRAINING_DURATION_MINUTES = 60
    EXAM_DURATION_MINUTES = 15


settings = Settings()
