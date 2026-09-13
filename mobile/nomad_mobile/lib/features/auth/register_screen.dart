import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:dio/dio.dart';

import '../../core/auth/auth_provider.dart';
import '../../core/theme/app_theme.dart';

class RegisterScreen extends ConsumerStatefulWidget {
  const RegisterScreen({super.key});

  @override
  ConsumerState<RegisterScreen> createState() => _RegisterScreenState();
}

class _RegisterScreenState extends ConsumerState<RegisterScreen> {
  final _formKey = GlobalKey<FormState>();
  final _nameCtrl = TextEditingController();
  final _phoneCtrl = TextEditingController();
  final _passCtrl = TextEditingController();
  final _passConfirmationCtrl = TextEditingController();
  final _refCtrl = TextEditingController();
  bool _loading = false;
  bool _obscure = true;
  bool _obscureConfirmation = true;

  Future<void> _submit() async {
    if (!_formKey.currentState!.validate()) return;
    setState(() => _loading = true);
    try {
      await ref.read(authProvider.notifier).register(
            name: _nameCtrl.text.trim(),
            phone: _phoneCtrl.text.trim(),
            password: _passCtrl.text,
            passwordConfirmation: _passConfirmationCtrl.text,
            referralCode:
                _refCtrl.text.trim().isEmpty ? null : _refCtrl.text.trim(),
          );
      if (mounted) context.go('/home');
    } on DioException catch (e) {
      String msg = 'Ошибка регистрации';

      // Детальный разбор ошибок
      if (e.response != null) {
        final statusCode = e.response?.statusCode;
        final data = e.response?.data;


        if (data is Map) {
          // Проверяем разные варианты структуры ошибки
          if (data.containsKey('detail')) {
            final detail = data['detail'];
            if (detail is String) {
              msg = detail;
            } else if (detail is List && detail.isNotEmpty) {
              // Pydantic validation errors
              final firstError = detail.first;
              if (firstError is Map) {
                final field = (firstError['loc'] as List?)?.last ?? '';
                final errorMsg = firstError['msg'] ?? '';
                msg = 'Ошибка в поле "$field": $errorMsg';
              } else {
                msg = detail.first.toString();
              }
            }
          } else if (data.containsKey('message')) {
            msg = data['message'].toString();
          } else if (data.containsKey('error')) {
            msg = data['error'].toString();
          }
        } else if (data is String) {
          msg = data;
        }

        // Специфичные коды ошибок
        if (statusCode == 400) {
          if (!msg.contains('уже зарегистрирован') &&
              !msg.contains('не найден') &&
              !msg.contains('Ошибка в поле')) {
            msg = 'Проверьте правильность введённых данных';
          }
        } else if (statusCode == 422) {
          msg = 'Некорректные данные. Проверьте все поля';
        } else if (statusCode == 500) {
          msg = 'Ошибка на сервере. Попробуйте позже';
        }
      } else if (e.type == DioExceptionType.connectionTimeout ||
          e.type == DioExceptionType.receiveTimeout) {
        msg = 'Превышено время ожидания. Проверьте интернет-соединение';
      } else if (e.type == DioExceptionType.connectionError) {
        msg =
            'Не удалось подключиться к серверу. Проверьте интернет-соединение';
      } else {
        msg = 'Ошибка соединения с сервером';
      }

      _showError(msg);
    } catch (_) {
      _showError('Непредвиденная ошибка. Попробуйте ещё раз');
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  void _showError(String msg) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(msg), backgroundColor: AppColors.error),
    );
  }

  @override
  void dispose() {
    _nameCtrl.dispose();
    _phoneCtrl.dispose();
    _passCtrl.dispose();
    _passConfirmationCtrl.dispose();
    _refCtrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.background,
      body: SafeArea(
        child: Column(
          children: [
            // ── Top bar ───────────────────────────────────────────
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
              child: const Row(
                children: [
                  Icon(Icons.school, color: AppColors.primary, size: 28),
                  SizedBox(width: 8),
                  Text(
                    'NOMAD Academy',
                    style: TextStyle(
                      fontSize: 22,
                      fontWeight: FontWeight.w700,
                      color: AppColors.primary,
                      letterSpacing: -0.5,
                    ),
                  ),
                ],
              ),
            ),
            // ── Content ───────────────────────────────────────────
            Expanded(
              child: SingleChildScrollView(
                padding: const EdgeInsets.symmetric(horizontal: 16),
                child: Form(
                  key: _formKey,
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const SizedBox(height: 16),
                      // ── Registration header ─────────────────────
                      const Text(
                        'РЕГИСТРАЦИЯ',
                        style: TextStyle(
                          fontSize: 12,
                          fontWeight: FontWeight.w700,
                          color: AppColors.accent,
                          letterSpacing: 1,
                        ),
                      ),
                      const SizedBox(height: 8),
                      const Text(
                        'Создайте аккаунт, чтобы начать обучение',
                        style: TextStyle(
                          fontSize: 26,
                          fontWeight: FontWeight.w700,
                          color: AppColors.primary,
                          height: 1.2,
                        ),
                      ),
                      const SizedBox(height: 8),
                      const Text(
                        'Присоединяйтесь к сообществу профессиональных водителей NOMAD.',
                        style: TextStyle(
                          fontSize: 16,
                          color: AppColors.onSurfaceVariant,
                        ),
                      ),
                      const SizedBox(height: 32),
                      // ── Name field ──────────────────────────────
                      TextFormField(
                        controller: _nameCtrl,
                        textCapitalization: TextCapitalization.words,
                        textInputAction: TextInputAction.next,
                        decoration: const InputDecoration(
                          hintText: 'Имя',
                          prefixIcon: Icon(Icons.person_outline,
                              size: 20, color: AppColors.outline),
                          border: UnderlineInputBorder(
                            borderSide:
                                BorderSide(color: AppColors.outlineVariant),
                          ),
                          enabledBorder: UnderlineInputBorder(
                            borderSide:
                                BorderSide(color: AppColors.outlineVariant),
                          ),
                          focusedBorder: UnderlineInputBorder(
                            borderSide: BorderSide(
                                color: AppColors.primary, width: 1.5),
                          ),
                          filled: false,
                          contentPadding: EdgeInsets.symmetric(vertical: 14),
                        ),
                        validator: (v) => (v == null || v.trim().isEmpty)
                            ? 'Введите имя'
                            : null,
                      ),
                      const SizedBox(height: 16),
                      // ── Phone field ─────────────────────────────
                      TextFormField(
                        controller: _phoneCtrl,
                        keyboardType: TextInputType.phone,
                        textInputAction: TextInputAction.next,
                        decoration: const InputDecoration(
                          hintText: '+7 700 000 00 00',
                          prefixIcon: Icon(Icons.phone_outlined,
                              size: 20, color: AppColors.outline),
                          border: UnderlineInputBorder(
                            borderSide:
                                BorderSide(color: AppColors.outlineVariant),
                          ),
                          enabledBorder: UnderlineInputBorder(
                            borderSide:
                                BorderSide(color: AppColors.outlineVariant),
                          ),
                          focusedBorder: UnderlineInputBorder(
                            borderSide: BorderSide(
                                color: AppColors.primary, width: 1.5),
                          ),
                          filled: false,
                          contentPadding: EdgeInsets.symmetric(vertical: 14),
                        ),
                        validator: (v) => (v == null || v.trim().isEmpty)
                            ? 'Введите телефон'
                            : null,
                      ),
                      const SizedBox(height: 16),
                      // ── Password field ──────────────────────────
                      TextFormField(
                        controller: _passCtrl,
                        obscureText: _obscure,
                        textInputAction: TextInputAction.next,
                        inputFormatters: [
                          FilteringTextInputFormatter.allow(RegExp(r'[!-~]')),
                        ],
                        decoration: InputDecoration(
                          hintText: 'Пароль (латинские буквы)',
                          prefixIcon: const Icon(Icons.lock_outlined,
                              size: 20, color: AppColors.outline),
                          suffixIcon: IconButton(
                            icon: Icon(
                              _obscure
                                  ? Icons.visibility_outlined
                                  : Icons.visibility_off_outlined,
                              size: 20,
                              color: AppColors.outline,
                            ),
                            onPressed: () =>
                                setState(() => _obscure = !_obscure),
                          ),
                          border: const UnderlineInputBorder(
                            borderSide:
                                BorderSide(color: AppColors.outlineVariant),
                          ),
                          enabledBorder: const UnderlineInputBorder(
                            borderSide:
                                BorderSide(color: AppColors.outlineVariant),
                          ),
                          focusedBorder: const UnderlineInputBorder(
                            borderSide: BorderSide(
                                color: AppColors.primary, width: 1.5),
                          ),
                          filled: false,
                          contentPadding:
                              const EdgeInsets.symmetric(vertical: 14),
                        ),
                        validator: (v) {
                          if (v == null || v.isEmpty) return 'Введите пароль';
                          if (v.length < 6) return 'Минимум 6 символов';
                          if (!RegExp(r'^[!-~]+$').hasMatch(v) ||
                              !RegExp(r'[A-Za-z]').hasMatch(v)) {
                            return 'Используйте латинские буквы, цифры и символы';
                          }
                          return null;
                        },
                      ),
                      const SizedBox(height: 16),
                      TextFormField(
                        controller: _passConfirmationCtrl,
                        obscureText: _obscureConfirmation,
                        textInputAction: TextInputAction.next,
                        inputFormatters: [
                          FilteringTextInputFormatter.allow(RegExp(r'[!-~]')),
                        ],
                        decoration: InputDecoration(
                          hintText: 'Повторите пароль',
                          prefixIcon: const Icon(Icons.lock_outlined,
                              size: 20, color: AppColors.outline),
                          suffixIcon: IconButton(
                            icon: Icon(
                              _obscureConfirmation
                                  ? Icons.visibility_outlined
                                  : Icons.visibility_off_outlined,
                              size: 20,
                              color: AppColors.outline,
                            ),
                            onPressed: () => setState(
                              () =>
                                  _obscureConfirmation = !_obscureConfirmation,
                            ),
                          ),
                          border: const UnderlineInputBorder(
                            borderSide:
                                BorderSide(color: AppColors.outlineVariant),
                          ),
                          enabledBorder: const UnderlineInputBorder(
                            borderSide:
                                BorderSide(color: AppColors.outlineVariant),
                          ),
                          focusedBorder: const UnderlineInputBorder(
                            borderSide: BorderSide(
                                color: AppColors.primary, width: 1.5),
                          ),
                          filled: false,
                          contentPadding:
                              const EdgeInsets.symmetric(vertical: 14),
                        ),
                        validator: (v) {
                          if (v == null || v.isEmpty) return 'Повторите пароль';
                          if (v != _passCtrl.text) return 'Пароли не совпадают';
                          return null;
                        },
                      ),
                      const SizedBox(height: 16),
                      // ── Referral field ──────────────────────────
                      TextFormField(
                        controller: _refCtrl,
                        textCapitalization: TextCapitalization.characters,
                        textInputAction: TextInputAction.done,
                        decoration: const InputDecoration(
                          hintText: 'Реферальный код (необязательно)',
                          prefixIcon: Icon(Icons.card_giftcard_outlined,
                              size: 20, color: AppColors.outline),
                          border: UnderlineInputBorder(
                            borderSide:
                                BorderSide(color: AppColors.outlineVariant),
                          ),
                          enabledBorder: UnderlineInputBorder(
                            borderSide:
                                BorderSide(color: AppColors.outlineVariant),
                          ),
                          focusedBorder: UnderlineInputBorder(
                            borderSide: BorderSide(
                                color: AppColors.primary, width: 1.5),
                          ),
                          filled: false,
                          contentPadding: EdgeInsets.symmetric(vertical: 14),
                        ),
                      ),
                      const SizedBox(height: 20),
                      // ── Submit button ───────────────────────────
                      SizedBox(
                        width: double.infinity,
                        height: 52,
                        child: ElevatedButton(
                          onPressed: _loading ? null : _submit,
                          style: ElevatedButton.styleFrom(
                            backgroundColor: AppColors.accent,
                            foregroundColor: Colors.white,
                            elevation: 0,
                            shape: RoundedRectangleBorder(
                              borderRadius: BorderRadius.circular(12),
                            ),
                          ),
                          child: _loading
                              ? const SizedBox(
                                  width: 24,
                                  height: 24,
                                  child: CircularProgressIndicator(
                                    color: Colors.white,
                                    strokeWidth: 2,
                                  ),
                                )
                              : const Row(
                                  mainAxisAlignment: MainAxisAlignment.center,
                                  children: [
                                    Text('Зарегистрироваться'),
                                    SizedBox(width: 8),
                                    Icon(Icons.chevron_right, size: 22),
                                  ],
                                ),
                        ),
                      ),
                      const SizedBox(height: 20),
                      // ── Login link ──────────────────────────────
                      Row(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: [
                          const Text(
                            'Уже есть аккаунт?',
                            style: TextStyle(
                              fontSize: 16,
                              color: AppColors.onSurfaceVariant,
                            ),
                          ),
                          TextButton(
                            onPressed: () => context.go('/login'),
                            child: const Text(
                              'Войти',
                              style: TextStyle(
                                fontSize: 16,
                                fontWeight: FontWeight.w700,
                                color: AppColors.primary,
                              ),
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 24),
                      // ── Trust badges ────────────────────────────
                      const Row(
                        children: [
                          Expanded(
                            child: _TrustBadge(
                              icon: Icons.verified_outlined,
                              title: 'Гос. лицензия',
                              subtitle:
                                  'Официальное обучение с выдачей сертификата.',
                            ),
                          ),
                          SizedBox(width: 12),
                          Expanded(
                            child: _TrustBadge(
                              icon: Icons.emoji_events_outlined,
                              title: '98% успех',
                              subtitle:
                                  'Наши студенты сдают экзамены с первого раза.',
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 24),
                      // ── Footer ──────────────────────────────────
                      const Center(
                        child: Text(
                          '© 2026 NOMAD Driving Academy',
                          style: TextStyle(
                            fontSize: 12,
                            color: AppColors.onSurfaceVariant,
                          ),
                        ),
                      ),
                      const SizedBox(height: 24),
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

// ── Trust badge ───────────────────────────────────────────────────────────────

class _TrustBadge extends StatelessWidget {
  final IconData icon;
  final String title;
  final String subtitle;

  const _TrustBadge({
    required this.icon,
    required this.title,
    required this.subtitle,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: AppColors.surfaceContainerLow,
        borderRadius: BorderRadius.circular(12),
        border:
            Border.all(color: AppColors.outlineVariant.withValues(alpha: 0.3)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(icon, color: AppColors.primary, size: 28),
          const SizedBox(height: 12),
          Text(
            title,
            style: const TextStyle(
              fontSize: 16,
              fontWeight: FontWeight.w700,
              color: AppColors.primary,
            ),
          ),
          const SizedBox(height: 4),
          Text(
            subtitle,
            style: const TextStyle(
              fontSize: 12,
              color: AppColors.onSurfaceVariant,
              height: 1.3,
            ),
          ),
        ],
      ),
    );
  }
}
