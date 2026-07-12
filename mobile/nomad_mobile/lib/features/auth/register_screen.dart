import 'package:flutter/material.dart';
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
  final _emailCtrl = TextEditingController();
  final _passCtrl = TextEditingController();
  final _refCtrl = TextEditingController();
  bool _loading = false;
  bool _obscure = true;
  bool _agreed = false;

  Future<void> _submit() async {
    if (!_formKey.currentState!.validate()) return;
    if (!_agreed) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Необходимо принять условия использования'),
          backgroundColor: AppColors.error,
        ),
      );
      return;
    }
    setState(() => _loading = true);
    try {
      await ref.read(authProvider.notifier).register(
            name: _nameCtrl.text.trim(),
            phone: _phoneCtrl.text.trim(),
            email: _emailCtrl.text.trim(),
            password: _passCtrl.text,
            referralCode: _refCtrl.text.trim().isEmpty
                ? null
                : _refCtrl.text.trim(),
          );
      if (mounted) context.go('/home');
    } on DioException catch (e) {
      final msg = (e.response?.data as Map?)?['detail'] ??
          'Ошибка регистрации';
      _showError(msg.toString());
    } catch (_) {
      _showError('Ошибка соединения с сервером');
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
    _emailCtrl.dispose();
    _passCtrl.dispose();
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
                      Text(
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
                        decoration: InputDecoration(
                          hintText: 'Имя (можно только имя)',
                          prefixIcon: const Icon(Icons.person_outline,
                              size: 20, color: AppColors.outline),
                          border: UnderlineInputBorder(
                            borderSide: BorderSide(color: AppColors.outlineVariant),
                          ),
                          enabledBorder: UnderlineInputBorder(
                            borderSide: BorderSide(color: AppColors.outlineVariant),
                          ),
                          focusedBorder: UnderlineInputBorder(
                            borderSide: BorderSide(color: AppColors.primary, width: 1.5),
                          ),
                          filled: false,
                          contentPadding: const EdgeInsets.symmetric(vertical: 14),
                        ),
                        validator: (v) =>
                            (v == null || v.trim().isEmpty) ? 'Введите имя' : null,
                      ),
                      const SizedBox(height: 16),
                      // ── Phone field ─────────────────────────────
                      TextFormField(
                        controller: _phoneCtrl,
                        keyboardType: TextInputType.phone,
                        textInputAction: TextInputAction.next,
                        decoration: InputDecoration(
                          hintText: '+7 700 000 00 00',
                          prefixIcon: const Icon(Icons.phone_outlined,
                              size: 20, color: AppColors.outline),
                          border: UnderlineInputBorder(
                            borderSide: BorderSide(color: AppColors.outlineVariant),
                          ),
                          enabledBorder: UnderlineInputBorder(
                            borderSide: BorderSide(color: AppColors.outlineVariant),
                          ),
                          focusedBorder: UnderlineInputBorder(
                            borderSide: BorderSide(color: AppColors.primary, width: 1.5),
                          ),
                          filled: false,
                          contentPadding: const EdgeInsets.symmetric(vertical: 14),
                        ),
                        validator: (v) =>
                            (v == null || v.trim().isEmpty) ? 'Введите телефон' : null,
                      ),
                      const SizedBox(height: 16),
                      // ── Email field ─────────────────────────────
                      TextFormField(
                        controller: _emailCtrl,
                        keyboardType: TextInputType.emailAddress,
                        textInputAction: TextInputAction.next,
                        decoration: InputDecoration(
                          hintText: 'Email',
                          prefixIcon: const Icon(Icons.mail_outlined,
                              size: 20, color: AppColors.outline),
                          border: UnderlineInputBorder(
                            borderSide: BorderSide(color: AppColors.outlineVariant),
                          ),
                          enabledBorder: UnderlineInputBorder(
                            borderSide: BorderSide(color: AppColors.outlineVariant),
                          ),
                          focusedBorder: UnderlineInputBorder(
                            borderSide: BorderSide(color: AppColors.primary, width: 1.5),
                          ),
                          filled: false,
                          contentPadding: const EdgeInsets.symmetric(vertical: 14),
                        ),
                        validator: (v) {
                          if (v == null || v.trim().isEmpty) return 'Введите email';
                          if (!v.contains('@')) return 'Неверный формат';
                          return null;
                        },
                      ),
                      const SizedBox(height: 16),
                      // ── Password field ──────────────────────────
                      TextFormField(
                        controller: _passCtrl,
                        obscureText: _obscure,
                        textInputAction: TextInputAction.next,
                        decoration: InputDecoration(
                          hintText: 'Пароль',
                          prefixIcon: const Icon(Icons.lock_outlined,
                              size: 20, color: AppColors.outline),
                          suffixIcon: IconButton(
                            icon: Icon(
                              _obscure ? Icons.visibility_outlined : Icons.visibility_off_outlined,
                              size: 20,
                              color: AppColors.outline,
                            ),
                            onPressed: () =>
                                setState(() => _obscure = !_obscure),
                          ),
                          border: UnderlineInputBorder(
                            borderSide: BorderSide(color: AppColors.outlineVariant),
                          ),
                          enabledBorder: UnderlineInputBorder(
                            borderSide: BorderSide(color: AppColors.outlineVariant),
                          ),
                          focusedBorder: UnderlineInputBorder(
                            borderSide: BorderSide(color: AppColors.primary, width: 1.5),
                          ),
                          filled: false,
                          contentPadding: const EdgeInsets.symmetric(vertical: 14),
                        ),
                        validator: (v) {
                          if (v == null || v.isEmpty) return 'Введите пароль';
                          if (v.length < 6) return 'Минимум 6 символов';
                          return null;
                        },
                      ),
                      const SizedBox(height: 16),
                      // ── Referral field ──────────────────────────
                      TextFormField(
                        controller: _refCtrl,
                        textCapitalization: TextCapitalization.characters,
                        textInputAction: TextInputAction.done,
                        decoration: InputDecoration(
                          hintText: 'Промокод (необязательно)',
                          prefixIcon: const Icon(Icons.card_giftcard_outlined,
                              size: 20, color: AppColors.outline),
                          border: UnderlineInputBorder(
                            borderSide: BorderSide(color: AppColors.outlineVariant),
                          ),
                          enabledBorder: UnderlineInputBorder(
                            borderSide: BorderSide(color: AppColors.outlineVariant),
                          ),
                          focusedBorder: UnderlineInputBorder(
                            borderSide: BorderSide(color: AppColors.primary, width: 1.5),
                          ),
                          filled: false,
                          contentPadding: const EdgeInsets.symmetric(vertical: 14),
                        ),
                      ),
                      const SizedBox(height: 20),
                      // ── Terms checkbox ──────────────────────────
                      Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          SizedBox(
                            width: 24,
                            height: 24,
                            child: Checkbox(
                              value: _agreed,
                              onChanged: (v) =>
                                  setState(() => _agreed = v ?? false),
                              activeColor: AppColors.accent,
                              shape: RoundedRectangleBorder(
                                borderRadius: BorderRadius.circular(4),
                              ),
                              side: const BorderSide(
                                  color: AppColors.outlineVariant),
                            ),
                          ),
                          const SizedBox(width: 12),
                          Expanded(
                            child: RichText(
                              text: const TextSpan(
                                text: 'Я согласен с ',
                                style: TextStyle(
                                  fontSize: 14,
                                  color: AppColors.onSurfaceVariant,
                                ),
                                children: [
                                  TextSpan(
                                    text: 'Условиями использования',
                                    style: TextStyle(
                                      fontWeight: FontWeight.w700,
                                      color: AppColors.accent,
                                    ),
                                  ),
                                  TextSpan(text: ' и '),
                                  TextSpan(
                                    text: 'Политикой конфиденциальности',
                                    style: TextStyle(
                                      fontWeight: FontWeight.w700,
                                      color: AppColors.accent,
                                    ),
                                  ),
                                ],
                              ),
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 24),
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
                      Center(
                        child: RichText(
                          text: const TextSpan(
                            text: 'Уже есть аккаунт? ',
                            style: TextStyle(
                              fontSize: 16,
                              color: AppColors.onSurfaceVariant,
                            ),
                            children: [
                              TextSpan(
                                text: 'Войти',
                                style: TextStyle(
                                  fontWeight: FontWeight.w700,
                                  color: AppColors.primary,
                                ),
                              ),
                            ],
                          ),
                        ),
                      ),
                      const SizedBox(height: 24),
                      // ── Trust badges ────────────────────────────
                      Row(
                        children: [
                          Expanded(
                            child: _TrustBadge(
                              icon: Icons.verified_outlined,
                              title: 'Гос. лицензия',
                              subtitle: 'Официальное обучение с выдачей сертификата.',
                            ),
                          ),
                          const SizedBox(width: 12),
                          Expanded(
                            child: _TrustBadge(
                              icon: Icons.emoji_events_outlined,
                              title: '98% успех',
                              subtitle: 'Наши студенты сдают экзамены с первого раза.',
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
        border: Border.all(color: AppColors.outlineVariant.withValues(alpha: 0.3)),
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
