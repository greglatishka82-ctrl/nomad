import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'core/auth/auth_provider.dart';
import 'core/auth/router.dart';
import 'core/notifications/notification_service.dart';
import 'core/theme/app_theme.dart';

class NomadApp extends ConsumerStatefulWidget {
  const NomadApp({super.key});

  @override
  ConsumerState<NomadApp> createState() => _NomadAppState();
}

class _NomadAppState extends ConsumerState<NomadApp> {
  @override
  void initState() {
    super.initState();
    // Привязываем пользователя к OneSignal после логина
    WidgetsBinding.instance.addPostFrameCallback((_) {
      ref.listenManual(authProvider, (prev, next) {
        if (next.status == AuthStatus.authenticated && next.userId != null) {
          NotificationService.instance.loginUser(next.userId!);
        } else if (next.status == AuthStatus.unauthenticated) {
          NotificationService.instance.logoutUser();
          NotificationService.instance.cancelAllReminders();
        }
      });
    });
  }

  @override
  Widget build(BuildContext context) {
    final router = ref.watch(routerProvider);
    return MaterialApp.router(
      title: 'NOMAD',
      theme: AppTheme.light(),
      routerConfig: router,
      debugShowCheckedModeBanner: false,
      locale: const Locale('ru', 'RU'),
    );
  }
}
