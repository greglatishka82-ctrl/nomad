import 'dart:async';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../core/api/api_client.dart';
import '../../core/auth/auth_storage.dart';
import '../../core/notifications/notification_service.dart';
import '../../shared/models/models.dart';

// ── Upcoming bookings — авто-обновление каждые 30 секунд ────────────────────

class UpcomingBookingsNotifier extends AsyncNotifier<List<Booking>> {
  Timer? _timer;

  @override
  Future<List<Booking>> build() async {
    _timer?.cancel();
    _timer = Timer.periodic(const Duration(seconds: 30), (_) {
      _refresh();
    });
    ref.onDispose(() => _timer?.cancel());
    return _fetch();
  }

  Future<List<Booking>> _fetch() async {
    // The home screen can be created before authentication finishes, or after
    // an expired token has been cleared. Do not keep issuing an unauthorized
    // request every 30 seconds in either case.
    final accessToken = await AuthStorage.getAccessToken();
    if (accessToken == null || accessToken.isEmpty) return [];

    final dio = ref.read(dioProvider);
    final resp = await dio.get('/api/mobile/bookings',
        queryParameters: {'filter': 'upcoming'});
    final bookings = (resp.data as List)
        .map((e) => Booking.fromJson(e as Map<String, dynamic>))
        .toList();

    // Перепланируем локальные звуковые напоминания за 1 час до каждого занятия.
    // Срабатывают даже при заблокированном экране (точный аларм Android).
    await NotificationService.instance.resyncReminders(
      bookings
          .where((b) => b.startDateTime != null)
          .map((b) => BookingReminder(
                id: b.id,
                datetime: b.startDateTime!,
                endDatetime: b.endDateTime,
                lessonType: b.lessonTypeLabel,
              ))
          .toList(),
    );

    return bookings;
  }

  Future<void> _refresh() async {
    try {
      final bookings = await _fetch();
      state = AsyncData(bookings);
    } catch (_) {
      // Тихо игнорируем — не сбрасываем старые данные при ошибке сети
    }
  }

  /// Принудительное обновление (pull-to-refresh, после создания/отмены записи)
  Future<void> refresh() async {
    state = const AsyncLoading();
    state = await AsyncValue.guard(_fetch);
  }
}

final upcomingBookingsProvider =
    AsyncNotifierProvider<UpcomingBookingsNotifier, List<Booking>>(
  UpcomingBookingsNotifier.new,
);

// ── History bookings — страница из семи записей по запросу ──────────────────

class HistoryBookingsState {
  final List<Booking> bookings;
  final bool hasMore;
  final bool isLoadingMore;

  const HistoryBookingsState({
    required this.bookings,
    required this.hasMore,
    this.isLoadingMore = false,
  });

  HistoryBookingsState copyWith({
    List<Booking>? bookings,
    bool? hasMore,
    bool? isLoadingMore,
  }) => HistoryBookingsState(
        bookings: bookings ?? this.bookings,
        hasMore: hasMore ?? this.hasMore,
        isLoadingMore: isLoadingMore ?? this.isLoadingMore,
      );
}

class HistoryBookingsNotifier extends AsyncNotifier<HistoryBookingsState> {
  int _page = 1;

  @override
  Future<HistoryBookingsState> build() => _fetchPage(1);

  Future<HistoryBookingsState> _fetchPage(int page) async {
    final dio = ref.read(dioProvider);
    final response = await dio.get(
      '/api/mobile/bookings/history',
      queryParameters: {'page': page},
    );
    final data = response.data as Map<String, dynamic>;
    final bookings = (data['items'] as List? ?? const [])
        .map((item) => Booking.fromJson(item as Map<String, dynamic>))
        .toList();
    return HistoryBookingsState(
      bookings: bookings,
      hasMore: data['has_more'] as bool? ?? false,
    );
  }

  Future<void> refresh() async {
    _page = 1;
    state = const AsyncLoading();
    state = await AsyncValue.guard(() => _fetchPage(_page));
  }

  Future<void> loadMore() async {
    final current = state.valueOrNull;
    if (current == null || !current.hasMore || current.isLoadingMore) return;
    state = AsyncData(current.copyWith(isLoadingMore: true));
    try {
      final nextPage = _page + 1;
      final next = await _fetchPage(nextPage);
      _page = nextPage;
      state = AsyncData(HistoryBookingsState(
        bookings: [...current.bookings, ...next.bookings],
        hasMore: next.hasMore,
      ));
    } catch (_) {
      state = AsyncData(current);
      rethrow;
    }
  }
}

final historyBookingsProvider =
    AsyncNotifierProvider<HistoryBookingsNotifier, HistoryBookingsState>(
  HistoryBookingsNotifier.new,
);

final bookingDetailProvider =
    FutureProvider.family<Booking, int>((ref, id) async {
  final dio = ref.watch(dioProvider);
  final resp = await dio.get('/api/mobile/bookings/$id');
  return Booking.fromJson(resp.data as Map<String, dynamic>);
});
