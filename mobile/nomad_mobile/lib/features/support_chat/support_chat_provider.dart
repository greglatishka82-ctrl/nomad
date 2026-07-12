import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../core/api/api_client.dart';
import '../../shared/models/models.dart';

final supportMessagesProvider =
    FutureProvider<List<SupportMessage>>((ref) async {
  final dio = ref.watch(dioProvider);
  final resp = await dio.get('/api/mobile/support/messages');
  return (resp.data as List)
      .map((e) => SupportMessage.fromJson(e as Map<String, dynamic>))
      .toList();
});

final supportUnreadCountProvider = FutureProvider<int>((ref) async {
  final dio = ref.watch(dioProvider);
  try {
    final resp = await dio.get('/api/mobile/support/unread-count');
    return (resp.data as Map<String, dynamic>)['unread_count'] as int;
  } catch (_) {
    return 0;
  }
});
