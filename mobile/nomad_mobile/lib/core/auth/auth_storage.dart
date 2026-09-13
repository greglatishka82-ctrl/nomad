import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:shared_preferences/shared_preferences.dart';

class AuthStorage {
  static SharedPreferences? _prefs;
  static const FlutterSecureStorage _secure = FlutterSecureStorage();

  static const _keyAccess = 'access_token';
  static const _keyRefresh = 'refresh_token';
  static const _keyUserId = 'user_id';
  static const _keyUserName = 'user_name';

  static Future<SharedPreferences> _getPrefs() async {
    _prefs ??= await SharedPreferences.getInstance();
    return _prefs!;
  }

  static Future<void> saveTokens({
    required String accessToken,
    required String refreshToken,
  }) async {
    await _secure.write(key: _keyAccess, value: accessToken);
    await _secure.write(key: _keyRefresh, value: refreshToken);
    final prefs = await _getPrefs();
    await prefs.remove(_keyAccess);
    await prefs.remove(_keyRefresh);
  }

  static Future<void> saveUserInfo({
    required int userId,
    required String name,
  }) async {
    final prefs = await _getPrefs();
    await prefs.setInt(_keyUserId, userId);
    await prefs.setString(_keyUserName, name);
  }

  static Future<String?> getAccessToken() async {
    return _readAndMigrateToken(_keyAccess);
  }

  static Future<String?> getRefreshToken() async {
    return _readAndMigrateToken(_keyRefresh);
  }

  static Future<String?> getUserName() async {
    final prefs = await _getPrefs();
    return prefs.getString(_keyUserName);
  }

  static Future<int?> getUserId() async {
    final prefs = await _getPrefs();
    return prefs.getInt(_keyUserId);
  }

  static Future<bool> isLoggedIn() async {
    final token = await getAccessToken();
    return token != null && token.isNotEmpty;
  }

  static Future<String?> _readAndMigrateToken(String key) async {
    final protectedValue = await _secure.read(key: key);
    if (protectedValue != null) return protectedValue;

    // One-time migration keeps existing users signed in after this update,
    // while moving the secret out of ordinary application preferences.
    final prefs = await _getPrefs();
    final legacyValue = prefs.getString(key);
    if (legacyValue != null) {
      await _secure.write(key: key, value: legacyValue);
      await prefs.remove(key);
    }
    return legacyValue;
  }

  static Future<void> clear() async {
    final prefs = await _getPrefs();
    await _secure.delete(key: _keyAccess);
    await _secure.delete(key: _keyRefresh);
    await prefs.remove(_keyAccess);
    await prefs.remove(_keyRefresh);
    await prefs.remove(_keyUserId);
    await prefs.remove(_keyUserName);
  }
}
