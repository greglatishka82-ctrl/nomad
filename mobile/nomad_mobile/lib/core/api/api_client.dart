import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:pretty_dio_logger/pretty_dio_logger.dart';

import '../auth/auth_storage.dart';
import '../../shared/models/models.dart';

// Для локального тестирования используй http://192.168.1.254:8000
// Для продакшна: https://nomad-o7ct.onrender.com
const String kBaseUrl = 'https://corps-happiness-somerset-ips.trycloudflare.com';

final dioProvider = Provider<Dio>((ref) {
  final dio = Dio(
    BaseOptions(
      baseUrl: kBaseUrl,
      connectTimeout: const Duration(seconds: 15),
      receiveTimeout: const Duration(seconds: 30),
      headers: {'Content-Type': 'application/json'},
    ),
  );

  // Логирование запросов (только в debug)
  assert(() {
    dio.interceptors.add(PrettyDioLogger(
      requestHeader: false,
      requestBody: true,
      responseBody: true,
      error: true,
      compact: true,
    ));
    return true;
  }());

  // JWT interceptor — добавляет токен и обновляет при 401
  dio.interceptors.add(
    InterceptorsWrapper(
      onRequest: (options, handler) async {
        final token = await AuthStorage.getAccessToken();
        if (token != null) {
          options.headers['Authorization'] = 'Bearer $token';
        }
        handler.next(options);
      },
      onError: (error, handler) async {
        if (error.response?.statusCode == 401) {
          // Пробуем обновить токен
          final refreshed = await _tryRefreshToken(dio);
          if (refreshed) {
            // Повторяем оригинальный запрос с новым токеном
            final token = await AuthStorage.getAccessToken();
            final opts = error.requestOptions;
            opts.headers['Authorization'] = 'Bearer $token';
            try {
              final resp = await dio.fetch(opts);
              return handler.resolve(resp);
            } catch (e) {
              return handler.next(error);
            }
          }
          // Токен не обновился — разлогиниваем
          await AuthStorage.clear();
        }
        handler.next(error);
      },
    ),
  );

  return dio;
});

/// Публичная конфигурация приложения (цены, адреса, контакты).
/// Загружается один раз при старте и кешируется.
final appConfigProvider = FutureProvider<AppConfig>((ref) async {
  final dio = ref.watch(dioProvider);
  final resp = await dio.get('/api/mobile/config');
  return AppConfig.fromJson(resp.data as Map<String, dynamic>);
});

Future<bool> _tryRefreshToken(Dio dio) async {
  final refreshToken = await AuthStorage.getRefreshToken();
  if (refreshToken == null) return false;

  try {
    final resp = await dio.post(
      '/api/mobile/auth/refresh',
      data: {'refresh_token': refreshToken},
      options: Options(headers: {}), // без токена чтобы не зациклиться
    );
    final data = resp.data as Map<String, dynamic>;
    await AuthStorage.saveTokens(
      accessToken: data['access_token'] as String,
      refreshToken: data['refresh_token'] as String,
    );
    return true;
  } catch (_) {
    return false;
  }
}
