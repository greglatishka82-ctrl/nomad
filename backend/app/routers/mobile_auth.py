"""
Мобильное API - Аутентификация
POST /api/mobile/auth/register
POST /api/mobile/auth/login
POST /api/mobile/auth/refresh
GET  /api/mobile/auth/me
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import APIRouter, Depends, HTTPException, status, Header
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.models import Client, Event, MobileSession, ReferralRecord
from app.services.mobile_auth import hash_password, verify_password
from app.services.client_lifecycle import (
    find_client_by_phone as _find_client_by_phone,
    reactivate_deleted_client,
)
from app.services.phone_utils import normalize_phone

router = APIRouter(prefix="/api/mobile/auth", tags=["mobile-auth"])


class RegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    phone: str = Field(min_length=5, max_length=30)
    password: str = Field(min_length=6, max_length=128)
    password_confirmation: str = Field(min_length=6, max_length=128)
    referral_code: Optional[str] = Field(default=None, max_length=50)


class LoginRequest(BaseModel):
    phone: str = Field(min_length=5, max_length=30)
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: int
    name: str
    phone: str
    created_at: datetime


def _validate_password(password: str) -> None:
    """Keep app passwords easy to type on an English keyboard and consistent."""
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="Пароль должен содержать минимум 6 символов")
    if not password.isascii() or any(char.isspace() for char in password) or not any(char.isalpha() for char in password):
        raise HTTPException(
            status_code=400,
            detail="Используйте пароль от 6 символов: латинские буквы, цифры и символы без пробелов",
        )


def create_token(user_id: int, session_id: str, token_type: str = "access") -> str:
    expires = timedelta(hours=24 if token_type == "access" else 168)  # 24h или 7 дней
    payload = {
        "sub": str(user_id),
        "sid": session_id,
        "type": token_type,
        "exp": datetime.now(timezone.utc) + expires,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")


def _decode_token(token: str, expected_type: str) -> dict:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
        if payload.get("type") != expected_type or not payload.get("sid"):
            raise ValueError("invalid token type or session")
        int(payload["sub"])
        return payload
    except (jwt.PyJWTError, KeyError, TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid token")


async def _create_session(db: AsyncSession, user_id: int) -> MobileSession:
    import uuid
    session = MobileSession(
        id=uuid.uuid4().hex,
        client_id=user_id,
        expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=7),
    )
    db.add(session)
    return session


async def _active_session(db: AsyncSession, payload: dict) -> MobileSession:
    session = await db.get(MobileSession, payload["sid"])
    if (
        not session
        or not session.is_active
        or session.client_id != int(payload["sub"])
        or session.expires_at <= datetime.now(timezone.utc).replace(tzinfo=None)
    ):
        raise HTTPException(status_code=401, detail="Session is no longer active")
    return session


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, db: AsyncSession = Depends(get_db)):
    # Нормализуем телефон
    normalized_phone = normalize_phone(body.phone)
    if not normalized_phone:
        raise HTTPException(status_code=400, detail="Введите корректный номер телефона")
    _validate_password(body.password)
    clean_name = body.name.strip()
    if not clean_name or any(ord(char) < 32 for char in clean_name):
        raise HTTPException(status_code=400, detail="Введите корректное имя")
    if body.password != body.password_confirmation:
        raise HTTPException(status_code=400, detail="Пароли не совпадают")
    # A client may already have been added by an administrator after a phone
    # call. In that case the phone is the identity: attach the mobile account
    # to that existing profile instead of creating a second client.
    existing_user = await _find_client_by_phone(
        db, normalized_phone, include_deleted=True, for_update=True,
    )
    was_deleted = bool(existing_user and existing_user.is_deleted)

    if existing_user and not was_deleted and existing_user.password_hash:
        raise HTTPException(status_code=400, detail="Для этого номера уже создан аккаунт. Войдите в него.")

    import uuid
    referral_code = f"NOMAD-{uuid.uuid4().hex[:6].upper()}"
    referred_by_client_id = None
    referral_discount_available = False
    if body.referral_code and not existing_user:
        ref_result = await db.execute(
            select(Client).where(
                Client.referral_code == body.referral_code.strip().upper(),
                Client.is_deleted == False,
            )
        )
        referrer = ref_result.scalar_one_or_none()
        if not referrer:
            raise HTTPException(status_code=400, detail="Реферальный код не найден")
        referred_by_client_id = referrer.id
        referral_discount_available = True

    if existing_user:
        user = existing_user
        if was_deleted:
            await reactivate_deleted_client(
                db,
                user,
                name=clean_name,
                phone=normalized_phone,
                password_hash=hash_password(body.password),
            )
            if not user.referral_code:
                user.referral_code = referral_code
        else:
            # Do not overwrite the name entered by the administrator: it is
            # the canonical card that already owns bookings and history.
            user.phone = normalized_phone
            user.password_hash = hash_password(body.password)
        db.add(Event(
            event_type="client_profile_reactivated" if was_deleted else "client_profile_linked",
            source="mobile",
            client_id=user.id,
            message=(
                f"Клиент «{user.name}» повторно зарегистрировался в приложении."
                if was_deleted else
                f"Клиент «{user.name}» привязал приложение к своей карточке."
            ),
        ))
    else:
        user = Client(
            name=clean_name,
            phone=normalized_phone,
            password_hash=hash_password(body.password),
            referral_code=referral_code,
            referred_by_client_id=referred_by_client_id,
            referral_discount_available=referral_discount_available,
        )
        db.add(user)
        await db.flush()
        if referred_by_client_id:
            db.add(ReferralRecord(referrer_client_id=referred_by_client_id, referred_client_id=user.id))
        ref_note = " по реферальному коду" if referred_by_client_id else ""
        db.add(Event(
            event_type="client_registered",
            source="mobile",
            client_id=user.id,
            message=f"Клиент «{user.name}» зарегистрировался в приложении{ref_note}.",
        ))
    await db.flush()
    session = await _create_session(db, user.id)
    await db.commit()
    await db.refresh(user)

    return TokenResponse(
        access_token=create_token(user.id, session.id, "access"),
        refresh_token=create_token(user.id, session.id, "refresh"),
    )


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    normalized_phone = normalize_phone(body.phone)
    user = await _find_client_by_phone(db, normalized_phone) if normalized_phone else None

    if not user or not user.password_hash or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Неверный номер телефона или пароль")

    session = await _create_session(db, user.id)
    await db.commit()

    return TokenResponse(
        access_token=create_token(user.id, session.id, "access"),
        refresh_token=create_token(user.id, session.id, "refresh"),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(body: RefreshRequest, db: AsyncSession = Depends(get_db)):
    try:
        payload = jwt.decode(body.refresh_token, settings.SECRET_KEY, algorithms=["HS256"])
        if payload.get("type") != "refresh":
            raise ValueError("invalid token type")
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    user = await db.get(Client, user_id)
    if not user or user.is_deleted:
        raise HTTPException(status_code=401, detail="User not found")

    if payload.get("sid"):
        session = await _active_session(db, payload)
    else:
        # Seamlessly upgrade refresh tokens issued before per-device sessions
        # existed. The next access token is fully revocable.
        session = await _create_session(db, user_id)
        await db.commit()

    return TokenResponse(
        access_token=create_token(user.id, session.id, "access"),
        refresh_token=create_token(user.id, session.id, "refresh"),
    )


async def get_current_user(
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
) -> Client:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")

    token = authorization[7:]
    payload = _decode_token(token, "access")
    await _active_session(db, payload)
    user_id = int(payload["sub"])

    user = await db.get(Client, user_id)
    if not user or user.is_deleted:
        raise HTTPException(status_code=401, detail="User not found")

    return user


@router.get("/me", response_model=UserResponse)
async def get_me(user: Client = Depends(get_current_user)):
    return UserResponse(
        id=user.id,
        name=user.name,
        phone=user.phone,
        created_at=user.created_at,
    )


@router.post("/logout")
async def logout(
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = _decode_token(authorization[7:], "access")
    session = await _active_session(db, payload)
    session.is_active = False
    await db.commit()
    return {"message": "Logged out"}


@router.post("/forgot-password")
async def forgot_password():
    return {"message": "Для смены пароля обратитесь к администратору NOMAD"}
