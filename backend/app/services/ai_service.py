import logging
from typing import Optional

import httpx
from sqlalchemy import select

from app.config import settings
from app.database import async_session
from app.models.models import Instructor

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_TEMPLATE = """Ты — ИИ-помощник АвтоПрактик NOMAD. Ты общаешься как лучший друг, который хочет помочь — позитивно, уверенно, с эмодзи там где уместно.

━━━━━━━━━━━━━━━━━━━━━━━━━
ДАННЫЕ ОРГАНИЗАЦИИ
━━━━━━━━━━━━━━━━━━━━━━━━━
Название: АвтоПрактик NOMAD
Тип: Центр практики вождения (НЕ автошкола — теория здесь не преподаётся, только практическое вождение)
Город: Павлодар, Казахстан
Телефон: +7 702 718 2233
Категория: ТОЛЬКО B (других категорий нет)
Режим работы: 9:00 до 19:00, без выходных
Автомобиль: Chevrolet Cobalt (седан, двойное управление)
Все цены в тенге (₸)

━━━━━━━━━━━━━━━━━━━━━━━━━
ПЛОЩАДКИ
━━━━━━━━━━━━━━━━━━━━━━━━━
• Циолковского 30 — площадка для занятий по вождению и пробного экзамена
  Занятия по вождению, длительность 60 минут, цена 10 000 ₸/час
  Пробный экзамен, 1 круг = 20 минут = 5 000 ₸
  Большая площадка, расширенная разметка, сложные элементы, электронная система

━━━━━━━━━━━━━━━━━━━━━━━━━
РАСПИСАНИЕ
━━━━━━━━━━━━━━━━━━━━━━━━━
Занятия начинаются в полный час: 09:00, 10:00, 11:00, 12:00, 13:00, 14:00, 15:00, 16:00, 17:00, 18:00
Последний слот для записи: 18:00

━━━━━━━━━━━━━━━━━━━━━━━━━
ИНСТРУКТОРЫ
━━━━━━━━━━━━━━━━━━━━━━━━━
ВСЕГО ИНСТРУКТОРОВ: {instructors_count}

{instructors_info}

ВАЖНО: Если спрашивают "сколько инструкторов" — отвечай "{instructors_count}", НЕ считай список вручную!

━━━━━━━━━━━━━━━━━━━━━━━━━
КАК ЗАПИСАТЬСЯ
━━━━━━━━━━━━━━━━━━━━━━━━━
Запись только через Telegram-бот: https://t.me/nomadrive_bot или по телефону: +7 702 718 2233
Через чат на сайте записаться НЕЛЬЗЯ — бот в чате только отвечает на вопросы.

Когда клиент хочет записаться, скажи так:
"К сожалению, я не могу записать тебя напрямую через чат. 😕 Чтобы забронировать занятие, открой Telegram-бота https://t.me/nomadrive_bot или позвони по телефону +7 702 718 2233. Там ты сможешь выбрать дату, время и инструктора. 🚗✨"

ВАЖНО: Давай ссылку на бота только ОДИН РАЗ в своем ответе, не дублируй её!

━━━━━━━━━━━━━━━━━━━━━━━━━
СТРОГИЕ ЗАПРЕТЫ — НИКОГДА НЕ ДЕЛАЙ ЭТО
━━━━━━━━━━━━━━━━━━━━━━━━━
1. НЕ придумывай цены, скидки, рассрочки, акции — только те что указаны выше
2. НЕ обещай другие категории (A, C, D, E) — только B
3. НЕ называй других инструкторов кроме тех что указаны в разделе ИНСТРУКТОРЫ
4. НЕ выдумывай данные о записях клиента
5. НЕ отвечай на темы не связанные с АвтоПрактик (погода, политика, etc.) — вежливо возвращай к теме
6. НЕ записывай клиентов через чат — направляй в Telegram или по телефону
7. НЕ СЧИТАЙ инструкторов вручную — используй точное число {instructors_count}!
8. НЕ называй нас "автошкола" — мы АвтоПрактик NOMAD, центр практики вождения. Если клиент спросит "это автошкола?" — объясни: "Мы не обычная автошкола 🚗 У нас нет теоретических занятий — только практическое вождение. Мы АвтоПрактик NOMAD — центр практики вождения в Павлодаре."

━━━━━━━━━━━━━━━━━━━━━━━━━
ЕСЛИ НЕ ЗНАЕШЬ ОТВЕТА
━━━━━━━━━━━━━━━━━━━━━━━━━
Если вопрос связан с АвтоПрактик, но точного ответа нет в твоих данных — честно скажи что не знаешь и порекомендуй уточнить по телефону: +7 702 718 2233. Лучше направить к живому человеку, чем выдумать неверный ответ.

━━━━━━━━━━━━━━━━━━━━━━━━━
ПОВЕДЕНИЕ
━━━━━━━━━━━━━━━━━━━━━━━━━
• Если клиент грубит или матерится — всегда отвечай вежливо, никогда не груби в ответ
• Общайся на том языке, на котором пишет клиент (русский или казахский)
• Используй эмодзи там где уместно, но не переусердствуй"""


async def _get_instructors_info() -> tuple[str, int]:
    """
    Загружает информацию об активных инструкторах из базы данных
    и форматирует её для промпта.
    Возвращает (текст_с_инструкторами, количество_инструкторов)
    """
    try:
        async with async_session() as session:
            result = await session.execute(
                select(Instructor).where(Instructor.is_active == True)
            )
            instructors = result.scalars().all()
            
            if not instructors:
                return "В данный момент инструкторы не указаны. Для уточнения звоните: +7 702 718 2233", 0
            
            instructor_lines = []
            for instructor in instructors:
                # Форматируем тип КПП
                if instructor.transmission == "both":
                    transmission_text = "АКПП и МКПП"
                elif instructor.transmission == "automatic":
                    transmission_text = "только АКПП"
                elif instructor.transmission == "manual":
                    transmission_text = "только МКПП"
                else:
                    transmission_text = "АКПП и МКПП"
                
                # Основная информация
                line = f"• {instructor.name} — {transmission_text}."
                
                # Добавляем описание если есть
                if instructor.description:
                    line += f" {instructor.description}"
                
                instructor_lines.append(line)
            
            return "\n".join(instructor_lines), len(instructors)
    
    except Exception as e:
        logger.error(f"Ошибка при загрузке инструкторов: {e}")
        return "В данный момент информация об инструкторах недоступна. Для уточнения звоните: +7 702 718 2233", 0


async def _build_system_prompt() -> str:
    """
    Строит полный системный промпт с динамическим списком инструкторов.
    """
    instructors_info, instructors_count = await _get_instructors_info()
    return SYSTEM_PROMPT_TEMPLATE.format(
        instructors_info=instructors_info,
        instructors_count=instructors_count
    )



async def _call_provider(
    base_url: str, api_key: str, model: str, messages: list, timeout: float = 15.0
) -> Optional[str]:
    """
    Вызывает AI провайдер с заданным таймаутом.
    Возвращает ответ или None в случае ошибки.
    """
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": messages,
                    "max_tokens": 1024,
                    "temperature": 0.3,
                },
            )
            if resp.status_code != 200:
                logger.warning(f"AI provider returned {resp.status_code}: {resp.text[:200]}")
                return None
            data = resp.json()
            return data["choices"][0]["message"]["content"]
    except httpx.TimeoutException:
        logger.warning(f"AI provider timeout after {timeout}s")
        return None
    except Exception as e:
        logger.warning(f"AI provider error: {e}")
        return None


async def chat_completion(user_message: str, history: Optional[list] = None) -> str:
    """
    Получает ответ от AI модели с фоллбэком на второй провайдер.
    Таймаут 15 секунд на каждый провайдер.
    """
    system_prompt = await _build_system_prompt()
    messages = [{"role": "system", "content": system_prompt}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    # Проверяем что API ключи настроены
    if not settings.GROQ_API_KEY and not settings.NVIDIA_API_KEY:
        logger.error("No AI API keys configured")
        return "Извините, AI-помощник временно недоступен. Позвоните нам: +7 702 718 2233"

    # Пробуем Groq
    if settings.GROQ_API_KEY:
        logger.info("Calling Groq API...")
        result = await _call_provider(
            settings.GROQ_BASE_URL, settings.GROQ_API_KEY, settings.GROQ_MODEL, messages, timeout=15.0
        )
        if result:
            logger.info("Groq API success")
            return result
        logger.warning("Groq API failed")

    # Фоллбэк на NVIDIA
    if settings.NVIDIA_API_KEY:
        logger.info("Falling back to NVIDIA API...")
        result = await _call_provider(
            settings.NVIDIA_BASE_URL, settings.NVIDIA_API_KEY, settings.NVIDIA_MODEL, messages, timeout=15.0
        )
        if result:
            logger.info("NVIDIA API success")
            return result
        logger.warning("NVIDIA API failed")

    logger.error("All AI providers failed")
    return "Извините, AI-помощник временно недоступен. Попробуйте позже или позвоните +7 702 718 2233"
