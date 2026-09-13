import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import 'auth_provider.dart';
import '../../features/auth/login_screen.dart';
import '../../features/auth/register_screen.dart';
import '../../features/auth/forgot_password_screen.dart';
import '../../features/auth/onboarding_screen.dart';
import '../../features/home/home_screen.dart';
import '../../features/booking/booking_wizard_screen.dart';
import '../../features/my_bookings/my_bookings_screen.dart';
import '../../features/my_bookings/booking_detail_screen.dart';
import '../../features/profile/profile_screen.dart';
import '../../features/profile/edit_profile_screen.dart';
import '../../features/referral/referral_screen.dart';
import '../../features/instructors/instructors_screen.dart';
import '../../features/faq/faq_screen.dart';
import '../../features/support_chat/support_chat_screen.dart';
import '../../features/notifications/notification_settings_screen.dart';
import '../../features/about/about_screen.dart';
import '../../shared/widgets/main_shell.dart';

final routerProvider = Provider<GoRouter>((ref) {
  final authState = ref.watch(authProvider);

  return GoRouter(
    initialLocation: '/',
    redirect: (context, state) {
      final isAuth = authState.status == AuthStatus.authenticated;
      final isUnknown = authState.status == AuthStatus.unknown;
      final location = state.uri.path;

      if (isUnknown) return null; // Ждём проверки
      if (!isAuth && location != '/login' && location != '/register' &&
          location != '/forgot-password' && location != '/onboarding') {
        return '/onboarding';
      }
      if (isAuth && (location == '/login' || location == '/register' ||
          location == '/onboarding')) {
        return '/home';
      }
      return null;
    },
    routes: [
      GoRoute(path: '/', redirect: (_, __) => '/home'),

      // Auth
      GoRoute(path: '/onboarding', builder: (_, __) => const OnboardingScreen()),
      GoRoute(path: '/login', builder: (_, __) => const LoginScreen()),
      GoRoute(path: '/register', builder: (_, __) => const RegisterScreen()),
      GoRoute(path: '/forgot-password', builder: (_, __) => const ForgotPasswordScreen()),

      // Main shell (bottom nav)
      ShellRoute(
        builder: (context, state, child) => MainShell(child: child),
        routes: [
          GoRoute(path: '/home', builder: (_, __) => const HomeScreen()),
          GoRoute(path: '/bookings', builder: (_, __) => const MyBookingsScreen()),
          GoRoute(path: '/profile', builder: (_, __) => const ProfileScreen()),
          GoRoute(path: '/support', builder: (_, __) => const SupportChatScreen()),
          GoRoute(path: '/info', builder: (_, __) => const InstructorsScreen()),
        ],
      ),

      // Детали
      GoRoute(path: '/booking/new', builder: (_, __) => const BookingWizardScreen()),
      GoRoute(
        path: '/booking/:id',
        builder: (_, state) => BookingDetailScreen(
          bookingId: int.parse(state.pathParameters['id']!),
        ),
      ),
      GoRoute(path: '/profile/edit', builder: (_, __) => const EditProfileScreen()),
      GoRoute(path: '/referral', builder: (_, __) => const ReferralScreen()),
      GoRoute(path: '/instructors', builder: (_, __) => const InstructorsScreen()),
      GoRoute(path: '/faq', builder: (_, __) => const FaqScreen()),
      GoRoute(path: '/notifications', builder: (_, __) => const NotificationSettingsScreen()),
      GoRoute(path: '/about', builder: (_, __) => const AboutScreen()),
    ],
  );
});
