import 'package:flutter/foundation.dart' show kIsWeb;
import 'package:shared_preferences/shared_preferences.dart';

class AuthStorage {
  static SharedPreferences? _prefs;

  static const _keyAccess = 'access_token';
  static const _keyRefresh = 'refresh_token';
  static const _keyUserId = 'user_id';
  static const _keyUserName = 'user_name';
  static const _keyUserEmail = 'user_email';

  static Future<SharedPreferences> _getPrefs() async {
    _prefs ??= await SharedPreferences.getInstance();
    return _prefs!;
  }

  static Future<void> saveTokens({
    required String accessToken,
    required String refreshToken,
  }) async {
    final prefs = await _getPrefs();
    await prefs.setString(_keyAccess, accessToken);
    await prefs.setString(_keyRefresh, refreshToken);
  }

  static Future<void> saveUserInfo({
    required int userId,
    required String name,
    required String email,
  }) async {
    final prefs = await _getPrefs();
    await prefs.setInt(_keyUserId, userId);
    await prefs.setString(_keyUserName, name);
    await prefs.setString(_keyUserEmail, email);
  }

  static Future<String?> getAccessToken() async {
    final prefs = await _getPrefs();
    return prefs.getString(_keyAccess);
  }

  static Future<String?> getRefreshToken() async {
    final prefs = await _getPrefs();
    return prefs.getString(_keyRefresh);
  }

  static Future<String?> getUserName() async {
    final prefs = await _getPrefs();
    return prefs.getString(_keyUserName);
  }

  static Future<String?> getUserEmail() async {
    final prefs = await _getPrefs();
    return prefs.getString(_keyUserEmail);
  }

  static Future<int?> getUserId() async {
    final prefs = await _getPrefs();
    return prefs.getInt(_keyUserId);
  }

  static Future<bool> isLoggedIn() async {
    final prefs = await _getPrefs();
    final token = prefs.getString(_keyAccess);
    return token != null && token.isNotEmpty;
  }

  static Future<void> clear() async {
    final prefs = await _getPrefs();
    await prefs.remove(_keyAccess);
    await prefs.remove(_keyRefresh);
    await prefs.remove(_keyUserId);
    await prefs.remove(_keyUserName);
    await prefs.remove(_keyUserEmail);
  }
}
