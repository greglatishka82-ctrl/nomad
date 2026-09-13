"""
Сервис отправки push-уведомлений через OneSignal REST API.
Документация: https://documentation.onesignal.com/reference/create-notification
"""
import logging
import httpx

from app.config import settings

logger = logging.getLogger(__name__)

ONESIGNAL_API_URL = "https://onesignal.com/api/v1/notifications"


async def send_push_to_user(
    user_id: int,
    title: str,
    body: str,
    data: dict | None = None,
) -> bool:
    """
    Отправляет push-уведомление конкретному пользователю по external_id = 'user_{user_id}'.
    Пользователь должен быть залогинен в OneSignal через NotificationService.loginUser(userId).
    Возвращает True при успехе, False при ошибке или если ключ не настроен.
    """
    if not settings.ONESIGNAL_REST_API_KEY:
        logger.debug("ONESIGNAL_REST_API_KEY not set — push skipped")
        return False

    payload = {
        "app_id": settings.ONESIGNAL_APP_ID,
        "include_aliases": {"external_id": [f"user_{user_id}"]},
        "target_channel": "push",
        "headings": {"en": title, "ru": title},
        "contents": {"en": body, "ru": body},
        "android_channel_id": "nomad_support",  # совпадает с channelId в Flutter
        "data": data or {},
    }

    headers = {
        "Authorization": f"Key {settings.ONESIGNAL_REST_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(ONESIGNAL_API_URL, json=payload, headers=headers)
            if resp.status_code == 200:
                result = resp.json()
                if result.get("errors"):
                    logger.warning(f"OneSignal push errors for user {user_id}: {result['errors']}")
                    return False
                logger.info(f"OneSignal push sent to user_{user_id}, id={result.get('id')}")
                return True
            else:
                logger.warning(
                    f"OneSignal push failed for user {user_id}: "
                    f"HTTP {resp.status_code} — {resp.text[:200]}"
                )
                return False
    except Exception as e:
        logger.error(f"OneSignal push exception for user {user_id}: {e}")
        return False
