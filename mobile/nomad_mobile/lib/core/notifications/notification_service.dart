import 'package:flutter/foundation.dart' show kIsWeb;
import 'package:flutter/material.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:onesignal_flutter/onesignal_flutter.dart';
import 'package:timezone/timezone.dart' as tz;
import 'package:timezone/data/latest.dart' as tz_data;

/// Централизованный сервис уведомлений.
/// - OneSignal: push от сервера (ответ поддержки, отмена записи администратором)
/// - flutter_local_notifications: локальный аларм за 1 час до занятия
/// На web всё отключено — не поддерживается.
class NotificationService {
  NotificationService._();
  static final NotificationService instance = NotificationService._();

  static const String _oneSignalAppId = '51602607-2b29-467b-ac12-5a7921f05a7e';

  final FlutterLocalNotificationsPlugin _local = FlutterLocalNotificationsPlugin();

  static const AndroidNotificationDetails _androidDetails = AndroidNotificationDetails(
    'booking_reminders',
    'Напоминания о занятиях',
    channelDescription: 'Уведомление за 1 час до начала занятия',
    importance: Importance.high,
    priority: Priority.high,
  );

  static const NotificationDetails _notificationDetails = NotificationDetails(
    android: _androidDetails,
  );

  // ─── Инициализация ────────────────────────────────────────────────────────

  Future<void> initialize() async {
    if (kIsWeb) return; // Web не поддерживает уведомления

    tz_data.initializeTimeZones();
    const initSettings = InitializationSettings(
      android: AndroidInitializationSettings('@mipmap/ic_launcher'),
    );
    await _local.initialize(initSettings);

    OneSignal.Debug.setLogLevel(OSLogLevel.none);
    OneSignal.initialize(_oneSignalAppId);
    OneSignal.Notifications.requestPermission(false);

    OneSignal.Notifications.addClickListener((event) {
      final data = event.notification.additionalData;
      if (data != null) {
        _handlePushData(data);
      }
    });
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

  // ─── Локальный аларм за 1 час до занятия ──────────────────────────────────

  Future<void> scheduleBookingReminder({
    required int bookingId,
    required DateTime lessonDatetime,
    required String lessonType,
  }) async {
    if (kIsWeb) return;

    final reminderTime = lessonDatetime.subtract(const Duration(hours: 1));
    if (reminderTime.isBefore(DateTime.now())) return;

    final tzTime = tz.TZDateTime.from(reminderTime, tz.local);
    await _local.zonedSchedule(
      bookingId,
      'Напоминание о занятии',
      '$lessonType начнётся через 1 час',
      tzTime,
      _notificationDetails,
      androidScheduleMode: AndroidScheduleMode.exactAllowWhileIdle,
      uiLocalNotificationDateInterpretation:
          UILocalNotificationDateInterpretation.absoluteTime,
    );
  }

  Future<void> cancelBookingReminder(int bookingId) async {
    if (kIsWeb) return;
    await _local.cancel(bookingId);
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

/// Ключ навигатора для перехода из уведомления
final GlobalKey<NavigatorState> navigatorKey = GlobalKey<NavigatorState>();

/// DTO для ресинхронизации алармов
class BookingReminder {
  final int id;
  final DateTime datetime;
  final String lessonType;

  const BookingReminder({
    required this.id,
    required this.datetime,
    required this.lessonType,
  });
}
