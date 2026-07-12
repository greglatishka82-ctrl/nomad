"""
Роутер аутентификации мобильного приложения.
POST /api/mobile/auth/register
POST /api/mobile/auth/login
POST /api/mobile/auth/refresh
POST /api/mobile/auth/logout
POST /api/mobile/auth/forgot-password
"""
import secrets
import logging
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.models import MobileUser
from app.services.auth import hash_password, verify_password
from app.services.mobile_auth import (
    create_access_token, create_refresh_token, decode_token,
    generate_password, send_welcome_email, send_password_reset_email,
    get_current_user_id,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/mobile/auth", tags=["mobile-auth"])

# ── Rate limiting ────────────────────────────────────────────────────────────
_login_attempts: dict[str, list[float]] = {}
MAX_LOGIN_ATTEMPTS = 10
LOGIN_BLOCK_SECONDS = 15 * 60  # 15 минут


def _check_rate_limit(email: str) -> None:
    now = time.time()
    attempts = _login_attempts.get(email, [])
    # Очищаем попытки старше 15 минут
    attempts = [t for t in attempts if now - t < LOGIN_BLOCK_SECONDS]
    _login_attempts[email] = attempts
    if len(attempts) >= MAX_LOGIN_ATTEMPTS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Слишком много попыток входа. Попробуйте через {LOGIN_BLOCK_SECONDS // 60} минут",
        )


def _record_failed_attempt(email: str) -> None:
    _login_attempts.setdefault(email, []).append(time.time())


def _clear_attempts(email: str) -> None:
    _login_attempts.pop(email, None)


# ── Схемы ──────────────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    name: str
    phone: str
    email: EmailStr
    password: str
    referral_code: Optional[str] = None

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Имя не может быть пустым")
        return v

    @field_validator("password")
    @classmethod
    def password_length(cls, v: str) -> str:
        if len(v) < 6:
            raise ValueError("Пароль должен содержать не менее 6 символов")
        return v

    @field_validator("phone")
    @classmethod
    def phone_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Телефон не может быть пустым")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user_id: int
    name: str
    email: str


# ── Эндпоинты ─────────────────────────────────────────────────────────────────

@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    # Проверяем уникальность email
    existing = await db.execute(
        select(MobileUser).where(MobileUser.email == body.email.lower())
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Пользователь с таким email уже зарегистрирован",
        )

    # Обрабатываем реферальный код
    referrer: Optional[MobileUser] = None
    if body.referral_code:
        ref_result = await db.execute(
            select(MobileUser).where(MobileUser.referral_code == body.referral_code.upper())
        )
        referrer = ref_result.scalar_one_or_none()

    # Генерируем уникальный реферальный код для нового пользователя
    own_ref_code = secrets.token_hex(4).upper()

    user = MobileUser(
        name=body.name.strip(),
        phone=body.phone.strip(),
        email=body.email.lower(),
        password_hash=hash_password(body.password),
        referral_code=own_ref_code,
        referred_by_id=referrer.id if referrer else None,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    # Записываем реферальную связь
    if referrer:
        from app.models.models import MobileReferralRecord
        db.add(MobileReferralRecord(referrer_id=referrer.id, referred_id=user.id))
        await db.commit()

    # Отправляем welcome email в фоне (не блокируем ответ)
    background_tasks.add_task(send_welcome_email, user.email, user.name, body.password)

    return TokenResponse(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
        user_id=user.id,
        name=user.name,
        email=user.email,
    )


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    _check_rate_limit(body.email.lower())

    result = await db.execute(
        select(MobileUser).where(MobileUser.email == body.email.lower())
    )
    user = result.scalar_one_or_none()

    if not user or not verify_password(body.password, user.password_hash):
        _record_failed_attempt(body.email.lower())
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный email или пароль",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Аккаунт заблокирован",
        )

    _clear_attempts(body.email.lower())

    return TokenResponse(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
        user_id=user.id,
        name=user.name,
        email=user.email,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest, db: AsyncSession = Depends(get_db)):
    user_id = decode_token(body.refresh_token, "refresh")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Недействительный refresh token",
        )

    result = await db.execute(select(MobileUser).where(MobileUser.id == user_id))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Пользователь не найден")

    return TokenResponse(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
        user_id=user.id,
        name=user.name,
        email=user.email,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(user_id: int = Depends(get_current_user_id)):
    # Stateless JWT — реальная инвалидация делается на клиенте (удалить токены из storage)
    return


@router.post("/forgot-password", status_code=status.HTTP_200_OK)
async def forgot_password(
    body: ForgotPasswordRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(MobileUser).where(MobileUser.email == body.email.lower())
    )
    user = result.scalar_one_or_none()

    # Всегда отвечаем одинаково чтобы не раскрывать существование аккаунта
    if user:
        new_pwd = generate_password()
        user.password_hash = hash_password(new_pwd)
        await db.commit()
        background_tasks.add_task(
            send_password_reset_email, user.email, user.name, new_pwd
        )

    return {"message": "Если аккаунт существует, новый пароль отправлен на email"}
