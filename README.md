# GREENTIME — школа практического вождения

Сайт, Telegram-боты, админ-панель и мобильное приложение школы практического
вождения в Павлодаре.

**Слоган:** Решил - действуй

## Бренд

| Роль | Цвет | HEX |
|------|------|-----|
| Основной (развитие, движение) | зелёный | `#00C853` |
| Акцент (ясность, свобода) | голубой | `#00D1FF` |
| Фон и текст на тёмном | белый | `#FFFFFF` |
| Базовый тёмный фон | тёмно-синий | `#06131F` |

Логотип: `frontend/brand/greentime-logo.png` (512×512, прозрачный фон),
иконки `favicon-32/64/180.png`. Мастер-файл в исходном разрешении —
`brand-source/`.

Шрифт интерфейса — Inter (сайт, админка), Roboto (мобильное приложение).

## Что где лежит

```
frontend/          лендинг (чистые HTML/CSS/JS), его отдаёт nginx
admin/frontend/    админ-панель (Vite SPA)
admin/backend/     API админ-панели (FastAPI)
backend/           API сайта + Telegram-боты (FastAPI, aiogram)
mobile/nomad_mobile/  приложение Android (Flutter)
deploy/            docker compose, Caddyfile, конфиги nginx
brand-source/      исходники логотипа
```

## Цены и услуги

- Практическое вождение — **7 000 ₸ / час**, площадка Лесная 5.
- Пробный экзамен сейчас **не проводится**. Услуга скрыта в интерфейсах,
  но модель данных и старые записи сохранены: включить обратно можно флагом
  `EXAM_ENABLED=true` в `deploy/.env` (по умолчанию `false`).
- Рабочие часы в интерфейсе: 8:00 – 23:00, без выходных.

## Деплой (VPS)

Сервер: `ubuntu@57.129.153.111` (`ssh nomad-vps`), проект в `/opt/nomad`.

```
https://greentime.57-129-153-111.sslip.io        сайт
https://admin.greentime.57-129-153-111.sslip.io  админка
```

Старые адреса `nomad.*` оставлены вторыми именами в `deploy/Caddyfile`,
чтобы ранее установленные приложения продолжали работать.

Обновление на сервере:

```bash
cd /opt/nomad/deploy
docker compose up -d --build
docker compose exec -T site-api python seed_faq.py   # после правки FAQ
```

Compose-проект называется `greentime`, но тома объявлены внешними и носят
прежние имена (`nomad_postgres_data` и т.д.) — база и сертификаты при
переименовании проекта не пересоздаются.

## Переменные окружения

`deploy/.env` (на сервере, в git не попадает):

```
POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD
DATABASE_URL=postgresql+asyncpg://...
SECRET_KEY, ADMIN_USERNAME, ADMIN_PASSWORD
TELEGRAM_BOT_TOKEN, INSTRUCTOR_BOT_TOKEN, REPORT_BOT_TOKEN, REPORT_OWNER_PHONE
ONESIGNAL_APP_ID, ONESIGNAL_REST_API_KEY
GROQ_API_KEY, GROQ_BASE_URL, GROQ_MODEL
EXAM_ENABLED=false        # пробный экзамен выключен
MOBILE_BOOKING_ENABLED    # запись из приложения
PUBLIC_SITE_URL           # ссылка в реферальной программе
```

## Три копии проекта

1. VPS `/opt/nomad` — рабочая.
2. GitHub `greglatishka82-ctrl/nomad` — резервная.
3. Локальная папка разработчика — через `git pull`.

Порядок: правим на VPS → `git commit && git push` → локально `git pull`.
