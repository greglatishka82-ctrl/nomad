import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:dio/dio.dart';

import '../../core/api/api_client.dart';
import '../../core/theme/app_theme.dart';
import '../../core/utils/formatters.dart';
import '../../shared/models/models.dart';
import '../../shared/widgets/common_widgets.dart';
import 'bookings_provider.dart';

class BookingDetailScreen extends ConsumerWidget {
  final int bookingId;
  const BookingDetailScreen({super.key, required this.bookingId});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(bookingDetailProvider(bookingId));
    return Scaffold(
      appBar: AppBar(title: const Text('Запись')),
      body: async.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (e, _) => ErrorState(
          message: 'Не удалось загрузить запись',
          onRetry: () => ref.refresh(bookingDetailProvider(bookingId)),
        ),
        data: (booking) => _DetailBody(booking: booking, ref: ref),
      ),
    );
  }
}

class _DetailBody extends StatelessWidget {
  final Booking booking;
  final WidgetRef ref;
  const _DetailBody({required this.booking, required this.ref});

  Future<void> _cancel(BuildContext context) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        title: const Text('Отменить запись?'),
        content: const Text('Отмена возможна не позже чем за 2 часа до занятия.'),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('Нет')),
          TextButton(
              onPressed: () => Navigator.pop(context, true),
              child: const Text('Отменить',
                  style: TextStyle(color: AppColors.error))),
        ],
      ),
    );
    if (confirmed != true) return;

    try {
      final dio = ref.read(dioProvider);
      await dio.delete('/api/mobile/bookings/${booking.id}');
      ref.invalidate(upcomingBookingsProvider);
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Запись отменена')),
        );
        context.pop();
      }
    } on DioException catch (e) {
      final msg = (e.response?.data as Map?)?['detail'] ?? 'Ошибка';
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(msg.toString()),
              backgroundColor: AppColors.error),
        );
      }
    }
  }

  Future<void> _rate(BuildContext context, String vote) async {
    try {
      final dio = ref.read(dioProvider);
      await dio.post('/api/mobile/bookings/${booking.id}/rate',
          data: {'vote': vote});
      ref.invalidate(bookingDetailProvider(booking.id));
      ref.invalidate(historyBookingsProvider);
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Спасибо за оценку!')),
        );
      }
    } catch (_) {}
  }

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Статус
          Row(
            children: [
              Expanded(
                child: Text(
                  serviceTypeLabel(booking.serviceType),
                  style: const TextStyle(
                      fontSize: 22,
                      fontWeight: FontWeight.w700,
                      color: AppColors.textPrimary),
                ),
              ),
              StatusBadge(status: booking.status),
            ],
          ),
          const SizedBox(height: 20),

          // Детали
          _Section(title: 'Детали занятия', children: [
            _Row(label: 'Дата', value: formatDate(booking.bookingDate)),
            _Row(label: 'Время',
                value: '${booking.startTime} — ${booking.endTime}'),
            _Row(label: 'Площадка', value: booking.location),
            _Row(label: 'Тип КПП',
                value: transmissionLabel(booking.transmission)),
            _Row(label: 'Стоимость', value: formatPrice(booking.price)),
          ]),

          if (booking.instructor != null) ...[
            const SizedBox(height: 16),
            _Section(title: 'Инструктор', children: [
              Row(
                children: [
                  InstructorAvatar(
                    avatarUrl: booking.instructor!.avatarUrl,
                    name: booking.instructor!.name,
                    radius: 28,
                  ),
                  const SizedBox(width: 14),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(booking.instructor!.name,
                            style: const TextStyle(
                                fontWeight: FontWeight.w700,
                                fontSize: 15)),
                        Text(
                            '★ ${booking.instructor!.rating.toStringAsFixed(1)}  •  '
                            '${booking.instructor!.experienceYears} лет',
                            style: const TextStyle(
                                color: AppColors.textSecondary,
                                fontSize: 13)),
                      ],
                    ),
                  ),
                ],
              ),
            ]),
          ],

          // Оценка
          if (booking.canRate) ...[
            const SizedBox(height: 16),
            _Section(title: 'Оценить занятие', children: [
              const Text('Как прошло занятие?',
                  style: TextStyle(color: AppColors.textSecondary)),
              const SizedBox(height: 12),
              Row(
                children: [
                  _RateButton(
                    emoji: '👍',
                    label: 'Хорошо',
                    onTap: () => _rate(context, 'good'),
                  ),
                  const SizedBox(width: 8),
                  _RateButton(
                    emoji: '😐',
                    label: 'Нормально',
                    onTap: () => _rate(context, 'normal'),
                  ),
                  const SizedBox(width: 8),
                  _RateButton(
                    emoji: '👎',
                    label: 'Плохо',
                    onTap: () => _rate(context, 'bad'),
                  ),
                ],
              ),
            ]),
          ] else if (booking.ratingVote != null) ...[
            const SizedBox(height: 16),
            _Section(title: 'Ваша оценка', children: [
              Row(
                children: [
                  Text(
                    booking.ratingVote == 'good'
                        ? '👍 Хорошо'
                        : booking.ratingVote == 'normal'
                            ? '😐 Нормально'
                            : '👎 Плохо',
                    style: const TextStyle(fontSize: 16),
                  ),
                ],
              ),
            ]),
          ],

          const SizedBox(height: 24),

          // Кнопка отмены
          if (booking.canCancel)
            SizedBox(
              width: double.infinity,
              child: OutlinedButton.icon(
                style: OutlinedButton.styleFrom(
                  foregroundColor: AppColors.error,
                  side: const BorderSide(color: AppColors.error),
                  minimumSize: const Size(double.infinity, 50),
                ),
                icon: const Icon(Icons.close),
                label: const Text('Отменить запись'),
                onPressed: () => _cancel(context),
              ),
            ),

          const SizedBox(height: 80),
        ],
      ),
    );
  }
}

class _Section extends StatelessWidget {
  final String title;
  final List<Widget> children;
  const _Section({required this.title, required this.children});

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: AppColors.divider),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(title,
              style: const TextStyle(
                  fontWeight: FontWeight.w700,
                  fontSize: 14,
                  color: AppColors.textSecondary)),
          const SizedBox(height: 12),
          ...children,
        ],
      ),
    );
  }
}

class _Row extends StatelessWidget {
  final String label;
  final String value;
  const _Row({required this.label, required this.value});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 100,
            child: Text(label,
                style: const TextStyle(
                    color: AppColors.textSecondary, fontSize: 13)),
          ),
          Expanded(
            child: Text(value,
                style: const TextStyle(
                    fontWeight: FontWeight.w600,
                    color: AppColors.textPrimary,
                    fontSize: 13)),
          ),
        ],
      ),
    );
  }
}

class _RateButton extends StatelessWidget {
  final String emoji;
  final String label;
  final VoidCallback onTap;
  const _RateButton(
      {required this.emoji, required this.label, required this.onTap});

  @override
  Widget build(BuildContext context) {
    return Expanded(
      child: GestureDetector(
        onTap: onTap,
        child: Container(
          padding: const EdgeInsets.symmetric(vertical: 12),
          decoration: BoxDecoration(
            color: AppColors.background,
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: AppColors.divider),
          ),
          child: Column(
            children: [
              Text(emoji, style: const TextStyle(fontSize: 24)),
              const SizedBox(height: 4),
              Text(label,
                  style: const TextStyle(
                      fontSize: 11,
                      color: AppColors.textSecondary)),
            ],
          ),
        ),
      ),
    );
  }
}
