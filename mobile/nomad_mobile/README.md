# NOMAD Mobile — Flutter APK

Мобильное приложение академии вождения NOMAD (Павлодар).

---

## Требования

- Flutter 3.44+ (`E:\PRO\flutter\flutter`)
- Android Studio с установленными:
  - Android SDK (API 34)
  - Android SDK Command-line Tools
  - Android SDK Build-Tools
- Java 21 (встроенная JBR из Android Studio)

---

## Первоначальная настройка (один раз)

### 1. Настроить пути

```bash
flutter config --android-sdk "C:\Users\knight\AppData\Local\Android\Sdk"
flutter config --jdk-dir "C:\Program Files\Android\Android Studio\jbr"
```

### 2. Принять лицензии Android

```bash
flutter doctor --android-licenses
# Нажимать y на все вопросы
```

### 3. Установить зависимости

```bash
cd e:\work\Жека\NOMAD\mobile\nomad_mobile
flutter pub get
```

### 4. Подключить Firebase (обязательно!)

1. Зайди на https://console.firebase.google.com/
2. Создай проект → добавь Android-приложение с package name: `com.nomad.nomad_mobile`
3. Скачай `google-services.json`
4. Замени файл: `android\app\google-services.json`

### 5. Указать URL бэкенда

Открой файл `lib\core\api\api_client.dart` и замените:
```dart
const String kBaseUrl = 'https://your-backend.onrender.com';
```
на реальный URL бэкенда, например:
```dart
const String kBaseUrl = 'https://nomad-backend.onrender.com';
```

---

## Сборка APK

### Debug APK (для тестирования)

```bash
flutter build apk --debug
```

Файл: `build\app\outputs\flutter-apk\app-debug.apk`

### Release APK (для раздачи пользователям)

```bash
flutter build apk --release --split-per-abi
```

Файлы в `build\app\outputs\flutter-apk\`:
- `app-arm64-v8a-release.apk` — для современных телефонов (рекомендуется)
- `app-armeabi-v7a-release.apk` — для старых телефонов
- `app-x86_64-release.apk` — для эмуляторов

> Для большинства пользователей достаточно `app-arm64-v8a-release.apk`

---

## Обновление версии

В `pubspec.yaml` измените строку:
```yaml
version: 1.0.0+1
```
Формат: `версия+buildNumber`. Например: `1.0.1+2`

---

## Структура проекта

```
lib/
├── main.dart                  # точка входа
├── app.dart                   # MaterialApp + FCM init
├── core/
│   ├── api/api_client.dart    # Dio HTTP клиент (JWT interceptor)
│   ├── auth/                  # авторизация, хранение токенов, роутер
│   ├── theme/app_theme.dart   # цвета и стили
│   └── utils/formatters.dart  # форматирование дат, цен
├── features/
│   ├── auth/                  # вход, регистрация, онбординг
│   ├── home/                  # главный экран
│   ├── booking/               # wizard записи (5 шагов)
│   ├── my_bookings/           # список и детали записей
│   ├── profile/               # личный кабинет
│   ├── packages/              # пакеты занятий
│   ├── certificates/          # сертификаты
│   ├── referral/              # реферальная программа
│   ├── support_chat/          # чат с поддержкой
│   ├── ai_chat/               # ИИ-помощник
│   ├── instructors/           # инструкторы + FAQ + контакты
│   └── notifications/         # настройки уведомлений
└── shared/
    ├── models/models.dart     # DTO модели
    └── widgets/               # общие виджеты
```

---

## Публикация APK на сайте

После сборки скопируй APK в папку `frontend/public/`:
```
e:\work\Жека\NOMAD\frontend\public\nomad.apk
```

Ссылка для скачивания: `https://nomadpvl.kz/nomad.apk`

---

## Деплой бэкенда (новые мобильные эндпоинты)

Новые зависимости в `backend/requirements.txt`:
```
PyJWT==2.9.0
```

Новые переменные окружения (добавить в Render):
```
SMTP_HOST=smtp.gmail.com
SMTP_PORT=465
SMTP_USER=your@gmail.com
SMTP_PASSWORD=your_app_password
```

> Для Gmail нужен App Password: Google Account → Security → 2FA → App passwords
