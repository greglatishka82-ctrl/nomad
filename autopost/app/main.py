"""GREENTIME — автопостинг в Telegram-канал @greentime_pvl и в Instagram (Buffer).

Работает на существующих таблицах autopost_* в базе GREENTIME.

Расписание (Asia/Almaty):
  пн–пт публикация в 10:00, сб–вс в 12:00;
  превью владельцу уходит за час до публикации;
  нет решения к моменту публикации — пост одобряется автоматически;
  «Отклонить» — пост и картинка удаляются с сервера, сразу предлагается следующий.
"""

import asyncio
import logging
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp
import asyncpg
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardRemove,
)
from PIL import Image

# ─── Конфигурация ────────────────────────────────────────────────────────────

TOKEN = os.environ.get("AUTOPOST_BOT_TOKEN", "").strip()
CHANNEL = os.environ.get("AUTOPOST_CHANNEL", "@greentime_pvl").strip()
OWNER_ENV = int(os.environ.get("AUTOPOST_OWNER_ID") or 0)
TZ = ZoneInfo(os.environ.get("AUTOPOST_TIMEZONE", "Asia/Almaty"))

DSN = os.environ.get("POSTGRES_DSN", "").strip()

INBOX = Path(os.environ.get("INBOX_DIR", "/data/inbox"))
MEDIA = Path(os.environ.get("MEDIA_DIR", "/data/media"))
PROCESSED = Path(os.environ.get("PROCESSED_DIR", "/data/processed"))
PUBLIC_MEDIA = Path(os.environ.get("PUBLIC_MEDIA_DIR", "/public/ig"))
PUBLIC_URL = os.environ.get("PUBLIC_MEDIA_URL", "").rstrip("/")

BUFFER_KEY = os.environ.get("BUFFER_API_KEY", "").strip()
BUFFER_CHANNEL = os.environ.get("BUFFER_CHANNEL_ID", "").strip()
IG_ENABLED = os.environ.get("IG_ENABLED", "true").lower() in ("1", "true", "yes")

PREVIEW_LEAD_MINUTES = int(os.environ.get("PREVIEW_LEAD_MINUTES", 60))
WEEKDAY_PUBLISH_HOUR = int(os.environ.get("WEEKDAY_PUBLISH_HOUR", 10))
WEEKEND_PUBLISH_HOUR = int(os.environ.get("WEEKEND_PUBLISH_HOUR", 12))

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("autopost")

pool: asyncpg.Pool | None = None


# ─── База ────────────────────────────────────────────────────────────────────

async def init_db() -> None:
    global pool
    pool = await asyncpg.create_pool(DSN, min_size=1, max_size=5)
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO autopost_state (id) VALUES (1) ON CONFLICT (id) DO NOTHING")
    log.info("База подключена")


async def get_state():
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM autopost_state WHERE id = 1")


async def set_state(**fields) -> None:
    keys = list(fields.keys())
    sets = ", ".join(f"{k} = ${i + 1}" for i, k in enumerate(keys))
    async with pool.acquire() as conn:
        await conn.execute(f"UPDATE autopost_state SET {sets} WHERE id = 1", *[fields[k] for k in keys])


async def owner_chat_id() -> int:
    if OWNER_ENV:
        return OWNER_ENV
    async with pool.acquire() as conn:
        value = await conn.fetchval("SELECT value FROM autopost_settings WHERE key = 'owner_id'")
    if value:
        try:
            return int(str(value).strip())
        except ValueError:
            pass
    state = await get_state()
    return int(state["owner_chat_id"] or 0) if state else 0


async def is_paused() -> bool:
    """Пауза автопостинга: пока включена, превью и публикации не отправляются."""
    async with pool.acquire() as conn:
        value = await conn.fetchval("SELECT value FROM autopost_settings WHERE key = 'paused'")
    return str(value or "").strip() in ("1", "true", "yes")


async def pick_next_post():
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT * FROM autopost_posts WHERE status = 'queued' ORDER BY post_number ASC LIMIT 1"
        )


# ─── Картинки ────────────────────────────────────────────────────────────────

def to_jpeg(src: Path, dst: Path, square: bool = False, size: int = 1080) -> None:
    with Image.open(src) as im:
        im = im.convert("RGB")
        if square:
            side = max(im.size)
            canvas = Image.new("RGB", (side, side), (0, 0, 0))
            canvas.paste(im, ((side - im.size[0]) // 2, (side - im.size[1]) // 2))
            im = canvas.resize((size, size), Image.LANCZOS)
        im.save(dst, "JPEG", quality=92, optimize=True)


# ─── Импорт из inbox ─────────────────────────────────────────────────────────

async def import_inbox() -> None:
    INBOX.mkdir(parents=True, exist_ok=True)
    MEDIA.mkdir(parents=True, exist_ok=True)
    PROCESSED.mkdir(parents=True, exist_ok=True)

    for txt in sorted(INBOX.glob("*.txt")):
        stem = txt.stem
        if not stem.isdigit():
            log.warning("Импорт: %s — имя файла не число, пропуск", txt.name)
            continue

        number = int(stem)
        images = [p for p in INBOX.iterdir() if p.is_file() and p.stem == stem and p.suffix.lower() in IMAGE_EXT]

        if not images:
            log.error("Импорт: для %s нет картинки", txt.name)
            continue
        if len(images) > 1:
            log.error("Импорт: пост №%s — несколько картинок, конфликт", number)
            continue

        text = ""
        for enc in ("utf-8", "utf-8-sig", "cp1251"):
            try:
                text = txt.read_text(encoding=enc).strip()
                break
            except UnicodeDecodeError:
                continue
        if not text:
            log.error("Импорт: пустой текст в %s", txt.name)
            continue

        async with pool.acquire() as conn:
            exists = await conn.fetchval("SELECT id FROM autopost_posts WHERE post_number = $1", number)
        if exists:
            log.warning("Импорт: пост №%s уже есть — пропуск", number)
            shutil.move(str(txt), PROCESSED / txt.name)
            shutil.move(str(images[0]), PROCESSED / images[0].name)
            continue

        target = MEDIA / f"{number}.jpg"
        try:
            to_jpeg(images[0], target)
        except Exception as exc:  # noqa: BLE001
            log.error("Импорт: картинка %s не обработана — %s", images[0].name, exc)
            continue

        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO autopost_posts (post_number, content, image_path, status) "
                "VALUES ($1, $2, $3, 'queued')",
                number, text, str(target),
            )
        shutil.move(str(txt), PROCESSED / txt.name)
        shutil.move(str(images[0]), PROCESSED / images[0].name)
        log.info("Импорт: пост №%s в очереди", number)


# ─── Публикация ──────────────────────────────────────────────────────────────

async def publish_telegram(bot: Bot, post) -> int:
    image = Path(post["image_path"]) if post["image_path"] else None
    caption = post["content"] or ""
    if image and image.exists():
        msg = await bot.send_photo(CHANNEL, FSInputFile(str(image)), caption=caption[:1024])
    else:
        msg = await bot.send_message(CHANNEL, caption[:4096])
    return msg.message_id


async def publish_instagram(post) -> bool:
    if not IG_ENABLED or not BUFFER_KEY or not BUFFER_CHANNEL:
        log.info("Instagram: не настроен — пропуск")
        return False

    image = Path(post["image_path"]) if post["image_path"] else None
    if not image or not image.exists() or not PUBLIC_URL:
        log.warning("Instagram: нет картинки или публичного URL — пропуск")
        return False

    PUBLIC_MEDIA.mkdir(parents=True, exist_ok=True)
    public_file = PUBLIC_MEDIA / f"post-{post['post_number']}.jpg"
    to_jpeg(image, public_file, square=True)
    url = f"{PUBLIC_URL}/{public_file.name}"

    text = (post["content"] or "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    query = (
        'mutation { createPost(input: { text: "%s", channelId: "%s", schedulingType: automatic, '
        'mode: shareNow, assets: [{ image: { url: "%s" } }], '
        'metadata: { instagram: { type: post, shouldShareToFeed: true } } }) { '
        "... on PostActionSuccess { post { id status } } ... on MutationError { message } } }"
    ) % (text, BUFFER_CHANNEL, url)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.buffer.com",
                json={"query": query},
                headers={"Authorization": f"Bearer {BUFFER_KEY}"},
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                data = await resp.json()
        result = (data.get("data") or {}).get("createPost") or {}
        if "message" in result:
            log.error("Instagram: %s", result["message"])
            return False
        log.info("Instagram: опубликовано (%s)", (result.get("post") or {}).get("id"))
        return True
    except Exception as exc:  # noqa: BLE001
        log.error("Instagram: сбой — %s", exc)
        return False


async def publish_current(bot: Bot) -> None:
    state = await get_state()
    post_id = state["current_post_id"]
    if not post_id:
        return

    async with pool.acquire() as conn:
        post = await conn.fetchrow("SELECT * FROM autopost_posts WHERE id = $1", post_id)
        if not post or post["status"] not in ("approved", "publishing"):
            return
        await conn.execute(
            "UPDATE autopost_posts SET status = 'publishing', updated_at = now() WHERE id = $1", post_id
        )

    message_id = None
    for attempt in range(1, 4):
        try:
            message_id = await publish_telegram(bot, post)
            break
        except Exception as exc:  # noqa: BLE001
            log.error("Публикация в Telegram, попытка %s: %s", attempt, exc)
            await asyncio.sleep(30)

    if message_id is None:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE autopost_posts SET status = 'failed', attempts = attempts + 1, "
                "last_error = 'telegram send failed', updated_at = now() WHERE id = $1",
                post_id,
            )
        log.error("Пост №%s не опубликован, статус failed", post["post_number"])
        return

    await publish_instagram(post)

    image = Path(post["image_path"]) if post["image_path"] else None
    if image and image.exists():
        try:
            image.unlink()
        except OSError as exc:
            log.warning("Не удалил %s: %s", image, exc)
    for leftover in PUBLIC_MEDIA.glob(f"post-{post['post_number']}.*"):
        try:
            leftover.unlink()
        except OSError:
            pass

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE autopost_posts SET status = 'published', telegram_message_id = $2, "
            "published_at = now(), content = NULL, image_path = NULL, updated_at = now() WHERE id = $1",
            post_id, message_id,
        )
    await set_state(current_post_id=None, last_published_date=datetime.now(TZ).date())
    log.info("Пост №%s опубликован в %s (message_id=%s)", post["post_number"], CHANNEL, message_id)


# ─── Бот ─────────────────────────────────────────────────────────────────────

router = Router()


def preview_kb(post_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve:{post_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject:{post_id}"),
    ]])


async def send_preview(bot: Bot, post, chat_id: int | None = None) -> bool:
    owner = chat_id or await owner_chat_id()
    if not owner:
        log.warning("Владелец не задан — превью отправить некому")
        return False

    image = Path(post["image_path"]) if post["image_path"] else None
    caption = (post["content"] or "")[:1024]
    if image and image.exists():
        msg = await bot.send_photo(owner, FSInputFile(str(image)), caption=caption, reply_markup=preview_kb(post["id"]))
    else:
        msg = await bot.send_message(owner, caption, reply_markup=preview_kb(post["id"]))

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE autopost_posts SET status = 'pending_approval', preview_chat_id = $2, "
            "preview_message_id = $3, updated_at = now() WHERE id = $1",
            post["id"], owner, msg.message_id,
        )
    await set_state(current_post_id=post["id"], last_preview_date=datetime.now(TZ).date())
    log.info("Превью поста №%s отправлено в %s", post["post_number"], owner)
    return True


@router.message(F.text == "/start")
async def cmd_start(message: Message):
    owner = await owner_chat_id()
    role = "владелец" if owner and message.from_user.id == owner else "не владелец"
    log.info("/start от chat_id=%s (@%s)", message.from_user.id, message.from_user.username)
    await message.answer(
        f"Пост-БОТ GREENTIME\n\nВаш chat_id: {message.from_user.id}\nРоль: {role}",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(F.text == "/status")
async def cmd_status(message: Message):
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT status, count(*) AS c FROM autopost_posts GROUP BY status")
    counts = {r["status"]: r["c"] for r in rows}
    state = await get_state()
    current = ""
    if state and state["current_post_id"]:
        async with pool.acquire() as conn:
            post = await conn.fetchrow(
                "SELECT post_number, status FROM autopost_posts WHERE id = $1", state["current_post_id"]
            )
        if post:
            current = f"\nВ работе: №{post['post_number']} ({post['status']})"
    await message.answer(
        "Очередь: queued=%s, pending=%s, approved=%s, published=%s, failed=%s%s"
        % (
            counts.get("queued", 0), counts.get("pending_approval", 0), counts.get("approved", 0),
            counts.get("published", 0), counts.get("failed", 0), current,
        )
    )


@router.message(F.text == "/preview")
async def cmd_preview(message: Message, bot: Bot):
    """Тестовая отправка следующего поста — без ожидания расписания."""
    log.info("/preview от chat_id=%s", message.from_user.id)
    state = await get_state()
    if state["current_post_id"]:
        async with pool.acquire() as conn:
            cur = await conn.fetchrow(
                "SELECT post_number FROM autopost_posts WHERE id = $1", state["current_post_id"]
            )
        await message.answer(f"Сейчас на визировании пост №{cur['post_number'] if cur else '?'}.")
        return

    post = await pick_next_post()
    if not post:
        await message.answer("Очередь пуста: нет постов в статусе queued.")
        return
    await send_preview(bot, post, chat_id=message.from_user.id)


@router.callback_query(F.data.startswith("approve:") | F.data.startswith("reject:"))
async def on_decision(callback: CallbackQuery, bot: Bot):
    action, raw_id = callback.data.split(":")
    post_id = int(raw_id)

    async with pool.acquire() as conn:
        post = await conn.fetchrow("SELECT * FROM autopost_posts WHERE id = $1", post_id)

    if not post:
        await callback.answer("Пост уже обработан")
        return
    if post["status"] != "pending_approval":
        await callback.answer("Решение уже принято")
        return

    if action == "approve":
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE autopost_posts SET status = 'approved', approved_at = now(), updated_at = now() "
                "WHERE id = $1", post_id,
            )
        try:
            await callback.message.edit_caption(
                caption=f"{(post['content'] or '')[:900]}\n\n✅ Одобрено", reply_markup=None
            )
        except Exception:  # noqa: BLE001
            await callback.message.edit_reply_markup(reply_markup=None)
        await callback.answer("Одобрено")
        log.info("Пост №%s одобрен", post["post_number"])
        return

    image = Path(post["image_path"]) if post["image_path"] else None
    if image and image.exists():
        try:
            image.unlink()
        except OSError:
            pass
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM autopost_posts WHERE id = $1", post_id)
    await set_state(current_post_id=None)
    try:
        await callback.message.edit_caption(caption="❌ Отклонено", reply_markup=None)
    except Exception:  # noqa: BLE001
        await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Отклонено")
    log.info("Пост №%s отклонён и удалён", post["post_number"])

    nxt = await pick_next_post()
    if nxt:
        await send_preview(bot, nxt)
    else:
        await callback.message.answer("Очередь пуста.")


# ─── Планировщик ─────────────────────────────────────────────────────────────

async def scheduler_loop(bot: Bot) -> None:
    while True:
        try:
            if await is_paused():
                await asyncio.sleep(60)
                continue
            now = datetime.now(TZ)
            publish_hour = WEEKDAY_PUBLISH_HOUR if now.weekday() < 5 else WEEKEND_PUBLISH_HOUR
            publish_at = now.replace(hour=publish_hour, minute=0, second=0, microsecond=0)
            preview_at = publish_at - timedelta(minutes=PREVIEW_LEAD_MINUTES)
            # Публикуем только внутри окна дня (до publish_at + 3 часа). Иначе тестовое
            # превью, отправленное вечером, сразу улетело бы в канал.
            in_window = now <= publish_at + timedelta(hours=3)

            state = await get_state()
            current_id = state["current_post_id"]

            if current_id is None and state["last_preview_date"] != now.date() and now >= preview_at:
                post = await pick_next_post()
                if post:
                    await send_preview(bot, post)
                else:
                    log.info("Очередь пуста — превью не отправлено")

            if current_id:
                async with pool.acquire() as conn:
                    post = await conn.fetchrow("SELECT * FROM autopost_posts WHERE id = $1", current_id)

                if not post:
                    await set_state(current_post_id=None)
                elif in_window:
                    if post["status"] == "pending_approval" and now >= publish_at:
                        async with pool.acquire() as conn:
                            await conn.execute(
                                "UPDATE autopost_posts SET status = 'approved', approved_at = now(), "
                                "updated_at = now() WHERE id = $1", current_id,
                            )
                        log.info("Пост №%s одобрен автоматически", post["post_number"])
                    if post["status"] in ("approved", "publishing") and now >= publish_at:
                        await publish_current(bot)
        except Exception as exc:  # noqa: BLE001
            log.exception("Ошибка планировщика: %s", exc)
        await asyncio.sleep(20)


async def importer_loop() -> None:
    while True:
        try:
            await import_inbox()
        except Exception as exc:  # noqa: BLE001
            log.exception("Ошибка импорта: %s", exc)
        await asyncio.sleep(30)


async def main() -> None:
    if not TOKEN or not DSN:
        raise SystemExit("Не заданы AUTOPOST_BOT_TOKEN или POSTGRES_DSN")

    for path in (INBOX, MEDIA, PROCESSED, PUBLIC_MEDIA):
        path.mkdir(parents=True, exist_ok=True)

    await init_db()

    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)

    log.info("Автопостинг запущен: канал %s, владелец %s", CHANNEL, await owner_chat_id() or "не задан")

    await asyncio.gather(dp.start_polling(bot), scheduler_loop(bot), importer_loop())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit) as exc:
        log.info("Остановка: %s", exc)
