import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:dio/dio.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../../core/api/api_client.dart';
import '../../core/auth/auth_provider.dart';
import '../../core/notifications/notification_service.dart';
import '../../core/theme/app_theme.dart';
import '../../core/theme/stitch_images.dart';
import '../../shared/models/models.dart';
import '../../shared/widgets/common_widgets.dart';
import '../my_bookings/bookings_provider.dart';

final myPackagesProvider = FutureProvider<List<UserPackage>>((ref) async {
  final dio = ref.watch(dioProvider);
  final response = await dio.get('/api/mobile/my-packages');
  return (response.data as List)
      .map((item) => UserPackage.fromJson(item as Map<String, dynamic>))
      .toList();
});

class HomeScreen extends ConsumerWidget {
  const HomeScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final authState = ref.watch(authProvider);
    final upcomingAsync = ref.watch(upcomingBookingsProvider);
    final userName = authState.userName ?? 'Привет';

    // Баннер «Приду / Не приду» доступен только в последний час перед занятием.
    Booking? attendanceTarget;
    if (upcomingAsync is AsyncData<List<Booking>>) {
      final now = DateTime.now();
      for (final b in upcomingAsync.value) {
        final start = b.startDateTime;
        if (!b.confirmedByClient &&
            start != null &&
            !start.isBefore(now) &&
            !start.isAfter(now.add(const Duration(hours: 1)))) {
          attendanceTarget = b;
          break;
        }
      }
    }

    return Scaffold(
      backgroundColor: AppColors.background,
      body: SafeArea(
        child: RefreshIndicator(
          onRefresh: () => ref.refresh(upcomingBookingsProvider.future),
          child: CustomScrollView(
            slivers: [
              // ── Top bar ───────────────────────────────────────────
              SliverToBoxAdapter(
                child: Padding(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                  child: Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      const Text(
                        'NOMAD Academy',
                        style: TextStyle(
                          fontSize: 22,
                          fontWeight: FontWeight.w700,
                          color: AppColors.primary,
                          letterSpacing: -0.5,
                        ),
                      ),
                      IconButton(
                        icon: const Icon(Icons.notifications_outlined,
                            color: AppColors.primary),
                        onPressed: () => context.push('/notifications'),
                      ),
                    ],
                  ),
                ),
              ),
              // ── Greeting ──────────────────────────────────────────
              SliverToBoxAdapter(
                child: Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        'Привет, ${userName.split(' ').first}! 👋',
                        style: const TextStyle(
                          fontSize: 26,
                          fontWeight: FontWeight.w700,
                          color: AppColors.onSurface,
                        ),
                      ),
                      const SizedBox(height: 4),
                      const Text(
                        'Твой путь к правам продолжается.',
                        style: TextStyle(
                          fontSize: 16,
                          color: AppColors.onSurfaceVariant,
                        ),
                      ),
                    ],
                  ),
                ),
              ),
              // ── Баннер подтверждения посещения ───────────────────
              if (attendanceTarget != null)
                SliverToBoxAdapter(
                  child: Padding(
                    padding: const EdgeInsets.fromLTRB(16, 16, 16, 0),
                    child: _AttendanceBanner(booking: attendanceTarget),
                  ),
                ),
              // ── Запрос разрешения уведомлений (при первом запуске) ──
              const SliverToBoxAdapter(child: _NotifPermissionPrompt()),

              // ── Balance card ──────────────────────────────────────
              const SliverToBoxAdapter(
                child: Padding(
                  padding: EdgeInsets.fromLTRB(16, 24, 16, 0),
                  child: _BalanceCard(),
                ),
              ),
              // ── Ближайшее занятие ─────────────────────────────────
              SliverToBoxAdapter(
                child: Padding(
                  padding: const EdgeInsets.fromLTRB(16, 24, 16, 0),
                  child: Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      const Text(
                        'Ближайшее занятие',
                        style: TextStyle(
                          fontSize: 20,
                          fontWeight: FontWeight.w500,
                          color: AppColors.onSurface,
                        ),
                      ),
                      GestureDetector(
                        onTap: () => context.go('/bookings'),
                        child: const Text(
                          'Все записи',
                          style: TextStyle(
                            fontSize: 14,
                            fontWeight: FontWeight.w500,
                            color: AppColors.accent,
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
              ),
              SliverToBoxAdapter(
                child: Padding(
                  padding: const EdgeInsets.fromLTRB(16, 12, 16, 0),
                  child: upcomingAsync.when(
                    loading: () => const _ShimmerBookingCard(),
                    error: (e, _) => Container(
                      padding: const EdgeInsets.all(16),
                      decoration: BoxDecoration(
                        color: Colors.white,
                        borderRadius: BorderRadius.circular(12),
                        border: Border.all(
                            color: AppColors.outlineVariant
                                .withValues(alpha: 0.3)),
                      ),
                      child: const Row(
                        children: [
                          Icon(Icons.info_outline,
                              color: AppColors.onSurfaceVariant, size: 20),
                          SizedBox(width: 8),
                          Expanded(
                            child: Text(
                              'Не удалось загрузить записи',
                              style: TextStyle(
                                  color: AppColors.onSurfaceVariant,
                                  fontSize: 14),
                            ),
                          ),
                        ],
                      ),
                    ),
                    data: (bookings) {
                      if (bookings.isEmpty) return const SizedBox.shrink();
                      return _NextBookingCard(booking: bookings.first);
                    },
                  ),
                ),
              ),
              // ── Быстрые действия ──────────────────────────────────
              const SliverToBoxAdapter(
                child: Padding(
                  padding: EdgeInsets.fromLTRB(16, 24, 16, 0),
                  child: Text(
                    'Быстрые действия',
                    style: TextStyle(
                      fontSize: 20,
                      fontWeight: FontWeight.w500,
                      color: AppColors.onSurface,
                    ),
                  ),
                ),
              ),
              SliverToBoxAdapter(
                child: Padding(
                  padding: const EdgeInsets.fromLTRB(16, 12, 16, 0),
                  child: Row(
                    children: [
                      Expanded(
                        child: _QuickActionCard(
                          icon: Icons.directions_car,
                          label: 'Записаться на\nзанятие',
                          color: AppColors.surfaceContainerHigh,
                          iconColor: AppColors.primary,
                          onTap: () =>
                              context.push('/booking/new?type=training'),
                        ),
                      ),
                      const SizedBox(width: 12),
                      Expanded(
                        child: _QuickActionCard(
                          icon: Icons.assignment_turned_in,
                          label: 'Пробный экзамен',
                          color: AppColors.surfaceVariant,
                          iconColor: AppColors.accent,
                          onTap: () => context.push('/booking/new?type=exam'),
                        ),
                      ),
                    ],
                  ),
                ),
              ),
              // ── Promo banner ──────────────────────────────────────
              SliverToBoxAdapter(
                child: Padding(
                  padding: const EdgeInsets.fromLTRB(16, 24, 16, 0),
                  child: _PromoBanner(),
                ),
              ),
              const SliverToBoxAdapter(child: SizedBox(height: 100)),
            ],
          ),
        ),
      ),
    );
  }
}

// ── Attendance banner (Приду / Не приду) ────────────────────────────────────

class _NotifPermissionPrompt extends ConsumerStatefulWidget {
  const _NotifPermissionPrompt();

  @override
  ConsumerState<_NotifPermissionPrompt> createState() =>
      _NotifPermissionPromptState();
}

class _NotifPermissionPromptState
    extends ConsumerState<_NotifPermissionPrompt> {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _maybeAsk());
  }

  Future<void> _maybeAsk() async {
    final prefs = await SharedPreferences.getInstance();
    if (prefs.getBool('notif_permission_asked') ?? false) return;
    if (!mounted) return;
    final granted = await showDialog<bool>(
      context: context,
      barrierDismissible: false,
      builder: (ctx) => AlertDialog(
        title: const Text('Уведомления'),
        content: const Text(
          'Разрешите уведомления, чтобы вовремя получать напоминания о занятиях '
          '(за 24 часа и за 1 час), подтверждения записи и другие важные сообщения.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Не сейчас'),
          ),
          ElevatedButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Разрешить'),
          ),
        ],
      ),
    );
    await prefs.setBool('notif_permission_asked', true);
    if (granted == true) {
      await NotificationService.instance.requestPermissions();
    }
  }

  @override
  Widget build(BuildContext context) => const SizedBox.shrink();
}

class _AttendanceBanner extends ConsumerStatefulWidget {
  final Booking booking;
  const _AttendanceBanner({required this.booking});

  @override
  ConsumerState<_AttendanceBanner> createState() => _AttendanceBannerState();
}

class _AttendanceBannerState extends ConsumerState<_AttendanceBanner> {
  bool _busy = false;

  Future<void> _respond(bool coming) async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      final dio = ref.read(dioProvider);
      await dio.post('/api/mobile/bookings/${widget.booking.id}/confirm',
          data: {'coming': coming});

      if (coming) {
        await NotificationService.instance
            .showEvent(NotificationService.kBookingConfirmed);
      }

      ref.invalidate(upcomingBookingsProvider);

      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(
                coming
                    ? 'Отлично, ждём вас на занятии!'
                    : 'Заявка на отмену отправлена администратору.'),
            backgroundColor: coming ? AppColors.success : AppColors.error,
          ),
        );
      }
    } on DioException catch (e) {
      final msg = (e.response?.data as Map?)?['detail'] ?? 'Ошибка';
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
              content: Text(msg.toString()), backgroundColor: AppColors.error),
        );
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final b = widget.booking;
    final dateLabel = formatDateShort(b.bookingDate);
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        gradient: const LinearGradient(
          colors: [AppColors.primary, AppColors.primaryContainer],
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
        ),
        borderRadius: BorderRadius.circular(16),
        boxShadow: AppColors.cardShadow,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Row(
            children: [
              Icon(Icons.event_available, color: Colors.white, size: 22),
              SizedBox(width: 8),
              Expanded(
                child: Text(
                  'Подтвердите занятие',
                  style: TextStyle(
                    color: Colors.white,
                    fontSize: 16,
                    fontWeight: FontWeight.w700,
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 6),
          Text(
            'Вы придёте на занятие $dateLabel в ${b.startTime}?',
            style:
                const TextStyle(color: Colors.white, fontSize: 14, height: 1.3),
          ),
          const SizedBox(height: 14),
          Row(
            children: [
              Expanded(
                child: ElevatedButton(
                  onPressed: _busy ? null : () => _respond(true),
                  style: ElevatedButton.styleFrom(
                    backgroundColor: Colors.white,
                    foregroundColor: AppColors.primary,
                    elevation: 0,
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(10),
                    ),
                    minimumSize: const Size(0, 46),
                  ),
                  child: const Text('Приду',
                      style: TextStyle(fontWeight: FontWeight.w700)),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: OutlinedButton(
                  onPressed: _busy ? null : () => _respond(false),
                  style: OutlinedButton.styleFrom(
                    foregroundColor: Colors.white,
                    side: const BorderSide(color: Colors.white70),
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(10),
                    ),
                    minimumSize: const Size(0, 46),
                  ),
                  child: const Text('Не приду',
                      style: TextStyle(fontWeight: FontWeight.w600)),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _BalanceCard extends ConsumerWidget {
  const _BalanceCard();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final packages = ref.watch(myPackagesProvider);
    return packages.when(
      loading: () => const SizedBox.shrink(),
      error: (_, __) => const SizedBox.shrink(),
      data: (items) {
        final active = items.where((item) => item.isActive).toList();
        if (active.isEmpty) return const SizedBox.shrink();
        final package = active.first;
        final expiry = package.expiresAt == null
            ? ''
            : ' до ${package.expiresAt!.substring(0, 10).split('-').reversed.join('.')}';
        return Container(
          width: double.infinity,
          padding: const EdgeInsets.all(16),
          decoration: BoxDecoration(
            color: AppColors.primary,
            borderRadius: BorderRadius.circular(16),
          ),
          child: Row(
            children: [
              const Icon(Icons.card_giftcard, color: Colors.white, size: 32),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(package.name,
                        style: const TextStyle(
                            color: Colors.white, fontWeight: FontWeight.w700)),
                    Text(
                        'Осталось занятий: ${package.remainingSessions}/${package.sessionsCount}$expiry',
                        style: const TextStyle(color: Colors.white70)),
                    if (package.remainingBonusExams > 0)
                      const Text('Пробный экзамен в подарок доступен',
                          style: TextStyle(color: Colors.white70)),
                  ],
                ),
              ),
            ],
          ),
        );
      },
    );
  }
}

// ── Next booking card ─────────────────────────────────────────────────────────

class _NextBookingCard extends StatelessWidget {
  final Booking booking;
  const _NextBookingCard({required this.booking});

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: () => context.push('/booking/${booking.id}'),
      child: Container(
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(
          color: Colors.white,
          borderRadius: BorderRadius.circular(12),
          boxShadow: AppColors.cardShadow,
          border: Border.all(
              color: AppColors.outlineVariant.withValues(alpha: 0.3)),
        ),
        child: Column(
          children: [
            Row(
              children: [
                ClipRRect(
                  borderRadius: BorderRadius.circular(24),
                  child: booking.instructor?.avatarUrl != null &&
                          booking.instructor!.avatarUrl!.isNotEmpty
                      ? Image.network(
                          booking.instructor!.avatarUrl!,
                          width: 48,
                          height: 48,
                          fit: BoxFit.cover,
                          errorBuilder: (_, __, ___) => Container(
                            width: 48,
                            height: 48,
                            decoration: const BoxDecoration(
                                color: AppColors.surfaceContainerHigh,
                                shape: BoxShape.circle),
                            child: const Icon(Icons.person,
                                color: AppColors.primary, size: 28),
                          ),
                        )
                      : InstructorAvatar(
                          name: booking.instructor?.name ?? '',
                          radius: 24,
                        ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        booking.instructor?.name ?? 'Инструктор',
                        style: const TextStyle(
                          fontSize: 16,
                          fontWeight: FontWeight.w600,
                          color: AppColors.primary,
                        ),
                      ),
                      const Text(
                        'Инструктор',
                        style: TextStyle(
                          fontSize: 12,
                          color: AppColors.onSurfaceVariant,
                        ),
                      ),
                    ],
                  ),
                ),
                Column(
                  crossAxisAlignment: CrossAxisAlignment.end,
                  children: [
                    Text(
                      booking.startTime,
                      style: const TextStyle(
                        fontSize: 20,
                        fontWeight: FontWeight.w700,
                        color: AppColors.primary,
                      ),
                    ),
                    Text(
                      formatDateShort(booking.bookingDate),
                      style: const TextStyle(
                        fontSize: 12,
                        color: AppColors.onSurfaceVariant,
                      ),
                    ),
                  ],
                ),
              ],
            ),
            const SizedBox(height: 12),
            Container(
              padding: const EdgeInsets.all(10),
              decoration: BoxDecoration(
                color: AppColors.surfaceContainerLow,
                borderRadius: BorderRadius.circular(8),
              ),
              child: Row(
                children: [
                  const Icon(Icons.location_on,
                      color: AppColors.accent, size: 18),
                  const SizedBox(width: 8),
                  Text(
                    booking.location,
                    style: const TextStyle(
                      fontSize: 14,
                      color: AppColors.onSurfaceVariant,
                    ),
                  ),
                  const Spacer(),
                  const Icon(Icons.chevron_right,
                      color: AppColors.onSurfaceVariant, size: 18),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

// ── Shimmer loading ───────────────────────────────────────────────────────────

class _ShimmerBookingCard extends StatelessWidget {
  const _ShimmerBookingCard();

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(12),
        border:
            Border.all(color: AppColors.outlineVariant.withValues(alpha: 0.3)),
      ),
      child: Column(
        children: [
          Row(
            children: [
              Container(
                width: 48,
                height: 48,
                decoration: const BoxDecoration(
                  color: AppColors.surfaceContainerHigh,
                  shape: BoxShape.circle,
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Container(
                        height: 16,
                        width: 120,
                        color: AppColors.surfaceContainerHigh),
                    const SizedBox(height: 4),
                    Container(
                        height: 12,
                        width: 80,
                        color: AppColors.surfaceContainerHigh),
                  ],
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

// ── Quick action card ─────────────────────────────────────────────────────────

class _QuickActionCard extends StatelessWidget {
  final IconData icon;
  final String label;
  final Color color;
  final Color iconColor;
  final VoidCallback onTap;

  const _QuickActionCard({
    required this.icon,
    required this.label,
    required this.color,
    required this.iconColor,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.all(16),
        height: 120,
        decoration: BoxDecoration(
          color: color,
          borderRadius: BorderRadius.circular(12),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Icon(icon, color: iconColor, size: 32),
            const Spacer(),
            Text(
              label,
              style: const TextStyle(
                fontSize: 16,
                fontWeight: FontWeight.w500,
                color: AppColors.primary,
                height: 1.2,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

// ── Promo banner ──────────────────────────────────────────────────────────────

class _PromoBanner extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return Container(
      height: 160,
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(16),
        boxShadow: AppColors.cardShadowLg,
      ),
      child: ClipRRect(
        borderRadius: BorderRadius.circular(16),
        child: Stack(
          fit: StackFit.expand,
          children: [
            // Background image
            Image.network(
              StitchImages.promoCar,
              fit: BoxFit.cover,
              errorBuilder: (_, __, ___) => Container(
                decoration: const BoxDecoration(
                  gradient: LinearGradient(
                    colors: [AppColors.primary, AppColors.primaryContainer],
                    begin: Alignment.topLeft,
                    end: Alignment.bottomRight,
                  ),
                ),
              ),
            ),
            // Dark overlay
            Container(
              decoration: BoxDecoration(
                gradient: LinearGradient(
                  colors: [
                    AppColors.primary.withValues(alpha: 0.8),
                    Colors.transparent
                  ],
                  begin: Alignment.centerLeft,
                  end: Alignment.centerRight,
                ),
              ),
            ),
            // Content
            const Padding(
              padding: EdgeInsets.all(20),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  Text(
                    'АКЦИЯ',
                    style: TextStyle(
                      color: AppColors.accent,
                      fontSize: 12,
                      fontWeight: FontWeight.w700,
                      letterSpacing: 1,
                    ),
                  ),
                  SizedBox(height: 8),
                  Text(
                    'Приведи друга —\nполучи скидку!',
                    style: TextStyle(
                      color: Colors.white,
                      fontSize: 18,
                      fontWeight: FontWeight.w700,
                      height: 1.3,
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

String formatDateShort(String date) {
  try {
    final parts = date.split('-');
    if (parts.length < 3) return date;
    final months = [
      '',
      'Января',
      'Февраля',
      'Марта',
      'Апреля',
      'Мая',
      'Июня',
      'Июля',
      'Августа',
      'Сентября',
      'Октября',
      'Ноября',
      'Декабря'
    ];
    final day = int.parse(parts[2]);
    final month = int.parse(parts[1]);
    final weekdays = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
    final dateObj = DateTime.parse(date);
    final weekday = weekdays[dateObj.weekday - 1];
    return '$day ${months[month]}, $weekday';
  } catch (_) {
    return date;
  }
}
