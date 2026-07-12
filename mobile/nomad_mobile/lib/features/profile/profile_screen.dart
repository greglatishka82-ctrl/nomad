import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:dio/dio.dart';

import '../../core/api/api_client.dart';
import '../../core/auth/auth_provider.dart';
import '../../core/theme/app_theme.dart';
import '../../shared/models/models.dart';
import '../../shared/widgets/common_widgets.dart';

// ── Providers ─────────────────────────────────────────────────────────────────

final profileProvider = FutureProvider<UserProfile>((ref) async {
  final dio = ref.watch(dioProvider);
  final resp = await dio.get('/api/mobile/profile');
  return UserProfile.fromJson(resp.data as Map<String, dynamic>);
});

final myPackagesProvider = FutureProvider<List<UserPackage>>((ref) async {
  final dio = ref.watch(dioProvider);
  final resp = await dio.get('/api/mobile/my-packages');
  return (resp.data as List)
      .map((e) => UserPackage.fromJson(e as Map<String, dynamic>))
      .toList();
});

final myCertificatesProvider =
    FutureProvider<List<UserCertificate>>((ref) async {
  final dio = ref.watch(dioProvider);
  final resp = await dio.get('/api/mobile/certificates');
  return (resp.data as List)
      .map((e) => UserCertificate.fromJson(e as Map<String, dynamic>))
      .toList();
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
                loading: () =>
                    const Center(child: CircularProgressIndicator()),
                error: (e, _) => ErrorState(
                  message: 'Не удалось загрузить профиль',
                  onRetry: () => ref.refresh(profileProvider),
                ),
                data: (profile) => RefreshIndicator(
                  onRefresh: () => ref.refresh(profileProvider.future),
                  child: ListView(
                    padding: const EdgeInsets.all(16),
                    children: [
                      // ── Avatar ───────────────────────────────────
                      Center(
                        child: Column(
                          children: [
                            Container(
                              width: 96,
                              height: 96,
                              decoration: BoxDecoration(
                                color: AppColors.primaryContainer,
                                shape: BoxShape.circle,
                                border: Border.all(
                                  color: AppColors.surfaceContainerHigh,
                                  width: 4,
                                ),
                              ),
                              child: Center(
                                child: Text(
                                  profile.name.isNotEmpty
                                      ? profile.name
                                          .split(' ')
                                          .take(2)
                                          .map((w) => w[0].toUpperCase())
                                          .join()
                                      : '?',
                                  style: const TextStyle(
                                    fontSize: 28,
                                    fontWeight: FontWeight.w700,
                                    color: AppColors.onPrimaryContainer,
                                  ),
                                ),
                              ),
                            ),
                            const SizedBox(height: 12),
                            Text(
                              profile.name,
                              style: const TextStyle(
                                fontSize: 22,
                                fontWeight: FontWeight.w700,
                                color: AppColors.onSurface,
                              ),
                            ),
                            const SizedBox(height: 4),
                            Text(
                              profile.email,
                              style: const TextStyle(
                                fontSize: 16,
                                color: AppColors.onSurfaceVariant,
                              ),
                            ),
                          ],
                        ),
                      ),
                      const SizedBox(height: 24),

                      // ── Info card ────────────────────────────────
                      _InfoCard(profile: profile),
                      const SizedBox(height: 16),

                      // ── Progress ─────────────────────────────────
                      _PackageWidget(),
                      const SizedBox(height: 16),

                      // ── Menu ─────────────────────────────────────
                      _MenuButton(
                        icon: Icons.notifications,
                        title: 'Уведомления',
                        onTap: () => context.push('/notifications'),
                      ),
                      const SizedBox(height: 8),
                      _MenuButton(
                        icon: Icons.description,
                        title: 'Мои сертификаты',
                        onTap: () => context.push('/certificates'),
                      ),
                      const SizedBox(height: 8),
                      _MenuButton(
                        icon: Icons.chat_bubble,
                        title: 'Поддержка',
                        onTap: () => context.push('/support'),
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
          const SizedBox(height: 16),
          _InfoRow(
            icon: Icons.mail,
            label: 'Email',
            value: profile.email,
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
                GestureDetector(
                  onTap: () {},
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
            const Icon(Icons.chevron_right,
                color: AppColors.outline, size: 22),
          ],
        ),
      ),
    );
  }
}

// ── Package widget ────────────────────────────────────────────────────────────

class _PackageWidget extends ConsumerWidget {
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(myPackagesProvider);
    return async.when(
      loading: () => const ShimmerCard(),
      error: (_, __) => const SizedBox.shrink(),
      data: (pkgs) {
        final active = pkgs.where((p) => p.isActive).toList();
        if (active.isEmpty) {
          return const SizedBox.shrink();
        }
        final package = active.first;
        final progress = package.sessionsCount > 0
            ? (package.sessionsCount - package.remainingSessions) /
                package.sessionsCount
            : 0.0;

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
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  const Text(
                    'Мой прогресс',
                    style: TextStyle(
                      fontSize: 16,
                      fontWeight: FontWeight.w600,
                      color: AppColors.onSurface,
                    ),
                  ),
                  Text(
                    '${(progress * 100).toInt()}%',
                    style: const TextStyle(
                      fontSize: 14,
                      fontWeight: FontWeight.w600,
                      color: AppColors.accent,
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 12),
              ClipRRect(
                borderRadius: BorderRadius.circular(4),
                child: LinearProgressIndicator(
                  value: progress,
                  backgroundColor: AppColors.surfaceContainer,
                  color: AppColors.accent,
                  minHeight: 12,
                ),
              ),
              const SizedBox(height: 12),
              Text(
                '${package.remainingSessions} из ${package.sessionsCount} занятий',
                style: const TextStyle(
                  fontSize: 16,
                  color: AppColors.onSurfaceVariant,
                ),
              ),
            ],
          ),
        );
      },
    );
  }
}
