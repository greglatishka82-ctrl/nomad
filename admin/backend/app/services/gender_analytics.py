"""Hourly name-based gender estimate used only by admin analytics."""

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select, text

from app.config import settings
from app.database import async_session, engine
from app.models.models import Client, GenderAnalytics


logger = logging.getLogger(__name__)
_TIMEZONE = ZoneInfo("Asia/Almaty")
_LOCK_ID = 2026082403
_CHUNK_SIZE = 100
_ALLOWED = {"male", "female", "unknown"}


def _provider() -> tuple[str, str, str] | None:
    """Return the Groq provider configuration."""
    if settings.GROQ_API_KEY:
        return "Groq", settings.GROQ_BASE_URL.rstrip("/"), settings.GROQ_MODEL
    return None


def _rate_limit_wait(response) -> float:
    """Seconds to wait before retrying a request rejected with HTTP 429."""
    header = response.headers.get("retry-after")
    if header:
        try:
            return min(max(float(header), 1.0), 120.0)
        except ValueError:
            pass
    match = re.search(r"try again in ([0-9.]+)s", response.text or "")
    if match:
        try:
            return min(max(float(match.group(1)) + 1.0, 1.0), 120.0)
        except ValueError:
            pass
    return 20.0


async def _post_with_rate_limit_retry(client, url: str, body: dict, attempts: int = 5):
    """Groq's rate limit is a small tokens-per-minute budget.

    A chunk can be rejected while the previous chunks are still counted against
    the current minute.  Waiting and retrying keeps the refresh working instead
    of losing the whole pass and keeping stale numbers.
    """
    response = None
    for attempt in range(attempts):
        response = await client.post(
            url,
            headers={"Authorization": f"Bearer {settings.GROQ_API_KEY}"},
            json=body,
        )
        if response.status_code != 429 or attempt == attempts - 1:
            return response
        wait = _rate_limit_wait(response)
        logger.info(
            "Rate limit reached, retrying in %.1fs (attempt %s/%s)",
            wait, attempt + 1, attempts,
        )
        await asyncio.sleep(wait)
    return response


def _parse_result(content: str, valid_ids: set[int]) -> dict[int, str]:
    start = content.find("{")
    end = content.rfind("}")
    if start < 0 or end < start:
        raise ValueError("AI response does not contain JSON")
    payload = json.loads(content[start:end + 1])
    result = {item_id: "unknown" for item_id in valid_ids}
    for item in payload.get("items", []):
        try:
            item_id = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        gender = str(item.get("gender", "unknown")).lower()
        if item_id in valid_ids and gender in _ALLOWED:
            result[item_id] = gender
    return result


async def _classify_chunk(
    client: httpx.AsyncClient, rows: list[tuple[int, str]], provider: tuple[str, str, str]
) -> dict[int, str]:
    provider_name, base_url, model = provider
    names = [{"id": item_id, "name": str(name).strip()[:200]} for item_id, name in rows]
    payload = {
        "model": model,
        "temperature": 0,
        "max_tokens": 4096,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Ты классификатор имён. Определи предполагаемый пол только по имени. "
                    "Верни ровно один JSON-объект с массивом items. Каждый элемент должен "
                    "содержать исходный числовой id и gender со значением male, female или unknown. "
                    "Верни каждый переданный id ровно один раз и не изменяй его. Для неоднозначного "
                    "имени, организации, мусора или сомнения ставь unknown. Не добавляй объяснений."
                ),
            },
            {"role": "user", "content": json.dumps({"items": names}, ensure_ascii=False)},
        ],
    }
    response = await _post_with_rate_limit_retry(
        client,
        f"{base_url}/chat/completions",
        {
            "model": model,
            "messages": payload["messages"],
            "stream": False,
            "temperature": 0,
            "max_tokens": 4096,
            "response_format": {"type": "json_object"},
        },
    )
    if response.status_code != 200:
        raise RuntimeError(f"{provider_name} returned HTTP {response.status_code}: {response.text[:300]}")
    content = response.json()["choices"][0]["message"]["content"]
    return _parse_result(content, {item_id for item_id, _ in rows})


async def _acquire_lock():
    if engine.dialect.name != "postgresql":
        return None, True
    connection = await engine.connect()
    acquired = bool(await connection.scalar(
        text("SELECT pg_try_advisory_lock(:lock_id)"), {"lock_id": _LOCK_ID}
    ))
    if not acquired:
        await connection.close()
        return None, False
    return connection, True


async def refresh_gender_analytics(force: bool = False) -> bool:
    """Refresh saved counts; a failure never overwrites the previous result."""
    provider = _provider()
    if provider is None:
        logger.warning("Gender analytics skipped: configure GROQ_API_KEY")
        return False

    lock_connection, acquired = await _acquire_lock()
    if not acquired:
        return False
    try:
        async with async_session() as db:
            cached = await db.get(GenderAnalytics, 1)
            now = datetime.now(_TIMEZONE).replace(tzinfo=None)
            # A full pass costs the whole free tokens-per-minute budget, so it
            # refreshes once a day; failed passes are retried on the next wake.
            if not force and cached and cached.updated_at and cached.updated_at >= now - timedelta(hours=20):
                return False
            rows = (await db.execute(
                select(Client.id, Client.name).where(
                    Client.name.is_not(None),
                    Client.is_deleted == False,
                ).order_by(Client.id)
            )).all()

        classified: dict[int, str] = {}
        async with httpx.AsyncClient(timeout=45.0) as client:
            chunk_size = 25
            for offset in range(0, len(rows), chunk_size):
                chunk = [(int(item_id), str(name)) for item_id, name in rows[offset:offset + chunk_size]]
                classified.update(await _classify_chunk(client, chunk, provider))

        counts = {gender: sum(value == gender for value in classified.values()) for gender in _ALLOWED}
        async with async_session() as db:
            cached = await db.get(GenderAnalytics, 1)
            if cached is None:
                cached = GenderAnalytics(id=1)
                db.add(cached)
            cached.male_count = counts["male"]
            cached.female_count = counts["female"]
            cached.unknown_count = counts["unknown"]
            cached.total_count = len(rows)
            cached.model = f"{provider[0]}: {provider[2]}"
            cached.updated_at = now
            await db.commit()
        logger.info("Gender analytics updated by %s for %s clients", provider[0], len(rows))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Gender analytics refresh failed; previous data kept: %s", exc)
        return False
    finally:
        if lock_connection is not None:
            try:
                await lock_connection.execute(
                    text("SELECT pg_advisory_unlock(:lock_id)"), {"lock_id": _LOCK_ID}
                )
            finally:
                await lock_connection.close()
