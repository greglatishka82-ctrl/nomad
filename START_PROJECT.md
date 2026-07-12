# ЗАПУСК ПРОЕКТА NOMAD

## Структура портов
| Сервис | Папка | Порт |
|---|---|---|
| Сайт (Frontend) | `frontend/` | http://localhost:3000 |
| Админка (Admin Frontend) | `admin/frontend/` | http://localhost:3001 |
| Бэкенд (Backend API) | `backend/` | http://localhost:8000 |
| Админ-бэкенд (Admin API) | `admin/backend/` | http://localhost:8001 |
| Мобильное приложение (Flutter) | `mobile/nomad_mobile/` | http://localhost:3003 |

---

## Шаг 1 — Установить зависимости (один раз)

```powershell
# Из корня проекта E:\work\NOMADPROJECT\NOMAD\
.venv\Scripts\python.exe -m pip install greenlet
.venv\Scripts\python.exe -m pip install "pydantic[email]"
```

---

## Шаг 2 — Запуск (каждый в отдельном терминале)

### Сайт → localhost:3000
```powershell
cd E:\work\NOMADPROJECT\NOMAD\frontend
python -m http.server 3000
```

### Админка → localhost:3001
```powershell
cd E:\work\NOMADPROJECT\NOMAD\admin\frontend
python -m http.server 3001
```

### Бэкенд → localhost:8000
```powershell
cd E:\work\NOMADPROJECT\NOMAD\backend
E:\work\NOMADPROJECT\NOMAD\.venv\Scripts\python.exe run.py
```

### Админ-бэкенд → localhost:8001
```powershell
cd E:\work\NOMADPROJECT\NOMAD\admin\backend
E:\work\NOMADPROJECT\NOMAD\.venv\Scripts\python.exe run.py
```

### Мобильное приложение → localhost:3003

**Первый раз (сборка ~3 минуты):**
```powershell
cd E:\work\NOMADPROJECT\NOMAD\mobile\nomad_mobile
flutter build web
python -m http.server 3003 --directory build\web
```

**После изменений в коде — пересобрать:**
```powershell
cd E:\work\NOMADPROJECT\NOMAD\mobile\nomad_mobile
flutter build web
# сервер на 3003 уже работает, просто обновить браузер
```

---

## Важно
- `.venv` находится в **корне проекта** `E:\work\NOMADPROJECT\NOMAD\.venv\`
- Использовать **PowerShell 7** (`pwsh`), не старый PowerShell
- `flutter build web` первый раз медленный (2-3 мин) — это норма для Flutter
