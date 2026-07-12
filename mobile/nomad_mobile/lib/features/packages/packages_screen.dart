import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:dio/dio.dart';

import '../../core/api/api_client.dart';
import '../../core/theme/app_theme.dart';
import '../../core/utils/formatters.dart';
import '../../shared/models/models.dart';
import '../../shared/widgets/common_widgets.dart';
import '../profile/profile_screen.dart';

final availablePackagesProvider = FutureProvider<List<Package>>((ref) async {
  final dio = ref.watch(dioProvider);
  final resp = await dio.get('/api/mobile/packages');
  return (resp.data as List)
      .map((e) => Package.fromJson(e as Map<String, dynamic>))
      .toList();
});

class PackagesScreen extends ConsumerWidget {
  const PackagesScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(availablePackagesProvider);
    return Scaffold(
      appBar: AppBar(title: const Text('Пакеты занятий')),
      body: async.when(
        loading: () =>
            ListView(children: List.generate(3, (_) => const ShimmerCard())),
        error: (e, _) => ErrorState(
          message: 'Не удалось загрузить пакеты',
          onRetry: () => ref.refresh(availablePackagesProvider),
        ),
        data: (packages) {
          if (packages.isEmpty) {
            return const EmptyState(
              icon: Icons.card_membership_outlined,
              title: 'Пакеты недоступны',
              subtitle: 'Свяжитесь с нами для уточнения',
            );
          }
          return ListView.builder(
            padding: const EdgeInsets.all(16),
            itemCount: packages.length,
            itemBuilder: (_, i) => _PackageCard(package: packages[i]),
          );
        },
      ),
    );
  }
}

class _PackageCard extends ConsumerWidget {
  final Package package;
  const _PackageCard({required this.package});

  Future<void> _request(BuildContext context, WidgetRef ref) async {
    try {
      final dio = ref.read(dioProvider);
      await dio.post('/api/mobile/packages/${package.id}/request');
      ref.invalidate(myPackagesProvider);
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text(
                'Заявка отправлена! Администратор активирует пакет после оплаты.'),
            backgroundColor: AppColors.success,
          ),
        );
      }
    } on DioException catch (e) {
      final msg = (e.response?.data as Map?)?['detail'] ?? 'Ошибка';
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
              content: Text(msg.toString()),
              backgroundColor: AppColors.error),
        );
      }
    }
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final pricePerSession =
        (package.price / package.sessionsCount).round();
    final configAsync = ref.watch(appConfigProvider);
    return configAsync.when(
      loading: () => const Card(
        child: Padding(
          padding: EdgeInsets.all(20),
          child: Center(child: CircularProgressIndicator()),
        ),
      ),
      error: (_, __) => Card(
        child: Padding(
          padding: EdgeInsets.all(20),
          child: Text('Ошибка загрузки конфигурации'),
        ),
      ),
      data: (config) {
        final singlePrice = config.priceTraining;
        final savings =
            (singlePrice * package.sessionsCount) - package.price;
        return Card(
          child: Padding(
            padding: const EdgeInsets.all(20),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Expanded(
                      child: Text(package.name,
                          style: const TextStyle(
                              fontWeight: FontWeight.w700, fontSize: 17)),
                    ),
                    if (savings > 0)
                      Container(
                        padding: const EdgeInsets.symmetric(
                            horizontal: 10, vertical: 4),
                        decoration: BoxDecoration(
                          color: AppColors.success.withValues(alpha: 0.12),
                          borderRadius: BorderRadius.circular(20),
                        ),
                        child: Text('−${formatPrice(savings)}',
                            style: const TextStyle(
                                color: AppColors.success,
                                fontSize: 12,
                                fontWeight: FontWeight.w600)),
                      ),
                  ],
                ),
                const SizedBox(height: 12),
                Row(
                  children: [
                    _Stat(
                        label: 'Занятий',
                        value: '${package.sessionsCount}'),
                    const SizedBox(width: 24),
                    _Stat(
                        label: 'За занятие',
                        value: formatPrice(pricePerSession)),
                  ],
                ),
                const SizedBox(height: 16),
                Row(
                  children: [
                    Expanded(
                      child: Text(formatPrice(package.price),
                          style: const TextStyle(
                              fontSize: 22,
                              fontWeight: FontWeight.w800,
                              color: AppColors.primary)),
                    ),
                    ElevatedButton(
                      onPressed: () => _request(context, ref),
                      style: ElevatedButton.styleFrom(
                          minimumSize: const Size(0, 44)),
                      child: const Text('Купить'),
                    ),
                  ],
                ),
                const SizedBox(height: 8),
                Text(
                  'Оплата наличными в автошколе • Администратор активирует пакет',
                  style: const TextStyle(
                      color: AppColors.textSecondary, fontSize: 12),
                ),
              ],
            ),
          ),
        );
      },
    );
  }
}

class _Stat extends StatelessWidget {
  final String label;
  final String value;
  const _Stat({required this.label, required this.value});

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label,
            style: const TextStyle(
                color: AppColors.textSecondary, fontSize: 12)),
        Text(value,
            style: const TextStyle(
                fontWeight: FontWeight.w700, fontSize: 15)),
      ],
    );
  }
}
