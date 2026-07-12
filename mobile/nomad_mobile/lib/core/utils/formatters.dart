import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

String formatPrice(int price) {
  final f = NumberFormat('#,###', 'ru_RU');
  return '${f.format(price)} ₸';
}

String formatDate(String isoDate) {
  final dt = DateTime.parse(isoDate);
  return DateFormat('d MMMM yyyy', 'ru_RU').format(dt);
}

String formatDateShort(String isoDate) {
  final dt = DateTime.parse(isoDate);
  return DateFormat('d MMM', 'ru_RU').format(dt);
}

String formatDatetime(String isoDatetime) {
  final dt = DateTime.parse(isoDatetime).toLocal();
  return DateFormat('d MMM, HH:mm', 'ru_RU').format(dt);
}

String formatTime(String time) {
  // "09:00:00" → "09:00"
  if (time.length >= 5) return time.substring(0, 5);
  return time;
}

String serviceTypeLabel(String type) {
  switch (type) {
    case 'training':
      return 'Урок вождения';
    case 'exam':
      return 'Пробный экзамен';
    default:
      return type;
  }
}

String transmissionLabel(String type) {
  switch (type) {
    case 'manual':
      return 'Механика';
    case 'automatic':
      return 'Автомат';
    case 'both':
      return 'Механика и автомат';
    default:
      return type;
  }
}

String dayOfWeekRu(DateTime dt) {
  const days = [
    'Понедельник', 'Вторник', 'Среда', 'Четверг',
    'Пятница', 'Суббота', 'Воскресенье'
  ];
  return days[dt.weekday - 1];
}


// Расширения для статусов
extension StatusExtension on String {
  String get statusLabel {
    switch (this) {
      case 'planned':
        return 'Запланирована';
      case 'confirmed':
        return 'Подтверждена';
      case 'completed':
        return 'Завершена';
      case 'cancelled':
        return 'Отменена';
      case 'no_show':
        return 'Не явился';
      default:
        return this;
    }
  }

  Color get statusColor {
    switch (this) {
      case 'planned':
        return const Color(0xFF2563EB); // primary
      case 'confirmed':
        return const Color(0xFF16A34A); // success
      case 'completed':
        return const Color(0xFF64748B); // gray
      case 'cancelled':
        return const Color(0xFFDC2626); // error
      case 'no_show':
        return const Color(0xFFF59E0B); // warning
      default:
        return const Color(0xFF64748B);
    }
  }
}
