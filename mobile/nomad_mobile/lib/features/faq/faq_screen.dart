import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/api_client.dart';
import '../../core/theme/app_theme.dart';
import '../../shared/models/models.dart';
import '../../shared/widgets/common_widgets.dart';

final faqProvider = FutureProvider<List<FaqItem>>((ref) async {
  final dio = ref.watch(dioProvider);
  final resp = await dio.get('/api/faq');
  return (resp.data as List)
      .map((e) => FaqItem.fromJson(e as Map<String, dynamic>))
      .toList();
});

class FaqScreen extends ConsumerWidget {
  const FaqScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return Scaffold(
      appBar: AppBar(title: const Text('Вопрос-ответ')),
      body: const FaqBody(),
    );
  }
}

class FaqBody extends ConsumerWidget {
  const FaqBody({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(faqProvider);
    return async.when(
      loading: () =>
          ListView(children: List.generate(4, (_) => const ShimmerCard())),
      error: (e, _) => ErrorState(
        message: 'Не удалось загрузить FAQ',
        onRetry: () => ref.refresh(faqProvider),
      ),
      data: (items) {
        if (items.isEmpty) {
          return const EmptyState(
            icon: Icons.help_outline,
            title: 'FAQ пуст',
            subtitle: 'Свяжитесь с нами по телефону',
          );
        }
        return ListView.builder(
          padding: const EdgeInsets.all(12),
          itemCount: items.length,
          itemBuilder: (_, i) => _FaqTile(item: items[i]),
        );
      },
    );
  }
}

class _FaqTile extends StatelessWidget {
  final FaqItem item;
  const _FaqTile({required this.item});

  @override
  Widget build(BuildContext context) {
    return Card(
      child: ExpansionTile(
        title: Text(item.question,
            style: const TextStyle(
                fontWeight: FontWeight.w600,
                fontSize: 14,
                color: AppColors.textPrimary)),
        iconColor: AppColors.primary,
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
            child: Text(
              item.answer,
              style: const TextStyle(
                  color: AppColors.textSecondary,
                  height: 1.5,
                  fontSize: 14),
            ),
          ),
        ],
      ),
    );
  }
}
