import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/date_symbol_data_local.dart';

import 'app.dart';
import 'core/notifications/notification_service.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();

  // Инициализируем локали для форматирования дат (ru_RU для календаря)
  await initializeDateFormatting('ru_RU', null);

  // Инициализируем уведомления (OneSignal + локальные алармы)
  await NotificationService.instance.initialize();

  runApp(const ProviderScope(child: NomadApp()));
}
