import logging
from typing import Optional

import httpx
from sqlalchemy import select

from app.config import settings
from app.database import async_session
from app.models.models import Instructor

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_TEMPLATE = """Ты — ИИ-помощник Академии вождения NOMAD. Ты общаешься как лучший друг, который хочет помочь — позитивно, уверенно, с эмодзи там где уместно.

━━━━━━━━━━━━━━━━━━━━━━━━━
ДАННЫЕ АВТОШКОЛЫ
━━━━━━━━━━━━━━━━━━━━━━━━━
Название: Академия вождения Nomad
Город: Павлодар, Казахстан
Телефон: +7 702 718 2233
Категория: ТОЛЬКО B (других категорий нет)
Режим работы: каждый день с 9:00 до 19:00, без выходных
Автомобиль: Chevrolet Cobalt (седан, двойное управление)
Все цены в тенге (₸)

━━━━━━━━━━━━━━━━━━━━━━━━━
ПЛОЩАДКИ
━━━━━━━━━━━━━━━━━━━━━━━━━
• Циолковского 28/1 — УЧЕБНАЯ площадка
  Занятия по вождению, длительность 60 минут, цена 6 000 ₸/час
  Для начинающих, базовые навыки вождения

• Циолковского 30 — ЭКЗАМЕНАЦИОННАЯ площадка
  Только пробные экзамены, 1 круг = 15 минут = 5 000 ₸
  Большая площадка, расширенная разметка, сложные элементы, электронная система

━━━━━━━━━━━━━━━━━━━━━━━━━
РАСПИСАНИЕ
━━━━━━━━━━━━━━━━━━━━━━━━━
Занятия начинаются в полный час: 09:00, 10:00, 11:00, 12:00, 13:00, 14:00, 15:00, 16:00, 17:00, 18:00
Последний слот для записи: 18:00

━━━━━━━━━━━━━━━━━━━━━━━━━
ИНСТРУКТОРЫ
━━━━━━━━━━━━━━━━━━━━━━━━━
{instructors_info}

━━━━━━━━━━━━━━━━━━━━━━━━━
КАК ЗАПИСАТЬСЯ
━━━━━━━━━━━━━━━━━━━━━━━━━
Запись только через Telegram-бот: @drivepvlbot
Или по телефону: +7 702 718 2233
Через чат на сайте записаться НЕЛЬЗЯ — бот в чате только отвечает на вопросы.

━━━━━━━━━━━━━━━━━━━━━━━━━
СТРОГИЕ ЗАПРЕТЫ — НИКОГДА НЕ ДЕЛАЙ ЭТО
━━━━━━━━━━━━━━━━━━━━━━━━━
1. НЕ придумывай цены, скидки, рассрочки, акции — только те что указаны выше
2. НЕ обещай другие категории (A, C, D, E) — только B
3. НЕ называй других инструкторов кроме тех что указаны в разделе ИНСТРУКТОРЫ
4. НЕ выдумывай данные о записях клиента
5. НЕ отвечай на темы не связанные с автошколой (погода, политика, etc.) — вежливо возвращай к теме
6. НЕ записывай клиентов через чат — направляй в Telegram или по телефону

━━━━━━━━━━━━━━━━━━━━━━━━━
ЕСЛИ НЕ ЗНАЕШЬ ОТВЕТА
━━━━━━━━━━━━━━━━━━━━━━━━━
Если вопрос связан с автошколой, но точного ответа нет в твоих данных — честно скажи что не знаешь и порекомендуй уточнить по телефону: +7 702 718 2233. Лучше направить к живому человеку, чем выдумать неверный ответ.

━━━━━━━━━━━━━━━━━━━━━━━━━
ПОВЕДЕНИЕ
━━━━━━━━━━━━━━━━━━━━━━━━━
• Если клиент грубит или матерится — всегда отвечай вежливо, никогда не груби в ответ
• Общайся на том языке, на котором пишет клиент (русский или казахский)
• Используй эмодзи там где уместно, но не переусердствуй"""


async def _get_instructors_info() -> str:
    """
    Загружает информацию об активных инструкторах из базы данных
    и форматирует её для промпта.
    """
    try:
        async with async_session() as session:
            result = await session.execute(
                select(Instructor).where(Instructor.is_active == True)
            )
            instructors = result.scalars().all()
            
            if not instructors:
                return "В данный момент инструкторы не указаны. Для уточнения звоните: +7 702 718 2233"
            
            instructor_lines = []
            for instructor in instructors:
                # Форматируем тип КПП
                if instructor.transmission.value == "both":
                    transmission_text = "АКПП и МКПП"
                elif instructor.transmission.value == "automatic":
                    transmission_text = "только АКПП"
                elif instructor.transmission.value == "manual":
                    transmission_text = "только МКПП"
                else:
                    transmission_text = "АКПП и МКПП"
                
                # Основная информация
                line = f"• {instructor.name} — {transmission_text}."
                
                # Добавляем описание если есть
                if instructor.description:
                    line += f" {instructor.description}"
                
                instructor_lines.append(line)
            
            return "\n".join(instructor_lines)
    
    except Exception as e:
        logger.error(f"Ошибка при загрузке инструкторов: {e}")
        return "В данный момент информация об инструкторах недоступна. Для уточнения звоните: +7 702 718 2233"


async def _build_system_prompt() -> str:
    """
    Строит полный системный промпт с динамическим списком инструкторов.
    """
    instructors_info = await _get_instructors_info()
    return SYSTEM_PROMPT_TEMPLATE.format(instructors_info=instructors_info)



async def _call_provider(
    base_url: str, api_key: str, model: str, messages: list
) -> Optional[str]:
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
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
    except Exception as e:
        logger.warning(f"AI provider error: {e}")
        return None


async def chat_completion(user_message: str, history: Optional[list] = None) -> str:
    system_prompt = await _build_system_prompt()
    messages = [{"role": "system", "content": system_prompt}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    result = await _call_provider(
        settings.GROQ_BASE_URL, settings.GROQ_API_KEY, settings.GROQ_MODEL, messages
    )
    if result:
        return result

    logger.info("Groq failed, falling back to NVIDIA")
    result = await _call_provider(
        settings.NVIDIA_BASE_URL, settings.NVIDIA_API_KEY, settings.NVIDIA_MODEL, messages
    )
    if result:
        return result

    return "Извините, временно не могу ответить. Попробуйте позже или свяжитесь с нами по телефону +77027182233"
