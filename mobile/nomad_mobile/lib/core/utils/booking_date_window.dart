const String kFixedBookingLocation = 'Циолковского 30';
const Duration _pavlodarUtcOffset = Duration(hours: 5);

DateTime bookingWindowStart({required int workingHoursEnd}) {
  final now = DateTime.now().toUtc().add(_pavlodarUtcOffset);
  final today = DateTime(now.year, now.month, now.day);
  final dayEnd = DateTime(now.year, now.month, now.day, workingHoursEnd);
  return now.isBefore(dayEnd) ? today : today.add(const Duration(days: 1));
}

DateTime bookingWindowEnd({required int workingHoursEnd}) {
  return bookingWindowStart(workingHoursEnd: workingHoursEnd)
      .add(const Duration(days: 6));
}

DateTime initialBookingDate({required int workingHoursEnd}) {
  return bookingWindowStart(workingHoursEnd: workingHoursEnd);
}
