import 'package:flutter/foundation.dart' show kIsWeb;
import 'package:flutter/material.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:onesignal_flutter/onesignal_flutter.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:timezone/timezone.dart' as tz;
import 'package:timezone/data/latest.dart' as tz_data;

/// Централизованный сервис уведомлений.
/// - flutter_local_notifications: локальные звуковые уведомления
///   (напоминания за 24ч/1ч, оценка занятия, события записи).
/// - OneSignal: push от сервера (ответ поддержки, активация пакета).
/// Каждый тип уведомления имеет СВОЙ звук, чтобы различать их на слух,
/// и приходит даже при заблокированном экране.
/// На web всё отключено — не поддерживается.
class NotificationService {
  NotificationService._();
  static final NotificationService instance = NotificationService._();

  static const String _oneSignalAppId = '51602607-2b29-467b-ac12-5a7921f05a7e';

  final FlutterLocalNotificationsPlugin _local =
      FlutterLocalNotificationsPlugin();

  // ─── Ключи типов уведомлений (совпадают с настройками) ────────────────────
  static const String kBookingConfirmed = 'booking_confirmed';
  static const String kReminder24h = 'reminder_24h';
  static const String kReminder1h = 'reminder_1h';
  static const String kRateRequest = 'rate_request';
  static const String kBookingCancelled = 'booking_cancelled';
  static const String kSupportReply = 'support_reply';

  /// Конфигурация каждого типа: канал, звук, тексты по умолчанию.
  static const Map<String, _NotifKind> _kinds = {
    kBookingConfirmed: _NotifKind(
      channelId: 'nomad_confirmed',
      channelName: 'Подтверждение записи',
      sound: 'confirmed',
      title: 'Запись подтверждена ✅',
      body: 'Ждём вас на занятии. Удачной поездки!',
    ),
    kReminder24h: _NotifKind(
      channelId: 'nomad_reminder_24h',
      channelName: 'Напоминание за 24 часа',
      sound: 'reminder_24h',
      title: 'Завтра занятие 🚗',
      body: 'Напоминаем о занятии завтра.',
    ),
    kReminder1h: _NotifKind(
      channelId: 'nomad_reminder_1h',
      channelName: 'Напоминание за 1 час',
      sound: 'reminder_1h',
      title: 'Через час занятие ⏰',
      body: 'Пора собираться!',
    ),
    kRateRequest: _NotifKind(
      channelId: 'nomad_rate',
      channelName: 'Оценить занятие',
      sound: 'rate',
      title: 'Как прошло занятие? ⭐',
      body: 'Оцените инструктора — это займёт пару секунд.',
    ),
    kBookingCancelled: _NotifKind(
      channelId: 'nomad_cancelled',
      channelName: 'Запись отменена',
      sound: 'cancelled',
      title: 'Запись отменена ❌',
      body: 'Ваша запись на занятие отменена.',
    ),
    kSupportReply: _NotifKind(
      channelId: 'nomad_support',
      channelName: 'Ответ поддержки',
      sound: 'support',
      title: 'Ответ поддержки 💬',
      body: 'Вам ответили в поддержке NOMAD.',
    ),
  };

  NotificationDetails _detailsFor(String key) {
    final k = _kinds[key]!;
    return NotificationDetails(
      android: AndroidNotificationDetails(
        k.channelId,
        k.channelName,
        importance: Importance.max,
        priority: Priority.high,
        playSound: true,
        sound: RawResourceAndroidNotificationSound(k.sound),
        enableVibration: true,
        enableLights: true,
        ledColor: const Color(0xFF1B5E20),
        ledOnMs: 800,
        ledOffMs: 400,
        category: AndroidNotificationCategory.reminder,
      ),
    );
  }

  // ─── Инициализация ────────────────────────────────────────────────────────

  Future<void> initialize() async {
    if (kIsWeb) return; // Web не поддерживает уведомления

    tz_data.initializeTimeZones();
    const initSettings = InitializationSettings(
      android: AndroidInitializationSettings('@mipmap/ic_launcher'),
    );
    await _local.initialize(initSettings);

    final android = _local.resolvePlatformSpecificImplementation<
        AndroidFlutterLocalNotificationsPlugin>();
    await android?.requestNotificationsPermission();

    // Создаём отдельный канал со своим звуком для каждого типа
    for (final k in _kinds.values) {
      await android?.createNotificationChannel(AndroidNotificationChannel(
        k.channelId,
        k.channelName,
        importance: Importance.max,
        playSound: true,
        sound: RawResourceAndroidNotificationSound(k.sound),
        enableVibration: true,
        enableLights: true,
      ));
    }

    OneSignal.Debug.setLogLevel(OSLogLevel.none);
    OneSignal.initialize(_oneSignalAppId);
    OneSignal.Notifications.requestPermission(true);

    OneSignal.Notifications.addClickListener((event) {
      final data = event.notification.additionalData;
      if (data != null) {
        _handlePushData(data);
      }
    });
  }

  // ─── Проверка переключателя из настроек ───────────────────────────────────

  Future<bool> _enabled(String key) async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getBool('notif_$key') ?? true;
  }

  // ─── Привязка пользователя к OneSignal ────────────────────────────────────

  void loginUser(int userId) {
    if (kIsWeb) return;
    OneSignal.login('user_$userId');
  }

  void logoutUser() {
    if (kIsWeb) return;
    OneSignal.logout();
  }

  /// Запрашивает разрешение на уведомления (системное окно Android).
  /// Возвращает true, если разрешено.
  Future<bool> requestPermissions() async {
    if (kIsWeb) return false;

    // Запрашиваем разрешение через flutter_local_notifications
    final android = _local.resolvePlatformSpecificImplementation<
        AndroidFlutterLocalNotificationsPlugin>();
    final flnGranted = await android?.requestNotificationsPermission() ?? false;

    // Также запрашиваем через OneSignal (это покажет системное окно Android)
    final osGranted = await OneSignal.Notifications.requestPermission(true);

    return flnGranted || osGranted;
  }

  // ─── Мгновенное событие (плашка + свой звук) ──────────────────────────────

  /// Показывает уведомление немедленно, если тип включён в настройках.
  Future<void> showEvent(String key, {String? title, String? body}) async {
    if (kIsWeb) return;
    if (!await _enabled(key)) return;
    final k = _kinds[key]!;
    final id = DateTime.now().millisecondsSinceEpoch.remainder(1000000);
    await _local.show(id, title ?? k.title, body ?? k.body, _detailsFor(key));
  }

  // ─── Планирование напоминаний о занятии ───────────────────────────────────

  Future<void> scheduleBookingReminder({
    required int bookingId,
    required DateTime lessonDatetime,
    DateTime? lessonEndDatetime,
    required String lessonType,
  }) async {
    if (kIsWeb) return;
    final now = DateTime.now();

    // За 24 часа
    if (await _enabled(kReminder24h)) {
      final t = lessonDatetime.subtract(const Duration(hours: 24));
      if (t.isAfter(now)) {
        await _zoned(
          _id24h(bookingId),
          'Завтра занятие 🚗',
          '$lessonType завтра в ${_hhmm(lessonDatetime)}.\n\n'
              '💵 Оплатить занятие можно наличными или через Kaspi QR.\n\n'
              'До встречи!',
          t,
          _detailsFor(kReminder24h),
        );
      }
    }

    // За 1 час
    if (await _enabled(kReminder1h)) {
      final t = lessonDatetime.subtract(const Duration(hours: 1));
      if (t.isAfter(now)) {
        await _zoned(
          bookingId,
          'Через час занятие ⏰',
          '$lessonType начнётся в ${_hhmm(lessonDatetime)}.\n\n'
              '💵 Оплатить занятие можно наличными или через Kaspi QR.\n\n'
              'Пора собираться! 🚗',
          t,
          _detailsFor(kReminder1h),
        );
      }
    }

    // Запрос оценки ровно через час после окончания занятия.
    // `end` содержит и дату, и время, поэтому будущая запись никогда не
    // получит это уведомление в день создания.
    if (await _enabled(kRateRequest)) {
      final end =
          lessonEndDatetime ?? lessonDatetime.add(const Duration(hours: 1));
      final t = end.add(const Duration(hours: 1));
      if (t.isAfter(now)) {
        final k = _kinds[kRateRequest]!;
        await _zoned(
            _idRate(bookingId), k.title, k.body, t, _detailsFor(kRateRequest));
      }
    }
  }

  Future<void> _zoned(int id, String title, String body, DateTime when,
      NotificationDetails details) async {
    await _local.zonedSchedule(
      id,
      title,
      body,
      tz.TZDateTime.from(when, tz.local),
      details,
      androidScheduleMode: AndroidScheduleMode.exactAllowWhileIdle,
      uiLocalNotificationDateInterpretation:
          UILocalNotificationDateInterpretation.absoluteTime,
    );
  }

  int _id24h(int bookingId) => bookingId + 1000000;
  int _idRate(int bookingId) => bookingId + 2000000;
  String _hhmm(DateTime d) =>
      '${d.hour.toString().padLeft(2, '0')}:${d.minute.toString().padLeft(2, '0')}';

  Future<void> cancelBookingReminder(int bookingId) async {
    if (kIsWeb) return;
    await _local.cancel(bookingId);
    await _local.cancel(_id24h(bookingId));
    await _local.cancel(_idRate(bookingId));
  }

  Future<void> cancelAllReminders() async {
    if (kIsWeb) return;
    await _local.cancelAll();
  }

  Future<void> resyncReminders(List<BookingReminder> bookings) async {
    if (kIsWeb) return;
    await _local.cancelAll();
    for (final b in bookings) {
      await scheduleBookingReminder(
        bookingId: b.id,
        lessonDatetime: b.datetime,
        lessonEndDatetime: b.endDatetime,
        lessonType: b.lessonType,
      );
    }
  }

  // ─── Обработка данных из push ─────────────────────────────────────────────

  void _handlePushData(Map<String, dynamic> data) {
    final type = data['type'] as String?;
    if (type == 'support_reply') {
      navigatorKey.currentState?.pushNamed('/support');
    } else if (type == 'booking_cancelled') {
      navigatorKey.currentState?.pushNamed('/bookings');
    }
  }
}

/// Внутреннее описание типа уведомления
class _NotifKind {
  final String channelId;
  final String channelName;
  final String sound;
  final String title;
  final String body;

  const _NotifKind({
    required this.channelId,
    required this.channelName,
    required this.sound,
    required this.title,
    required this.body,
  });
}

/// Ключ навигатора для перехода из уведомления
final GlobalKey<NavigatorState> navigatorKey = GlobalKey<NavigatorState>();

/// DTO для ресинхронизации напоминаний
class BookingReminder {
  final int id;
  final DateTime datetime;
  final DateTime? endDatetime;
  final String lessonType;

  const BookingReminder({
    required this.id,
    required this.datetime,
    this.endDatetime,
    required this.lessonType,
  });
}
