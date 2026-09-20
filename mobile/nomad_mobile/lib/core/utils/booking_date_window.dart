const String kFixedBookingLocation = 'Циолковского 30';
const Duration _pavlodarUtcOffset = Duration(hours: 5);

/// Час, с которого школа ещё принимает запись сегодня.
///
/// Жёстких часов закрытия нет: вождение идёт по графику инструктора, поэтому
/// сегодня доступно всегда, а пустые дни клиент увидит как «нет слотов».
/// Пробный экзамен инструктор принимает только до examLastSlotHour, поэтому
/// после этого часа календарь начинается со следующего дня.
int _lastSlotHour(String? serviceType, int examLastSlotHour) =>
    serviceType == 'exam' ? examLastSlotHour : 24;

DateTime bookingWindowStart({
  String? serviceType,
  int examLastSlotHour = 20,
}) {
  final now = DateTime.now().toUtc().add(_pavlodarUtcOffset);
  final today = DateTime(now.year, now.month, now.day);
  final dayEnd = DateTime(
      now.year, now.month, now.day, _lastSlotHour(serviceType, examLastSlotHour));
  return now.isBefore(dayEnd) ? today : today.add(const Duration(days: 1));
}

DateTime bookingWindowEnd({
  String? serviceType,
  int examLastSlotHour = 20,
}) {
  return bookingWindowStart(
    serviceType: serviceType,
    examLastSlotHour: examLastSlotHour,
  ).add(const Duration(days: 6));
}

DateTime initialBookingDate({
  String? serviceType,
  int examLastSlotHour = 20,
}) {
  return bookingWindowStart(
    serviceType: serviceType,
    examLastSlotHour: examLastSlotHour,
  );
}
