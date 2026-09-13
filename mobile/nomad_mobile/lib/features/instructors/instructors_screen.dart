import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../core/api/api_client.dart';
import '../../core/theme/app_theme.dart';
import '../../shared/models/models.dart';
import '../../shared/widgets/common_widgets.dart';
import '../faq/faq_screen.dart';

final instructorsProvider = FutureProvider<List<Instructor>>((ref) async {
  final dio = ref.watch(dioProvider);
  final resp = await dio.get('/api/instructors');
  return (resp.data as List)
      .map((e) => Instructor.fromJson(e as Map<String, dynamic>))
      .toList();
});

class InstructorsScreen extends ConsumerStatefulWidget {
  const InstructorsScreen({super.key});

  @override
  ConsumerState<InstructorsScreen> createState() =>
      _InstructorsScreenState();
}

class _InstructorsScreenState extends ConsumerState<InstructorsScreen>
    with SingleTickerProviderStateMixin {
  late TabController _tabs;

  @override
  void initState() {
    super.initState();
    _tabs = TabController(length: 3, vsync: this);
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
        title: const Text('Инфо'),
        bottom: TabBar(
          controller: _tabs,
          isScrollable: true,
          labelColor: AppColors.primary,
          unselectedLabelColor: AppColors.onSurfaceVariant,
          indicatorColor: AppColors.accent,
          tabs: const [
            Tab(text: 'Инструкторы'),
            Tab(text: 'FAQ'),
            Tab(text: 'Контакты'),
          ],
        ),
      ),
      body: TabBarView(
        controller: _tabs,
        children: [
          _InstructorsList(),
          const FaqBody(),
          const _ContactsTab(),
        ],
      ),
    );
  }
}

class _InstructorsList extends ConsumerStatefulWidget {
  @override
  ConsumerState<_InstructorsList> createState() =>
      _InstructorsListState();
}

class _InstructorsListState extends ConsumerState<_InstructorsList> {
  String _filter = 'all';

  @override
  Widget build(BuildContext context) {
    final async = ref.watch(instructorsProvider);
    return Column(
      children: [
        // Фильтры
        Padding(
          padding: const EdgeInsets.all(12),
          child: Row(
            children: [
              _FilterChip(
                  label: 'Все',
                  selected: _filter == 'all',
                  onTap: () => setState(() => _filter = 'all')),
              const SizedBox(width: 8),
              _FilterChip(
                  label: 'Механика',
                  selected: _filter == 'manual',
                  onTap: () => setState(() => _filter = 'manual')),
              const SizedBox(width: 8),
              _FilterChip(
                  label: 'Автомат',
                  selected: _filter == 'automatic',
                  onTap: () =>
                      setState(() => _filter = 'automatic')),
            ],
          ),
        ),
        Expanded(
          child: async.when(
            loading: () => ListView(
                children:
                    List.generate(3, (_) => const ShimmerCard())),
            error: (e, _) => ErrorState(
              message: 'Не удалось загрузить инструкторов',
              onRetry: () => ref.refresh(instructorsProvider),
            ),
            data: (all) {
              final filtered = _filter == 'all'
                  ? all
                  : all
                      .where((i) =>
                          i.transmission == _filter ||
                          i.transmission == 'both')
                      .toList();
              if (filtered.isEmpty) {
                return const EmptyState(
                  icon: Icons.person_off_outlined,
                  title: 'Нет инструкторов',
                );
              }
              return ListView.builder(
                padding: const EdgeInsets.symmetric(
                    horizontal: 16, vertical: 4),
                itemCount: filtered.length,
                itemBuilder: (_, i) =>
                    _InstructorCard(instructor: filtered[i]),
              );
            },
          ),
        ),
      ],
    );
  }
}

class _InstructorCard extends StatelessWidget {
  final Instructor instructor;
  const _InstructorCard({required this.instructor});

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(instructor.name,
                style: const TextStyle(
                    fontWeight: FontWeight.w700, fontSize: 16)),
            const SizedBox(height: 4),
            Row(
              children: [
                const Icon(Icons.work_outline,
                    size: 14,
                    color: AppColors.textSecondary),
                const SizedBox(width: 4),
                Text(
                    '${instructor.experienceYears} лет',
                    style: const TextStyle(
                        color: AppColors.textSecondary,
                        fontSize: 13)),
              ],
            ),
            const SizedBox(height: 6),
            Container(
              padding: const EdgeInsets.symmetric(
                  horizontal: 8, vertical: 3),
              decoration: BoxDecoration(
                color: AppColors.primary.withValues(alpha: 0.08),
                borderRadius: BorderRadius.circular(8),
              ),
              child: Text(
                _transLabel(instructor.transmission),
                style: const TextStyle(
                    color: AppColors.primary,
                    fontSize: 11,
                    fontWeight: FontWeight.w600),
              ),
            ),
            if (instructor.description.isNotEmpty) ...[
              const SizedBox(height: 8),
              Text(instructor.description,
                  style: const TextStyle(
                      color: AppColors.textSecondary,
                      fontSize: 13,
                      height: 1.4)),
            ],
          ],
        ),
      ),
    );
  }

  String _transLabel(String t) {
    switch (t) {
      case 'manual':
        return 'Механика';
      case 'automatic':
        return 'Автомат';
      default:
        return 'Механика и автомат';
    }
  }
}

class _FilterChip extends StatelessWidget {
  final String label;
  final bool selected;
  final VoidCallback onTap;
  const _FilterChip(
      {required this.label,
      required this.selected,
      required this.onTap});

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 150),
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
        decoration: BoxDecoration(
          color: selected ? AppColors.primary : Colors.white,
          borderRadius: BorderRadius.circular(20),
          border: Border.all(
              color: selected ? AppColors.primary : AppColors.divider),
        ),
        child: Text(
          label,
          style: TextStyle(
            color: selected ? Colors.white : AppColors.textSecondary,
            fontSize: 13,
            fontWeight:
                selected ? FontWeight.w600 : FontWeight.normal,
          ),
        ),
      ),
    );
  }
}

class _ContactsTab extends ConsumerWidget {
  const _ContactsTab();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final configAsync = ref.watch(appConfigProvider);
    return configAsync.when(
      loading: () => const Center(child: CircularProgressIndicator()),
      error: (_, __) => const Center(child: Text('Ошибка загрузки')),
      data: (config) => ListView(
        padding: const EdgeInsets.all(16),
        children: [
          _ContactItem(
            icon: Icons.phone,
            title: 'Телефон',
            subtitle: config.phone,
            color: AppColors.success,
          ),
          _ContactItem(
            icon: Icons.location_on,
            title: 'Учебная площадка',
            subtitle: config.locationMain,
            color: AppColors.primary,
          ),
          _ContactItem(
            icon: Icons.location_on,
            title: 'Экзаменационная площадка',
            subtitle: config.locationExam,
            color: AppColors.accent,
          ),
          const _ContactItem(
            icon: Icons.access_time,
            title: 'Режим работы',
            subtitle: 'Без выходных, 9:00–19:00',
            color: AppColors.warning,
          ),
          const SizedBox(height: 16),
          const _ContactLink(
            icon: Icons.camera_alt_outlined,
            title: 'Инстаграм',
            url: 'https://www.instagram.com/autodrom_nomad/',
          ),
          const _ContactLink(
            icon: Icons.telegram,
            title: 'Телеграм',
            url: 'https://t.me/nomadrive_bot',
          ),
        ],
      ),
    );
  }
}

class _ContactItem extends StatelessWidget {
  final IconData icon;
  final String title;
  final String subtitle;
  final Color color;
  const _ContactItem({
    required this.icon,
    required this.title,
    required this.subtitle,
    required this.color,
  });

  @override
  Widget build(BuildContext context) {
    return Card(
      child: ListTile(
        leading: CircleAvatar(
          backgroundColor: color.withValues(alpha: 0.12),
          child: Icon(icon, color: color),
        ),
        title: Text(title,
            style: const TextStyle(
                fontWeight: FontWeight.w600, fontSize: 14)),
        subtitle: Text(subtitle,
            style: const TextStyle(color: AppColors.textSecondary)),
      ),
    );
  }
}

class _ContactLink extends StatelessWidget {
  final IconData icon;
  final String title;
  final String url;
  const _ContactLink({
    required this.icon,
    required this.title,
    required this.url,
  });

  Future<void> _launchURL(String urlString) async {
    final uri = Uri.parse(urlString);
    if (await canLaunchUrl(uri)) {
      await launchUrl(uri, mode: LaunchMode.externalApplication);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Card(
      child: ListTile(
        leading: CircleAvatar(
          backgroundColor: AppColors.primary.withValues(alpha: 0.12),
          child: Icon(icon, color: AppColors.primary),
        ),
        title: Text(title,
            style: const TextStyle(
                fontWeight: FontWeight.w600, fontSize: 14)),
        trailing: const Icon(Icons.open_in_new, size: 18, color: AppColors.primary),
        onTap: () => _launchURL(url),
      ),
    );
  }
}
