import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/theme/app_theme.dart';
import '../../core/utils/formatters.dart';
import '../../shared/models/models.dart';
import '../../shared/widgets/common_widgets.dart';
import 'bookings_provider.dart';

class MyBookingsScreen extends ConsumerStatefulWidget {
  const MyBookingsScreen({super.key});

  @override
  ConsumerState<MyBookingsScreen> createState() => _MyBookingsScreenState();
}

class _MyBookingsScreenState extends ConsumerState<MyBookingsScreen>
    with SingleTickerProviderStateMixin {
  late TabController _tabs;

  @override
  void initState() {
    super.initState();
    _tabs = TabController(length: 2, vsync: this);
  }

  @override
  void dispose() {
    _tabs.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Мои записи'),
        bottom: TabBar(
          controller: _tabs,
          labelColor: Colors.white,
          unselectedLabelColor: Colors.white60,
          indicatorColor: AppColors.accent,
          tabs: const [
            Tab(text: 'Предстоящие'),
            Tab(text: 'История'),
          ],
        ),
      ),
      floatingActionButton: FloatingActionButton.extended(
        backgroundColor: AppColors.accent,
        icon: const Icon(Icons.add, color: Colors.white),
        label: const Text('Записаться',
            style: TextStyle(color: Colors.white, fontWeight: FontWeight.w600)),
        onPressed: () => context.push('/booking/new'),
      ),
      body: TabBarView(
        controller: _tabs,
        children: [
          _BookingsList(
            provider: upcomingBookingsProvider,
            emptyIcon: Icons.calendar_month_outlined,
            emptyTitle: 'Нет предстоящих занятий',
            emptySubtitle: 'Запишитесь на урок вождения или пробный экзамен',
          ),
          _BookingsList(
            provider: historyBookingsProvider,
            emptyIcon: Icons.history,
            emptyTitle: 'История пуста',
            emptySubtitle: 'Здесь будут ваши завершённые занятия',
          ),
        ],
      ),
    );
  }
}

class _BookingsList extends ConsumerWidget {
  final ProviderListenable<AsyncValue<List<Booking>>> provider;
  final IconData emptyIcon;
  final String emptyTitle;
  final String emptySubtitle;

  const _BookingsList({
    required this.provider,
    required this.emptyIcon,
    required this.emptyTitle,
    required this.emptySubtitle,
  });

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(provider);
    return async.when(
      loading: () => ListView(
        children: List.generate(3, (_) => const ShimmerCard()),
      ),
      error: (e, _) => ErrorState(
        message: 'Не удалось загрузить записи',
        onRetry: () => ref.refresh(provider as Refreshable),
      ),
      data: (bookings) {
        if (bookings.isEmpty) {
          return EmptyState(
            icon: emptyIcon,
            title: emptyTitle,
            subtitle: emptySubtitle,
          );
        }
        return RefreshIndicator(
          onRefresh: () => ref.refresh(provider as Refreshable).future,
          child: ListView.builder(
            padding: const EdgeInsets.symmetric(vertical: 8),
            itemCount: bookings.length,
            itemBuilder: (_, i) => BookingCard(booking: bookings[i]),
          ),
        );
      },
    );
  }
}

class BookingCard extends StatelessWidget {
  final Booking booking;
  const BookingCard({super.key, required this.booking});

  @override
  Widget build(BuildContext context) {
    return Card(
      child: InkWell(
        borderRadius: BorderRadius.circular(16),
        onTap: () => context.push('/booking/${booking.id}'),
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Expanded(
                    child: Text(
                      serviceTypeLabel(booking.serviceType),
                      style: const TextStyle(
                          fontWeight: FontWeight.w700,
                          fontSize: 15,
                          color: AppColors.textPrimary),
                    ),
                  ),
                  StatusBadge(status: booking.status),
                ],
              ),
              const SizedBox(height: 8),
              _InfoRow(
                  icon: Icons.calendar_today_outlined,
                  text: formatDate(booking.bookingDate)),
              const SizedBox(height: 4),
              _InfoRow(
                  icon: Icons.access_time,
                  text: '${booking.startTime} — ${booking.endTime}'),
              const SizedBox(height: 4),
              _InfoRow(
                  icon: Icons.location_on_outlined,
                  text: booking.location),
              if (booking.instructor != null) ...[
                const SizedBox(height: 4),
                _InfoRow(
                    icon: Icons.person_outline,
                    text: booking.instructor!.name),
              ],
              const SizedBox(height: 12),
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Text(formatPrice(booking.price),
                      style: const TextStyle(
                          fontWeight: FontWeight.w700,
                          fontSize: 16,
                          color: AppColors.primary)),
                  Text(transmissionLabel(booking.transmission),
                      style: const TextStyle(
                          color: AppColors.textSecondary, fontSize: 12)),
                ],
              ),
              if (booking.canRate) ...[
                const SizedBox(height: 12),
                SizedBox(
                  width: double.infinity,
                  child: OutlinedButton.icon(
                    icon: const Icon(Icons.star_outline, size: 18),
                    label: const Text('Оценить занятие'),
                    onPressed: () => context.push('/booking/${booking.id}'),
                  ),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

class _InfoRow extends StatelessWidget {
  final IconData icon;
  final String text;
  const _InfoRow({required this.icon, required this.text});

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Icon(icon, size: 14, color: AppColors.textSecondary),
        const SizedBox(width: 6),
        Expanded(
          child: Text(text,
              style: const TextStyle(
                  color: AppColors.textSecondary, fontSize: 13)),
        ),
      ],
    );
  }
}
