import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/api/api_client.dart';
import '../../core/auth/auth_provider.dart';
import '../../core/theme/app_theme.dart';
import '../../shared/models/models.dart';
import '../../shared/widgets/common_widgets.dart';

// ── Providers ─────────────────────────────────────────────────────────────────

final profileProvider = FutureProvider<UserProfile>((ref) async {
  final dio = ref.watch(dioProvider);
  final resp = await dio.get('/api/mobile/profile');
  final data = resp.data as Map<String, dynamic>;
  return UserProfile.fromJson(data);
});

final referralProvider = FutureProvider<ReferralInfo>((ref) async {
  final dio = ref.watch(dioProvider);
  final resp = await dio.get('/api/mobile/referral');
  return ReferralInfo.fromJson(resp.data as Map<String, dynamic>);
});

// ── Screen ───────────────────────────────────────────────────────────────────

class ProfileScreen extends ConsumerWidget {
  const ProfileScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final profileAsync = ref.watch(profileProvider);

    return Scaffold(
      backgroundColor: AppColors.background,
      body: SafeArea(
        child: Column(
          children: [
            // ── Top bar (dark navy) ────────────────────────────────
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
              color: AppColors.primaryContainer,
              child: Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  const Text(
                    'Профиль',
                    style: TextStyle(
                      fontSize: 22,
                      fontWeight: FontWeight.w700,
                      color: Colors.white,
                    ),
                  ),
                  IconButton(
                    icon: const Icon(Icons.settings, color: Colors.white),
                    onPressed: () => context.push('/profile/edit'),
                  ),
                ],
              ),
            ),
            // ── Content ────────────────────────────────────────────
            Expanded(
              child: profileAsync.when(
                loading: () => const Center(child: CircularProgressIndicator()),
                error: (e, _) => ErrorState(
                  message: 'Не удалось загрузить профиль',
                  onRetry: () => ref.refresh(profileProvider),
                ),
                data: (profile) => RefreshIndicator(
                  onRefresh: () => ref.refresh(profileProvider.future),
                  child: ListView(
                    padding: const EdgeInsets.all(16),
                    children: [
                      Center(
                        child: Column(
                          children: [
                            Text(
                              profile.name,
                              textAlign: TextAlign.center,
                              style: const TextStyle(
                                fontSize: 22,
                                fontWeight: FontWeight.w700,
                                color: AppColors.onSurface,
                              ),
                            ),
                          ],
                        ),
                      ),
                      const SizedBox(height: 24),

                      // ── Info card ────────────────────────────────
                      _InfoCard(profile: profile),
                      const SizedBox(height: 16),

                      // ── Menu ─────────────────────────────────────
                      _MenuButton(
                        icon: Icons.notifications,
                        title: 'Уведомления',
                        onTap: () => context.push('/notifications'),
                      ),
                      const SizedBox(height: 8),
                      _MenuButton(
                        icon: Icons.info,
                        title: 'О NOMAD',
                        onTap: () => context.push('/about'),
                      ),
                      const SizedBox(height: 16),

                      // ── Logout ───────────────────────────────────
                      SizedBox(
                        width: double.infinity,
                        height: 52,
                        child: ElevatedButton(
                          onPressed: () async {
                            await ref.read(authProvider.notifier).logout();
                            if (context.mounted) context.go('/login');
                          },
                          style: ElevatedButton.styleFrom(
                            backgroundColor: AppColors.error,
                            foregroundColor: Colors.white,
                            elevation: 0,
                            shape: RoundedRectangleBorder(
                              borderRadius: BorderRadius.circular(12),
                            ),
                          ),
                          child: const Text(
                            'Выйти',
                            style: TextStyle(
                              fontSize: 16,
                              fontWeight: FontWeight.w600,
                            ),
                          ),
                        ),
                      ),
                      const SizedBox(height: 80),
                    ],
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _InfoCard extends StatelessWidget {
  final UserProfile profile;
  const _InfoCard({required this.profile});

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
        children: [
          _InfoRow(
            icon: Icons.phone,
            label: 'Телефон',
            value: profile.phone,
          ),
          if (profile.referralCode != null) ...[
            const Divider(height: 24, color: AppColors.surfaceContainerHigh),
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                _InfoRow(
                  icon: Icons.qr_code,
                  label: 'Реферальный код',
                  value: profile.referralCode!,
                  bold: true,
                ),
                Builder(
                  builder: (context) => GestureDetector(
                    onTap: () async {
                      await Clipboard.setData(
                          ClipboardData(text: profile.referralCode!));
                      if (context.mounted) {
                        ScaffoldMessenger.of(context).showSnackBar(
                          const SnackBar(
                            content: Text('Код скопирован'),
                            duration: Duration(seconds: 2),
                          ),
                        );
                      }
                    },
                    child: Container(
                      padding: const EdgeInsets.all(8),
                      decoration: BoxDecoration(
                        color: AppColors.surfaceContainer,
                        borderRadius: BorderRadius.circular(8),
                      ),
                      child: const Icon(Icons.content_copy,
                          size: 20, color: AppColors.accent),
                    ),
                  ),
                ),
              ],
            ),
          ],
        ],
      ),
    );
  }
}

class _InfoRow extends StatelessWidget {
  final IconData icon;
  final String label;
  final String value;
  final bool bold;

  const _InfoRow({
    required this.icon,
    required this.label,
    required this.value,
    this.bold = false,
  });

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Icon(icon, size: 22, color: AppColors.accent),
        const SizedBox(width: 12),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                label,
                style: const TextStyle(
                  fontSize: 12,
                  color: AppColors.onSurfaceVariant,
                ),
              ),
              const SizedBox(height: 2),
              Text(
                value,
                style: TextStyle(
                  fontSize: 16,
                  fontWeight: bold ? FontWeight.bold : FontWeight.w600,
                  color: AppColors.onSurface,
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }
}

// ── Menu button ───────────────────────────────────────────────────────────────

class _MenuButton extends StatelessWidget {
  final IconData icon;
  final String title;
  final VoidCallback onTap;

  const _MenuButton({
    required this.icon,
    required this.title,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(
          color: Colors.white,
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: AppColors.outlineVariant),
        ),
        child: Row(
          children: [
            Icon(icon, color: AppColors.primaryContainer, size: 22),
            const SizedBox(width: 12),
            Expanded(
              child: Text(
                title,
                style: const TextStyle(
                  fontSize: 16,
                  fontWeight: FontWeight.w600,
                  color: AppColors.onSurface,
                ),
              ),
            ),
            const Icon(Icons.chevron_right, color: AppColors.outline, size: 22),
          ],
        ),
      ),
    );
  }
}
