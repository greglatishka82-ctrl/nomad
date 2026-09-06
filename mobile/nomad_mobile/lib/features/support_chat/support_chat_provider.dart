import 'dart:async';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../core/api/api_client.dart';
import '../../core/notifications/notification_service.dart';
import '../../shared/models/models.dart';

// ── Polling провайдер — обновляет сообщения каждые 5 секунд ─────────────────

class SupportMessagesNotifier extends AsyncNotifier<List<SupportMessage>> {
  Timer? _timer;
  // ID последнего известного сообщения от admin — для обнаружения новых
  int _lastAdminMsgId = 0;

  @override
  Future<List<SupportMessage>> build() async {
    _timer?.cancel();
    _timer = Timer.periodic(const Duration(seconds: 5), (_) {
      _refresh();
    });
    ref.onDispose(() => _timer?.cancel());
    final messages = await _fetchMessages();
    // Инициализируем базовый ID, чтобы не стрелять звуком при первой загрузке
    _lastAdminMsgId = _maxAdminId(messages);
    return messages;
  }

  Future<List<SupportMessage>> _fetchMessages() async {
    final dio = ref.read(dioProvider);
    final resp = await dio.get('/api/mobile/support/messages');
    return (resp.data as List)
        .map((e) => SupportMessage.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  int _maxAdminId(List<SupportMessage> messages) {
    final adminMsgs = messages.where((m) => m.sender == 'admin');
    if (adminMsgs.isEmpty) return 0;
    return adminMsgs.map((m) => m.id).reduce((a, b) => a > b ? a : b);
  }

  Future<void> _refresh() async {
    try {
      final messages = await _fetchMessages();
      final newMaxAdminId = _maxAdminId(messages);

      // Если появилось новое сообщение от admin — показываем звуковое уведомление
      if (newMaxAdminId > _lastAdminMsgId && _lastAdminMsgId > 0) {
        _lastAdminMsgId = newMaxAdminId;
        await NotificationService.instance.showEvent(
          NotificationService.kSupportReply,
        );
      } else if (newMaxAdminId > _lastAdminMsgId) {
        // Первый раз — только обновляем счётчик без звука
        _lastAdminMsgId = newMaxAdminId;
      }

      state = AsyncData(messages);
    } catch (_) {
      // Тихо игнорируем ошибки polling
    }
  }

  Future<void> refresh() async {
    await _refresh();
  }
}

final supportMessagesProvider =
    AsyncNotifierProvider<SupportMessagesNotifier, List<SupportMessage>>(
  SupportMessagesNotifier.new,
);

final supportUnreadCountProvider = FutureProvider<int>((ref) async {
  final dio = ref.watch(dioProvider);
  try {
    final resp = await dio.get('/api/mobile/support/unread-count');
    return (resp.data as Map<String, dynamic>)['unread_count'] as int;
  } catch (_) {
    return 0;
  }
});
