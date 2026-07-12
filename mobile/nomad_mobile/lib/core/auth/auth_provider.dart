import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:dio/dio.dart';

import '../api/api_client.dart';
import 'auth_storage.dart';

enum AuthStatus { unknown, authenticated, unauthenticated }

class AuthState {
  final AuthStatus status;
  final int? userId;
  final String? userName;
  final String? userEmail;

  const AuthState({
    this.status = AuthStatus.unknown,
    this.userId,
    this.userName,
    this.userEmail,
  });

  AuthState copyWith({
    AuthStatus? status,
    int? userId,
    String? userName,
    String? userEmail,
  }) =>
      AuthState(
        status: status ?? this.status,
        userId: userId ?? this.userId,
        userName: userName ?? this.userName,
        userEmail: userEmail ?? this.userEmail,
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
      final email = await AuthStorage.getUserEmail();
      state = AuthState(
        status: AuthStatus.authenticated,
        userId: id,
        userName: name,
        userEmail: email,
      );
    } else {
      state = const AuthState(status: AuthStatus.unauthenticated);
    }
  }

  Future<void> register({
    required String name,
    required String phone,
    required String email,
    required String password,
    String? referralCode,
  }) async {
    final resp = await _dio.post('/api/mobile/auth/register', data: {
      'name': name,
      'phone': phone,
      'email': email,
      'password': password,
      if (referralCode != null && referralCode.isNotEmpty)
        'referral_code': referralCode,
    });
    await _handleTokenResponse(resp.data);
  }

  Future<void> login({required String email, required String password}) async {
    final resp = await _dio.post('/api/mobile/auth/login', data: {
      'email': email,
      'password': password,
    });
    await _handleTokenResponse(resp.data);
  }

  Future<void> logout() async {
    try {
      await _dio.post('/api/mobile/auth/logout');
    } catch (_) {}
    await AuthStorage.clear();
    state = const AuthState(status: AuthStatus.unauthenticated);
  }

  Future<void> _handleTokenResponse(Map<String, dynamic> data) async {
    await AuthStorage.saveTokens(
      accessToken: data['access_token'] as String,
      refreshToken: data['refresh_token'] as String,
    );
    await AuthStorage.saveUserInfo(
      userId: data['user_id'] as int,
      name: data['name'] as String,
      email: data['email'] as String,
    );
    state = AuthState(
      status: AuthStatus.authenticated,
      userId: data['user_id'] as int,
      userName: data['name'] as String,
      userEmail: data['email'] as String,
    );
  }
}

final authProvider = StateNotifierProvider<AuthNotifier, AuthState>((ref) {
  final dio = ref.watch(dioProvider);
  return AuthNotifier(dio);
});

// Удобный shorthand
final isAuthenticatedProvider = Provider<bool>((ref) {
  return ref.watch(authProvider).status == AuthStatus.authenticated;
});
