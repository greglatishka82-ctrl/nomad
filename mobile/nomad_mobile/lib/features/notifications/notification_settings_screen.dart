import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../../core/theme/app_theme.dart';

class NotificationSettingsScreen extends StatefulWidget {
  const NotificationSettingsScreen({super.key});

  @override
  State<NotificationSettingsScreen> createState() =>
      _NotificationSettingsScreenState();
}

class _NotificationSettingsScreenState
    extends State<NotificationSettingsScreen> {
  final Map<String, bool> _settings = {
    'booking_confirmed': true,
    'reminder_24h': true,
    'reminder_2h': true,
    'rate_request': true,
    'booking_cancelled': true,
    'package_activated': true,
    'support_reply': true,
  };

  final _labels = {
    'booking_confirmed': 'Подтверждение записи',
    'reminder_24h': 'Напоминание за 24 часа',
    'reminder_2h': 'Напоминание за 2 часа',
    'rate_request': 'Оценить занятие',
    'booking_cancelled': 'Запись отменена',
    'package_activated': 'Пакет активирован',
    'support_reply': 'Ответ поддержки',
  };

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final prefs = await SharedPreferences.getInstance();
    setState(() {
      for (final key in _settings.keys) {
        _settings[key] = prefs.getBool('notif_$key') ?? true;
      }
    });
  }

  Future<void> _toggle(String key, bool val) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool('notif_$key', val);
    setState(() => _settings[key] = val);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Уведомления')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Container(
            decoration: BoxDecoration(
              color: Colors.white,
              borderRadius: BorderRadius.circular(16),
              border: Border.all(color: AppColors.divider),
            ),
            child: Column(
              children: _settings.entries.map((entry) {
                final isLast =
                    entry.key == _settings.keys.last;
                return Column(
                  children: [
                    SwitchListTile(
                      value: entry.value,
                      activeThumbColor: AppColors.primary,
                      title: Text(_labels[entry.key] ?? entry.key,
                          style: const TextStyle(fontSize: 15)),
                      onChanged: (v) => _toggle(entry.key, v),
                    ),
                    if (!isLast)
                      const Divider(
                          height: 1, indent: 16, endIndent: 16),
                  ],
                );
              }).toList(),
            ),
          ),
          const SizedBox(height: 16),
          const Text(
            'Уведомления помогут не пропустить занятие и быть в курсе важных событий.',
            style: TextStyle(
                color: AppColors.textSecondary,
                fontSize: 13,
                height: 1.4),
          ),
        ],
      ),
    );
  }
}
