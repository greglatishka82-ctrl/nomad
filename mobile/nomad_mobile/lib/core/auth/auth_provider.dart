import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:dio/dio.dart';

import '../api/api_client.dart';
import 'auth_storage.dart';
import 'auth_session.dart';

enum AuthStatus { unknown, authenticated, unauthenticated }

class AuthState {
  final AuthStatus status;
  final int? userId;
  final String? userName;

  const AuthState({
    this.status = AuthStatus.unknown,
    this.userId,
    this.userName,
  });

  AuthState copyWith({
    AuthStatus? status,
    int? userId,
    String? userName,
  }) =>
      AuthState(
        status: status ?? this.status,
        userId: userId ?? this.userId,
        userName: userName ?? this.userName,
      );
}

class AuthNotifier extends StateNotifier<AuthState> {
  final Dio _dio;

  AuthNotifier(this._dio) : super(const AuthState()) {
    _checkAuth();
  }

  Future<void> _checkAuth() async {
    final isLoggedIn = await AuthStorage.isLoggedIn();
    if (isLoggedIn) {
      final id = await AuthStorage.getUserId();
      final name = await AuthStorage.getUserName();
      state = AuthState(
        status: AuthStatus.authenticated,
        userId: id,
        userName: name,
      );
    } else {
      state = const AuthState(status: AuthStatus.unauthenticated);
    }
  }

  Future<void> register({
    required String name,
    required String phone,
    required String password,
    required String passwordConfirmation,
    String? referralCode,
  }) async {
    try {
      final data = {
        'name': name,
        'phone': phone,
        'password': password,
        'password_confirmation': passwordConfirmation,
      };

      // Добавляем referral_code ТОЛЬКО если он не пустой
      if (referralCode != null && referralCode.trim().isNotEmpty) {
        data['referral_code'] = referralCode.trim();
      }

      final resp = await _dio.post('/api/mobile/auth/register', data: data);
      await _handleTokenResponse(resp.data);
    } catch (e) {
      rethrow;
    }
  }

  Future<void> login({required String phone, required String password}) async {
    try {
      final resp = await _dio.post('/api/mobile/auth/login', data: {
        'phone': phone,
        'password': password,
      });
      await _handleTokenResponse(resp.data);
    } catch (e) {
      rethrow;
    }
  }

  Future<void> logout() async {
    try {
      await _dio.post('/api/mobile/auth/logout');
    } catch (_) {}
    await AuthStorage.clear();
    state = const AuthState(status: AuthStatus.unauthenticated);
  }

  Future<void> expireSession() async {
    await AuthStorage.clear();
    state = const AuthState(status: AuthStatus.unauthenticated);
  }

  Future<void> _handleTokenResponse(Map<String, dynamic> data) async {
    await AuthStorage.saveTokens(
      accessToken: data['access_token'] as String,
      refreshToken: data['refresh_token'] as String,
    );
    // Получаем информацию о пользователе через /me
    try {
      final meResp = await _dio.get('/api/mobile/auth/me');
      final me = meResp.data as Map<String, dynamic>;
      await AuthStorage.saveUserInfo(
        userId: me['id'] as int,
        name: me['name'] as String,
      );
      state = AuthState(
        status: AuthStatus.authenticated,
        userId: me['id'] as int,
        userName: me['name'] as String,
      );
    } catch (_) {
      // Токен получен, но /me не сработал — сохраняем хотя бы токен
      state = const AuthState(status: AuthStatus.authenticated);
    }
  }
}

final authProvider = StateNotifierProvider<AuthNotifier, AuthState>((ref) {
  final dio = ref.watch(dioProvider);
  final notifier = AuthNotifier(dio);
  ref.listen<int>(authSessionInvalidationProvider, (_, __) {
    notifier.expireSession();
  });
  return notifier;
});

// Удобный shorthand
final isAuthenticatedProvider = Provider<bool>((ref) {
  return ref.watch(authProvider).status == AuthStatus.authenticated;
});
