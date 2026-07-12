import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:dio/dio.dart';

import '../../core/api/api_client.dart';
import '../../core/theme/app_theme.dart';
import '../../core/utils/formatters.dart';
import '../../shared/models/models.dart';
import '../../shared/widgets/common_widgets.dart';
import '../profile/profile_screen.dart';

class CertificatesScreen extends ConsumerWidget {
  const CertificatesScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(myCertificatesProvider);
    return Scaffold(
      appBar: AppBar(title: const Text('Мои сертификаты')),
      floatingActionButton: FloatingActionButton.extended(
        backgroundColor: AppColors.accent,
        icon: const Icon(Icons.add, color: Colors.white),
        label: const Text('Активировать',
            style: TextStyle(
                color: Colors.white, fontWeight: FontWeight.w600)),
        onPressed: () => _showActivateDialog(context, ref),
      ),
      body: async.when(
        loading: () =>
            ListView(children: List.generate(2, (_) => const ShimmerCard())),
        error: (e, _) => ErrorState(
          message: 'Не удалось загрузить сертификаты',
          onRetry: () => ref.refresh(myCertificatesProvider),
        ),
        data: (certs) {
          if (certs.isEmpty) {
            return EmptyState(
              icon: Icons.card_giftcard_outlined,
              title: 'Нет сертификатов',
              subtitle: 'Введите код сертификата чтобы его активировать',
              buttonLabel: 'Активировать',
              onButton: () => _showActivateDialog(context, ref),
            );
          }
          return ListView.builder(
            padding: const EdgeInsets.all(16),
            itemCount: certs.length,
            itemBuilder: (_, i) => _CertCard(cert: certs[i]),
          );
        },
      ),
    );
  }

  void _showActivateDialog(BuildContext context, WidgetRef ref) {
    final ctrl = TextEditingController();
    showDialog(
      context: context,
      builder: (_) => AlertDialog(
        title: const Text('Активировать сертификат'),
        content: TextField(
          controller: ctrl,
          textCapitalization: TextCapitalization.characters,
          decoration: const InputDecoration(
            labelText: 'Код сертификата',
            hintText: 'NOMAD-XXXX',
          ),
        ),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('Отмена')),
          ElevatedButton(
            onPressed: () async {
              try {
                final dio = ref.read(dioProvider);
                final resp = await dio.post(
                    '/api/mobile/certificates/activate',
                    data: {'code': ctrl.text.trim()});
                ref.invalidate(myCertificatesProvider);
                if (context.mounted) {
                  Navigator.pop(context);
                  final data = resp.data as Map;
                  ScaffoldMessenger.of(context).showSnackBar(
                    SnackBar(
                      content: Text(
                          'Сертификат активирован! Баланс: ${formatPrice(data['remaining'] as int)}'),
                      backgroundColor: AppColors.success,
                    ),
                  );
                }
              } on DioException catch (e) {
                final msg =
                    (e.response?.data as Map?)?['detail'] ?? 'Ошибка';
                if (context.mounted) {
                  ScaffoldMessenger.of(context).showSnackBar(
                    SnackBar(
                        content: Text(msg.toString()),
                        backgroundColor: AppColors.error),
                  );
                }
              }
            },
            child: const Text('Активировать'),
          ),
        ],
      ),
    );
  }
}

class _CertCard extends StatelessWidget {
  final UserCertificate cert;
  const _CertCard({required this.cert});

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Row(
          children: [
            Container(
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: cert.isSpent
                    ? AppColors.background
                    : AppColors.accent.withValues(alpha: 0.12),
                shape: BoxShape.circle,
              ),
              child: Icon(Icons.card_giftcard,
                  color: cert.isSpent
                      ? AppColors.textHint
                      : AppColors.accent),
            ),
            const SizedBox(width: 14),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(cert.code,
                      style: const TextStyle(
                          fontWeight: FontWeight.w700,
                          fontFamily: 'monospace')),
                  const SizedBox(height: 4),
                  Text(
                    cert.isSpent
                        ? 'Исчерпан'
                        : 'Остаток: ${formatPrice(cert.remaining)}',
                    style: TextStyle(
                        color: cert.isSpent
                            ? AppColors.textHint
                            : AppColors.success,
                        fontWeight: FontWeight.w600,
                        fontSize: 13),
                  ),
                  Text('Номинал: ${formatPrice(cert.nominal)}',
                      style: const TextStyle(
                          color: AppColors.textSecondary,
                          fontSize: 12)),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}
