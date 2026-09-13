# Инструкции по сборке NOMAD Mobile

## Предварительные требования

1. **Flutter SDK 3.44.6+**
2. **Android Studio** или **Android SDK Command-line Tools**
3. **Java JDK 11+** (для keytool)

## Настройка проекта

### 1. Установка зависимостей

```bash
flutter pub get
```

### 2. Создание ключа подписи (первый раз)

Keystore уже настроен в `android/key.properties`. Если нужно создать новый:

```bash
keytool -genkey -v -keystore android/nomad-release-key.keystore \
  -alias nomad -keyalg RSA -keysize 2048 -validity 10000 \
  -storepass nomad123456 -keypass nomad123456 \
  -dname "CN=NOMAD, OU=Dev, O=NOMAD, L=Pavlodar, ST=Pavlodar, C=KZ"
```

**ВАЖНО:** Сохраните `nomad-release-key.keystore` и пароли в безопасном месте!

### 3. Настройка OneSignal

В файле `lib/core/notifications/notification_service.dart` уже настроен OneSignal App ID:
```dart
static const String _oneSignalAppId = '51602607-2b29-467b-ac12-5a7921f05a7e';
```

Если нужно изменить, обновите этот ID в коде.

### 4. Настройка Backend URL

В файле `lib/core/api/api_client.dart` измените URL backend:

```dart
baseUrl: 'https://your-backend-url.com'  // Измените на продакшн URL
```

## Сборка APK

### Debug сборка (для тестирования)

```bash
flutter build apk --debug
```

APK будет в: `build/app/outputs/flutter-apk/app-debug.apk`

### Release сборка (для публикации)

```bash
flutter build apk --release
```

APK будет в: `build/app/outputs/flutter-apk/app-release.apk`

### Оптимизированная сборка с разделением по ABI

Для уменьшения размера APK:

```bash
flutter build apk --release --split-per-abi
```

Создаст три APK:
- `app-armeabi-v7a-release.apk` (ARM 32-bit)
- `app-arm64-v8a-release.apk` (ARM 64-bit) - рекомендуется
- `app-x86_64-release.apk` (x86 64-bit)

Размер каждого ~15-20 МБ вместо ~40 МБ.

## Тестирование

### Запуск всех тестов

```bash
flutter test
```

### Анализ кода

```bash
flutter analyze
```

### Проверка размера APK

```bash
flutter build apk --release --analyze-size
```

## Установка APK на устройство

### Через ADB

```bash
adb install build/app/outputs/flutter-apk/app-release.apk
```

### Через файл

Скопируйте APK на устройство и откройте файл для установки.

## Публикация

### Вариант 1: Прямая ссылка

1. Загрузите APK на сервер или Google Drive
2. Поделитесь ссылкой с пользователями
3. Пользователям нужно разрешить установку из неизвестных источников

### Вариант 2: OneSignal Dashboard

1. Войдите в OneSignal Dashboard
2. Загрузите APK в раздел "Mobile Push"
3. Распространяйте через OneSignal ссылку

### Вариант 3: Google Play Store (будущее)

1. Создайте аккаунт разработчика ($25)
2. Создайте приложение в Play Console
3. Загрузите AAB:
   ```bash
   flutter build appbundle --release
   ```
4. Заполните описание, скриншоты
5. Отправьте на модерацию

## Обновление версии

Отредактируйте `pubspec.yaml`:

```yaml
version: 1.0.1+2  # 1.0.1 - version name, 2 - version code
```

## Troubleshooting

### Ошибка "Android licenses not accepted"

```bash
flutter doctor --android-licenses
```

### Ошибка "No connected devices"

```bash
flutter devices
flutter emulators --launch <emulator-name>
```

### Ошибка при сборке release

```bash
flutter clean
flutter pub get
flutter build apk --release
```

## Чек-лист перед релизом

- [ ] Обновлена версия в `pubspec.yaml`
- [ ] Backend URL изменён на продакшн
- [ ] OneSignal App ID настроен
- [ ] Все тесты проходят (`flutter test`)
- [ ] Код проанализирован (`flutter analyze`)
- [ ] APK протестирован на реальном устройстве
- [ ] Размер APK < 30 МБ
- [ ] Keystore сохранён в безопасном месте

## Контакты

- Backend: http://localhost:8000 (dev) → https://your-domain.com (prod)
- OneSignal: https://onesignal.com/apps/51602607-2b29-467b-ac12-5a7921f05a7e

