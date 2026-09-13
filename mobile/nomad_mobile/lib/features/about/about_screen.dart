import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../core/api/api_client.dart';
import '../../core/theme/app_theme.dart';

class AboutScreen extends ConsumerWidget {
  const AboutScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final configAsync = ref.watch(appConfigProvider);

    return Scaffold(
      backgroundColor: AppColors.background,
      appBar: AppBar(
        title: const Text('О NOMAD'),
      ),
      body: configAsync.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (_, __) => const Center(child: Text('Ошибка загрузки')),
        data: (config) => ListView(
          padding: const EdgeInsets.all(16),
          children: [
            // ── Logo / Name ──────────────────────────────
            Center(
              child: Column(
                children: [
                  Container(
                    width: 80,
                    height: 80,
                    decoration: BoxDecoration(
                      color: AppColors.primary,
                      borderRadius: BorderRadius.circular(20),
                    ),
                    child: const Center(
                      child: Text(
                        'N',
                        style: TextStyle(
                          color: Colors.white,
                          fontSize: 36,
                          fontWeight: FontWeight.w800,
                        ),
                      ),
                    ),
                  ),
                  const SizedBox(height: 16),
                  const Text(
                    'NOMAD Driving Academy',
                    style: TextStyle(
                      fontSize: 22,
                      fontWeight: FontWeight.w700,
                      color: AppColors.onSurface,
                    ),
                  ),
                  const SizedBox(height: 4),
                  const Text(
                    'АвтоПрактик №1',
                    style: TextStyle(
                      fontSize: 14,
                      color: AppColors.onSurfaceVariant,
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 32),

            // ── Contact info ─────────────────────────────
            _InfoSection(
              title: 'Контакты',
              children: [
                _InfoRow(icon: Icons.phone, text: config.phone),
                _InfoRow(icon: Icons.location_on, text: config.locationMain),
                if (config.locationExam.trim() != config.locationMain.trim())
                  _InfoRow(icon: Icons.location_on, text: config.locationExam),
                const _InfoRow(
                  icon: Icons.access_time,
                  text: 'Без выходных, с 09:00 до 19:00',
                ),
              ],
            ),
            const SizedBox(height: 24),

            // ── About ────────────────────────────────────
            const _InfoSection(
              title: 'О нас',
              children: [
                _InfoText(
                  text:
                      'Мы обучаем вождению на автомобилях Chevrolet Cobalt с механической и автоматической коробками передач.',
                ),
                SizedBox(height: 12),
                _InfoText(
                  text:
                      'Наша миссия — сделать обучение вождению комфортным, безопасным и доступным для каждого. '
                      'Индивидуальный подход, опытные инструкторы и современные методики.',
                ),
              ],
            ),
            const SizedBox(height: 24),

            // ── Stats ────────────────────────────────────
            const _InfoSection(
              title: 'Наши результаты',
              children: [
                _StatRow(label: 'Лицензия', value: 'Государственная'),
                _StatRow(label: 'Процент сдачи', value: '98%'),
                _StatRow(label: 'Опыт инструкторов', value: 'от 5 лет'),
                _StatRow(label: 'Рейтинг', value: '4.9 из 5.0'),
              ],
            ),
            const SizedBox(height: 24),

            // ── Social Media ─────────────────────────────
            _InfoSection(
              title: 'Мы в сети',
              children: [
                _ActionRow(
                  icon: Icons.language,
                  text: 'Официальный сайт',
                  onTap: () => _launchURL('https://nomadrive.vercel.app/'),
                ),
                const Divider(height: 1),
                _ActionRow(
                  icon: Icons.camera_alt_outlined,
                  text: 'Instagram',
                  onTap: () => _launchURL('https://www.instagram.com/autodrom_nomad/'),
                ),
                const Divider(height: 1),
                _ActionRow(
                  icon: Icons.telegram,
                  text: 'Телеграм',
                  onTap: () => _launchURL('https://t.me/nomadrive_bot'),
                ),
              ],
            ),
            const SizedBox(height: 24),

            // ── Rate app ─────────────────────────────────
            SizedBox(
              width: double.infinity,
              height: 52,
              child: ElevatedButton(
                onPressed: () => _showRatingDialog(context, ref),
                style: ElevatedButton.styleFrom(
                  backgroundColor: AppColors.primary,
                  foregroundColor: Colors.white,
                  elevation: 0,
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(12),
                  ),
                ),
                child: const Text(
                  'Оценить приложение',
                  style: TextStyle(
                    fontSize: 16,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ),
            ),
            const SizedBox(height: 32),

            // ── Version ──────────────────────────────────
            const Center(
              child: Text(
                'Версия 1.0.0',
                style: TextStyle(
                  color: AppColors.textHint,
                  fontSize: 12,
                ),
              ),
            ),
            const SizedBox(height: 80),
          ],
        ),
      ),
    );
  }

  Future<void> _launchURL(String urlString) async {
    final uri = Uri.parse(urlString);
    if (await canLaunchUrl(uri)) {
      await launchUrl(uri, mode: LaunchMode.externalApplication);
    }
  }

  void _showRatingDialog(BuildContext context, WidgetRef ref) {
    showDialog(
      context: context,
      barrierDismissible: false,
      builder: (_) => _RatingDialog(ref: ref),
    );
  }
}

// ── Диалог оценки приложения ─────────────────────────────────────────────────

class _RatingDialog extends StatefulWidget {
  final WidgetRef ref;
  const _RatingDialog({required this.ref});

  @override
  State<_RatingDialog> createState() => _RatingDialogState();
}

class _RatingDialogState extends State<_RatingDialog> {
  int _selectedStars = 0;
  bool _sending = false;
  bool _sent = false;

  @override
  Widget build(BuildContext context) {
    if (_sent) {
      return AlertDialog(
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
        content: const Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.check_circle_outline,
                color: Colors.green, size: 56),
            SizedBox(height: 12),
            Text(
              'Спасибо за оценку!',
              style: TextStyle(
                fontSize: 18,
                fontWeight: FontWeight.w700,
              ),
            ),
            SizedBox(height: 6),
            Text(
              'Ваш отзыв поможет нам стать лучше.',
              textAlign: TextAlign.center,
              style: TextStyle(color: Colors.grey),
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(),
            child: const Text('Закрыть'),
          ),
        ],
      );
    }

    return AlertDialog(
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
      title: const Text(
        'Оцените приложение',
        textAlign: TextAlign.center,
        style: TextStyle(fontWeight: FontWeight.w700),
      ),
      content: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          const Text(
            'Насколько вам нравится NOMAD?',
            textAlign: TextAlign.center,
            style: TextStyle(color: Colors.grey),
          ),
          const SizedBox(height: 20),
          Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: List.generate(5, (i) {
              final star = i + 1;
              return GestureDetector(
                onTap: () => setState(() => _selectedStars = star),
                child: Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 4),
                  child: Icon(
                    star <= _selectedStars ? Icons.star : Icons.star_border,
                    color: Colors.amber,
                    size: 40,
                  ),
                ),
              );
            }),
          ),
          const SizedBox(height: 8),
          Text(
            _starLabel(_selectedStars),
            style: TextStyle(
              color: _selectedStars > 0 ? AppColors.primary : Colors.transparent,
              fontWeight: FontWeight.w600,
            ),
          ),
        ],
      ),
      actions: [
        TextButton(
          onPressed: _sending ? null : () => Navigator.of(context).pop(),
          child: const Text('Отмена'),
        ),
        ElevatedButton(
          onPressed: (_selectedStars == 0 || _sending)
              ? null
              : () async {
                  setState(() => _sending = true);
                  try {
                    final dio = widget.ref.read(dioProvider);
                    await dio.post('/api/mobile/app-review', data: {
                      'stars': _selectedStars,
                    });
                    setState(() {
                      _sending = false;
                      _sent = true;
                    });
                  } catch (_) {
                    setState(() => _sending = false);
                    if (context.mounted) {
                      ScaffoldMessenger.of(context).showSnackBar(
                        const SnackBar(
                          content: Text('Не удалось отправить оценку. Попробуйте позже.'),
                        ),
                      );
                    }
                  }
                },
          style: ElevatedButton.styleFrom(
            backgroundColor: AppColors.primary,
            foregroundColor: Colors.white,
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(8),
            ),
          ),
          child: _sending
              ? const SizedBox(
                  width: 16,
                  height: 16,
                  child: CircularProgressIndicator(
                    strokeWidth: 2,
                    color: Colors.white,
                  ),
                )
              : const Text('Отправить'),
        ),
      ],
    );
  }

  String _starLabel(int stars) {
    switch (stars) {
      case 1:
        return 'Очень плохо';
      case 2:
        return 'Плохо';
      case 3:
        return 'Нормально';
      case 4:
        return 'Хорошо';
      case 5:
        return 'Отлично!';
      default:
        return '';
    }
  }
}

// ── Вспомогательные виджеты ───────────────────────────────────────────────────

class _InfoSection extends StatelessWidget {
  final String title;
  final List<Widget> children;
  const _InfoSection({required this.title, required this.children});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: AppColors.outlineVariant),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            title,
            style: const TextStyle(
              fontSize: 16,
              fontWeight: FontWeight.w700,
              color: AppColors.onSurface,
            ),
          ),
          const SizedBox(height: 12),
          ...children,
        ],
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
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Row(
        children: [
          Icon(icon, size: 18, color: AppColors.accent),
          const SizedBox(width: 10),
          Expanded(
            child: Text(
              text,
              style: const TextStyle(
                fontSize: 14,
                color: AppColors.onSurface,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _InfoText extends StatelessWidget {
  final String text;
  const _InfoText({required this.text});

  @override
  Widget build(BuildContext context) {
    return Text(
      text,
      style: const TextStyle(
        fontSize: 14,
        color: AppColors.onSurfaceVariant,
        height: 1.5,
      ),
    );
  }
}

class _StatRow extends StatelessWidget {
  final String label;
  final String value;
  const _StatRow({required this.label, required this.value});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Text(
            label,
            style: const TextStyle(
              fontSize: 14,
              color: AppColors.onSurfaceVariant,
            ),
          ),
          Text(
            value,
            style: const TextStyle(
              fontSize: 14,
              fontWeight: FontWeight.w600,
              color: AppColors.onSurface,
            ),
          ),
        ],
      ),
    );
  }
}

class _ActionRow extends StatelessWidget {
  final IconData icon;
  final String text;
  final VoidCallback onTap;

  const _ActionRow({
    required this.icon,
    required this.text,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(8),
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 12),
        child: Row(
          children: [
            Icon(icon, size: 24, color: AppColors.primary),
            const SizedBox(width: 12),
            Expanded(
              child: Text(
                text,
                style: const TextStyle(
                  fontSize: 16,
                  color: AppColors.onSurface,
                  fontWeight: FontWeight.w500,
                ),
              ),
            ),
            const Icon(Icons.chevron_right, size: 20, color: AppColors.outline),
          ],
        ),
      ),
    );
  }
}
