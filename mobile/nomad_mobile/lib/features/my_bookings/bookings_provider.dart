import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../core/api/api_client.dart';
import '../../shared/models/models.dart';

final upcomingBookingsProvider = FutureProvider<List<Booking>>((ref) async {
  final dio = ref.watch(dioProvider);
  final resp = await dio.get('/api/mobile/bookings',
      queryParameters: {'filter': 'upcoming'});
  return (resp.data as List)
      .map((e) => Booking.fromJson(e as Map<String, dynamic>))
      .toList();
});

final historyBookingsProvider = FutureProvider<List<Booking>>((ref) async {
  final dio = ref.watch(dioProvider);
  final resp = await dio.get('/api/mobile/bookings',
      queryParameters: {'filter': 'history'});
  return (resp.data as List)
      .map((e) => Booking.fromJson(e as Map<String, dynamic>))
      .toList();
});

final bookingDetailProvider =
    FutureProvider.family<Booking, int>((ref, id) async {
  final dio = ref.watch(dioProvider);
  final resp = await dio.get('/api/mobile/bookings/$id');
  return Booking.fromJson(resp.data as Map<String, dynamic>);
});
