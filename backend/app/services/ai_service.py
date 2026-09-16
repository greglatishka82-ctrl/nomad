import asyncio
import logging
import re
import json
from pathlib import Path
from typing import Optional

import httpx
from sqlalchemy import select

from app.config import settings
from app.database import async_session
from app.models.models import FAQItem, Instructor, Package, Vehicle

logger = logging.getLogger(__name__)
PROMPT_PATH = Path(__file__).with_name("landing_chat_policy_v2.md")
# One process serves the landing chat.  The semaphore serialises model calls so
# a burst of questions cannot exceed the provider rate limits.
_ai_queue = asyncio.Semaphore(1)

_TOKEN_RE = re.compile(r"[а-яёa-z0-9]{3,}", re.IGNORECASE)
_STOP_TOKENS = {"как", "что", "это", "или", "для", "про", "есть", "могу", "можно", "сколько"}


def _tokens(value: str) -> set[str]:
    return {token.casefold() for token in _TOKEN_RE.findall(value) if token.casefold() not in _STOP_TOKENS}


def _format_money(value: int) -> str:
    return f"{value:,}".replace(",", " ") + " ₸"


def _faq_score(question_tokens: set[str], item: FAQItem) -> int:
    item_tokens = _tokens(f"{item.question} {item.answer}")
    score = 0
    for question_token in question_tokens:
        for item_token in item_tokens:
            same_price_meaning = (
                question_token.startswith("цен") and item_token.startswith("стоим")
            ) or (
                question_token.startswith("стоим") and item_token.startswith("цен")
            )
            same_contact_meaning = (
                question_token.startswith(("телефон", "контакт"))
                and item_token.startswith(("связ", "позвон"))
            )
            if same_price_meaning or same_contact_meaning or question_token == item_token or (
                len(question_token) >= 4 and len(item_token) >= 4 and question_token[:4] == item_token[:4]
            ):
                score += 1
                break
    return score


def _has_topic(question_tokens: set[str], stems: tuple[str, ...]) -> bool:
    return any(token.startswith(stem) for token in question_tokens for stem in stems)


async def _get_retrieved_facts(question: str) -> dict:
    """Return a compact, request-scoped JSON context from PostgreSQL only."""
    question_tokens = _tokens(question)
    catalog_query = _has_topic(question_tokens, ("услуг", "предлаг", "ассортим", "пакет")) or question.strip().endswith("есть?")
    wants_packages = catalog_query or _has_topic(question_tokens, ("пакет", "цен", "стоим", "скид", "сертифик", "рефер"))
    wants_instructors = _has_topic(question_tokens, ("инструкт", "механ", "автомат", "акпп", "мкпп"))
    wants_vehicles = _has_topic(question_tokens, ("автомоб", "машин", "cobalt", "шеврол"))
    try:
        async with async_session() as session:
            faq_items = (await session.execute(
                select(FAQItem).where(FAQItem.is_active.is_(True)).order_by(FAQItem.sort_order, FAQItem.id)
            )).scalars().all()
            packages = (await session.execute(
                select(Package).where(Package.is_active.is_(True)).order_by(Package.sessions_count, Package.id)
            )).scalars().all()
            instructors = []
            vehicles = []
            if wants_instructors:
                instructors = (await session.execute(
                    select(Instructor).where(Instructor.is_active.is_(True)).order_by(Instructor.name)
                )).scalars().all()
            if wants_vehicles:
                vehicles = (await session.execute(
                    select(Vehicle).where(Vehicle.is_under_repair.is_(False)).order_by(Vehicle.name)
                )).scalars().all()
    except Exception as exc:
        logger.error("Ошибка при загрузке актуальных данных для AI: %s", exc)
        return {"database_status": "unavailable", "capabilities": _chat_capabilities()}

    ranked_faq = sorted(
        ((item, _faq_score(question_tokens, item)) for item in faq_items),
        key=lambda pair: (-pair[1], pair[0].sort_order, pair[0].id),
    )
    relevant_faq = [item for item, score in ranked_faq if score > 0][:5]
    if catalog_query and not relevant_faq:
        relevant_faq = faq_items[:5]
    return {
        "database_status": "ok",
        "capabilities": _chat_capabilities(),
        "requested_categories": {
            "service_catalog": {
                "requested": catalog_query,
                "has_confirmed_records": bool(relevant_faq or packages),
            },
            "instructors": {
                "requested": wants_instructors,
                "has_confirmed_records": bool(instructors),
            },
            "vehicles": {
                "requested": wants_vehicles,
                "has_confirmed_records": bool(vehicles),
            },
        },
        "faq": [
            {"id": item.id, "question": item.question, "answer": item.answer}
            for item in relevant_faq
        ],
        "packages": [
            {
                "id": package.id,
                "name": package.name,
                "sessions_count": package.sessions_count,
                "price_kzt": package.price,
                "description": package.description,
                "validity_days": package.validity_days,
                "bonus_exam": package.bonus_exam,
            }
            for package in packages[:10]
        ] if wants_packages else [],
        "instructors": [
            {
                "id": instructor.id,
                "name": instructor.name,
                "transmission": instructor.transmission,
                "experience_years": instructor.experience_years,
                "rating": instructor.rating,
                "description": instructor.description,
            }
            for instructor in instructors[:10]
        ],
        "vehicles": [
            {"id": vehicle.id, "name": vehicle.name, "transmission": vehicle.transmission}
            for vehicle in vehicles[:10]
        ],
    }


def _chat_capabilities() -> dict:
    return {
        "can_answer_questions": True,
        "can_create_booking": False,
        "can_confirm_booking": False,
        "can_cancel_booking": False,
        "can_reschedule_booking": False,
        "can_select_instructor": False,
        "can_read_client_data": False,
    }


async def _build_system_prompt(question: str, has_history: bool = False) -> str:
    """Read policy and append only the relevant PostgreSQL facts for this request."""
    facts = await _get_retrieved_facts(question)
    policy = PROMPT_PATH.read_text(encoding="utf-8")
    requested_categories = facts.get("requested_categories", {})
    unavailable_categories = [
        name
        for name, state in requested_categories.items()
        if state.get("requested") and not state.get("has_confirmed_records")
    ]
    request_state = {
        "unavailable_requested_categories": unavailable_categories,
    }
    dialogue_note = ""
    if has_history:
        dialogue_note = (
            "\n\nДИАЛОГ_ИДЁТ: разговор уже начат. Не здоровайся заново, не повторяй "
            "ранее сказанное и учитывай предыдущие сообщения. Отвечай только на "
            "последний вопрос клиента, опираясь на контекст диалога."
        )
    return (
        f"{policy}{dialogue_note}\n\nFACTS:\n"
        f"{json.dumps(facts, ensure_ascii=False, separators=(',', ':'))}\n\n"
        f"REQUEST_STATE:\n{json.dumps(request_state, ensure_ascii=False, separators=(',', ':'))}"
    )



def _retry_after_seconds(response) -> float:
    value = response.headers.get("retry-after")
    try:
        return max(1.0, min(float(value), 10.0))
    except (TypeError, ValueError):
        return 3.0


async def _call_groq(
    messages: list, timeout: float = 60.0, max_tokens: int = 384, temperature: float = 0.0
) -> Optional[str]:
    """Call Groq's OpenAI-compatible chat completions API with rate-limit retries."""
    if not settings.GROQ_API_KEY:
        return None
    url = f"{settings.GROQ_BASE_URL.rstrip('/')}/chat/completions"
    payload = {
        "model": settings.GROQ_MODEL,
        "messages": messages,
        "stream": False,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    headers = {"Authorization": f"Bearer {settings.GROQ_API_KEY}"}
    attempts = 3
    for attempt in range(1, attempts + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, headers=headers, json=payload)
        except httpx.TimeoutException:
            logger.warning("Groq timeout after %ss", timeout)
            return None
        except Exception as exc:
            logger.warning("Groq error: %s", exc)
            return None
        if resp.status_code == 429:
            if attempt < attempts:
                delay = _retry_after_seconds(resp)
                logger.warning("Groq rate limited (429); retry %s/%s in %.1fs",
                               attempt, attempts - 1, delay)
                await asyncio.sleep(delay)
                continue
            logger.warning("Groq rate limited (429); giving up after %s attempts", attempts)
            return None
        if resp.status_code != 200:
            logger.warning("Groq returned %s: %s", resp.status_code, resp.text[:200])
            return None
        try:
            choices = resp.json().get("choices") or []
            content = choices[0].get("message", {}).get("content") if choices else None
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            logger.warning("Groq invalid response: %s", exc)
            return None
        if not content:
            return None
        return content.strip()
    return None


_HISTORY_LIMIT = 6
_FALLBACK_PREFIX = "Извините, AI-помощник временно недоступен"


async def chat_completion(user_message: str, history: Optional[list] = None) -> str:
    """Answer through the Groq provider keeping the dialogue context."""
    # Keep both sides of the dialogue.  The operator policy (facts only from
    # FACTS) still applies, but the model needs its own previous replies to
    # resolve references, avoid repeating greetings and stay coherent.
    history_messages = []
    for item in (history or [])[-_HISTORY_LIMIT:]:
        if not isinstance(item, dict):
            continue
        role, content = item.get("role"), item.get("content")
        if role not in ("user", "assistant") or not content:
            continue
        text = str(content)[:1200]
        if role == "assistant" and text.startswith(_FALLBACK_PREFIX):
            continue
        history_messages.append({"role": role, "content": text})
    has_prior_dialogue = any(item["role"] == "assistant" for item in history_messages)
    system_prompt = await _build_system_prompt(user_message, has_history=has_prior_dialogue)
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history_messages)
    if (not history_messages or history_messages[-1]["role"] != "user"
            or history_messages[-1]["content"] != user_message):
        messages.append({"role": "user", "content": user_message})

    if not settings.GROQ_API_KEY:
        logger.error("GROQ_API_KEY is not configured")
        return "Извините, AI-помощник временно недоступен. Позвоните нам: +7 702 718 2233"

    logger.info("Waiting for the AI queue")
    async with _ai_queue:
        logger.info("Calling Groq model %s", settings.GROQ_MODEL)
        result = await _call_groq(messages)
    if result:
        logger.info("Groq request success")
        return result
    logger.error("Groq request failed")
    return "Извините, AI-помощник временно недоступен. Попробуйте позже или позвоните +7 702 718 2233"
