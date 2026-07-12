"""
JWT-аутентификация и email-утилиты для мобильного приложения.
"""
import logging
import secrets
import smtplib
import string
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings

logger = logging.getLogger(__name__)

# ── Токены ────────────────────────────────────────────────────────────────────

ACCESS_TOKEN_TTL_HOURS = 1
REFRESH_TOKEN_TTL_DAYS = 30
ALGORITHM = "HS256"

bearer_scheme = HTTPBearer()


def create_access_token(user_id: int) -> str:
    payload = {
        "sub": str(user_id),
        "type": "access",
        "exp": datetime.now(timezone.utc) + timedelta(hours=ACCESS_TOKEN_TTL_HOURS),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def create_refresh_token(user_id: int) -> str:
    payload = {
        "sub": str(user_id),
        "type": "refresh",
        "exp": datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_TTL_DAYS),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str, token_type: str = "access") -> Optional[int]:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != token_type:
            return None
        return int(payload["sub"])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.PyJWTError:
        return None


async def get_current_user_id(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> int:
    user_id = decode_token(credentials.credentials, "access")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Недействительный или истёкший токен",
        )
    return user_id


# ── Email ──────────────────────────────────────────────────────────────────────

def generate_password(length: int = 10) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _send_email(to_email: str, subject: str, html_body: str) -> bool:
    smtp_host = settings.SMTP_HOST
    smtp_port = int(settings.SMTP_PORT)
    smtp_user = settings.SMTP_USER
    smtp_password = settings.SMTP_PASSWORD

    if not smtp_host or not smtp_user:
        logger.warning("SMTP не настроен — письмо не отправлено")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"NOMAD Автошкола <{smtp_user}>"
        msg["To"] = to_email
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        with smtplib.SMTP_SSL(smtp_host, smtp_port) as server:
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_user, to_email, msg.as_string())
        return True
    except Exception as e:
        logger.error(f"Ошибка отправки email на {to_email}: {e}")
        return False


def send_welcome_email(to_email: str, name: str, password: str) -> None:
    subject = "Добро пожаловать в NOMAD! Ваши данные для входа"
    html_body = f"""
    <div style="font-family:Arial,sans-serif;max-width:500px;margin:0 auto;">
      <h2 style="color:#1A2B4A;">Академия вождения NOMAD 🚗</h2>
      <p>Привет, <b>{name}</b>!</p>
      <p>Ваш аккаунт в мобильном приложении успешно создан.</p>
      <p><b>Email:</b> {to_email}<br>
         <b>Пароль:</b> {password}</p>
      <p>Сохраните этот пароль — он понадобится для входа.</p>
      <hr>
      <p style="color:#888;font-size:12px;">Академия вождения NOMAD · Павлодар · +7 702 718 22 33</p>
    </div>
    """
    _send_email(to_email, subject, html_body)


def send_password_reset_email(to_email: str, name: str, new_password: str) -> None:
    subject = "NOMAD — сброс пароля"
    html_body = f"""
    <div style="font-family:Arial,sans-serif;max-width:500px;margin:0 auto;">
      <h2 style="color:#1A2B4A;">Академия вождения NOMAD 🚗</h2>
      <p>Привет, <b>{name}</b>!</p>
      <p>Вы запросили сброс пароля. Ваш новый пароль:</p>
      <p style="font-size:22px;font-weight:bold;color:#FF6B35;">{new_password}</p>
      <p>После входа рекомендуем сменить его в настройках профиля.</p>
      <hr>
      <p style="color:#888;font-size:12px;">Академия вождения NOMAD · Павлодар · +7 702 718 22 33</p>
    </div>
    """
    _send_email(to_email, subject, html_body)
