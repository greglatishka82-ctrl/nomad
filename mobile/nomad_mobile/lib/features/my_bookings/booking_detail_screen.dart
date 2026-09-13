import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:dio/dio.dart';

import '../../core/api/api_client.dart';
import '../../core/theme/app_theme.dart';
import '../../core/utils/booking_date_window.dart';
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

  Future<void> _reschedule(BuildContext context) async {
    final config = await ref.read(appConfigProvider.future);
    if (!context.mounted) return;
    final firstDay = bookingWindowStart(
      workingHoursEnd: config.workingHoursEnd,
    );
    final lastDay = bookingWindowEnd(
      workingHoursEnd: config.workingHoursEnd,
    );
    final picked = await showDatePicker(
      context: context,
      initialDate: initialBookingDate(
        workingHoursEnd: config.workingHoursEnd,
      ),
      firstDate: firstDay,
      lastDate: lastDay,
      helpText: 'Выберите новую дату',
    );
    if (picked == null || !context.mounted) return;

    // Получаем доступные слоты ТОЛЬКО для текущего инструктора
    try {
      final dio = ref.read(dioProvider);
      final dateStr =
          '${picked.year}-${picked.month.toString().padLeft(2, '0')}-${picked.day.toString().padLeft(2, '0')}';
      final resp = await dio.get('/api/mobile/slots', queryParameters: {
        'booking_date': dateStr,
        'service_type': booking.serviceType,
        'transmission': booking.transmission,
        'instructor_id': booking.instructor!.id,
        'location_preference': kFixedBookingLocation,
      });
      final slots = List<String>.from(resp.data['slots'] as List);

      if (!context.mounted) return;
      if (slots.isEmpty) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
              content: Text(
                  'На выбранную дату нет свободных слотов у вашего инструктора')),
        );
        return;
      }

      // Показываем выбор времени
      final selectedTime = await showDialog<String>(
        context: context,
        builder: (_) => AlertDialog(
          title: const Text('Выберите время'),
          content: SizedBox(
            width: double.maxFinite,
            child: ListView.builder(
              shrinkWrap: true,
              itemCount: slots.length,
              itemBuilder: (ctx, i) => ListTile(
                title: Text(slots[i]),
                onTap: () => Navigator.pop(ctx, slots[i]),
              ),
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('Отмена'),
            ),
          ],
        ),
      );
      if (selectedTime == null || !context.mounted) return;

      // Отправляем заявку на перенос: текущая дата изменится после решения администратора.
      await dio.put('/api/mobile/bookings/${booking.id}/reschedule', data: {
        'new_date': dateStr,
        'new_start_time': selectedTime,
      });

      ref.invalidate(upcomingBookingsProvider);
      ref.invalidate(bookingDetailProvider(booking.id));

      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
              content: Text('Заявка на перенос отправлена администратору.')),
        );
        context.pop();
      }
    } on DioException catch (e) {
      final msg = (e.response?.data as Map?)?['detail'] ?? 'Ошибка';
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
              content: Text(msg.toString()), backgroundColor: AppColors.error),
        );
      }
    }
  }

  Future<void> _cancel(BuildContext context) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        title: const Text('Отменить запись?'),
        content:
            const Text('Отмена возможна не позже чем за 2 часа до занятия.'),
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
          const SnackBar(
              content: Text('Ваша заявка на отмену находится в обработке.')),
        );
        context.pop();
      }
    } on DioException catch (e) {
      final msg = (e.response?.data as Map?)?['detail'] ?? 'Ошибка';
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
              content: Text(msg.toString()), backgroundColor: AppColors.error),
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
    final cashPayment = booking.isUpcoming && booking.price > 0 && !booking.isPaid;
    return SingleChildScrollView(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (booking.isPending) ...[
            Container(
              width: double.infinity,
              padding: const EdgeInsets.all(16),
              decoration: BoxDecoration(
                color: Colors.orange.shade50,
                borderRadius: BorderRadius.circular(12),
                border: Border.all(color: Colors.orange.shade200),
              ),
              child: const Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    '⏳ Ваша заявка находится в обработке. Ожидайте подтверждения.',
                    style: TextStyle(fontWeight: FontWeight.w600, fontSize: 14),
                  ),
                  SizedBox(height: 8),
                  Text(
                    'Если подтверждение не пришло в течение 15 минут, свяжитесь с администратором автошколы.',
                    style:
                        TextStyle(fontSize: 12, color: AppColors.textSecondary),
                  ),
                  SizedBox(height: 4),
                  Text('📞 +7 702 718 22 33', style: TextStyle(fontSize: 12)),
                  Text('📞 +7 707 881 08 48', style: TextStyle(fontSize: 12)),
                  SizedBox(height: 4),
                  Text(
                    '⏰ Заявки подтверждаются с 09:00 до 19:00.',
                    style:
                        TextStyle(fontSize: 11, color: AppColors.textSecondary),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 16),
          ],

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
          if (booking.bookingNumber != null) ...[
            const SizedBox(height: 8),
            Text(
              '📋 Номер записи: ${booking.bookingNumber}',
              style: const TextStyle(
                fontSize: 16,
                fontWeight: FontWeight.w700,
                color: AppColors.primary,
              ),
            ),
          ],
          const SizedBox(height: 20),

          // Детали
          _Section(title: 'Детали занятия', children: [
            _Row(label: 'Дата', value: formatDate(booking.bookingDate)),
            _Row(
                label: 'Время',
                value: '${booking.startTime} — ${booking.endTime}'),
            _Row(label: 'Площадка', value: booking.location),
            _Row(
                label: 'Тип КПП',
                value: transmissionLabel(booking.transmission)),
          ]),

          const SizedBox(height: 16),
          _Section(title: 'Оплата', children: [
            _Row(
                label: 'Базовая стоимость',
                value: formatPrice(booking.basePrice)),
            if (booking.hasReferralDiscount)
              _Row(
                  label: 'Скидка по реферальному коду',
                  value: '−${formatPrice(booking.referralDiscountAmount)}'),
            if (booking.hasCertificate)
              _Row(
                  label: 'Оплачено сертификатом',
                  value: '−${formatPrice(booking.certificateAmount)}'),
            _Row(
              label: 'К оплате',
              value: (booking.packageSessionsUsed != null
                  ? 'Бесплатно (пакет)'
                  : '${formatPrice(booking.price)}${cashPayment ? ' (оплата наличными или через Kaspi QR)' : ''}'),
            ),
            if (booking.packageSessionsUsed != null)
              _Row(
                label: 'Пакет',
                value:
                    'использовано ${booking.packageSessionsUsed} из ${booking.packageSessionsTotal}',
              ),
            _Row(
              label: 'Статус оплаты',
              value: booking.isPaid
                  ? (booking.hasCertificate && booking.price == 0
                      ? 'Оплачено сертификатом'
                      : 'Оплачено')
                  : 'Оплата наличными или через Kaspi QR',
            ),
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
                                fontWeight: FontWeight.w700, fontSize: 15)),
                        Text('${booking.instructor!.experienceYears} лет стажа',
                            style: const TextStyle(
                                color: AppColors.textSecondary, fontSize: 13)),
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
              const SizedBox(height: 8),
              const Text(
                'Оценка полностью анонимна и нужна только для улучшения качества обслуживания.',
                style: TextStyle(color: AppColors.textSecondary, fontSize: 12),
              ),
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

          // Кнопки действий
          if (booking.canCancel) ...[
            const SizedBox(height: 24),
            SizedBox(
              width: double.infinity,
              child: OutlinedButton.icon(
                style: OutlinedButton.styleFrom(
                  foregroundColor: AppColors.primary,
                  side: const BorderSide(color: AppColors.primary),
                  minimumSize: const Size(double.infinity, 50),
                ),
                icon: const Icon(Icons.schedule),
                label: const Text('Изменить время'),
                onPressed: () => _reschedule(context),
              ),
            ),
            const SizedBox(height: 12),
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
          ],
          if (booking.isCancellationPending) ...[
            const SizedBox(height: 16),
            const Text(
                'Ваша заявка на отмену находится в обработке. Если вы нажали отмену случайно, отзовите заявку.'),
            const SizedBox(height: 8),
            OutlinedButton.icon(
              icon: const Icon(Icons.undo),
              label: const Text('Отменить отмену записи'),
              onPressed: () async {
                try {
                  final dio = ref.read(dioProvider);
                  await dio.post(
                      '/api/mobile/bookings/${booking.id}/cancel-request/revoke');
                  ref.invalidate(upcomingBookingsProvider);
                  ref.invalidate(bookingDetailProvider(booking.id));
                } on DioException catch (e) {
                  if (context.mounted) {
                    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
                        content: Text(
                            ((e.response?.data as Map?)?['detail'] ?? 'Ошибка')
                                .toString())));
                  }
                }
              },
            ),
          ],
          if (booking.isReschedulePending) ...[
            const SizedBox(height: 16),
            const Text(
              'Заявка на перенос времени находится в обработке. Текущая запись пока сохранена без изменений.',
            ),
          ],

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
                      fontSize: 11, color: AppColors.textSecondary)),
            ],
          ),
        ),
      ),
    );
  }
}
