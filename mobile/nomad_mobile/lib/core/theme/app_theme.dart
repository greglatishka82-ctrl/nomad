import 'package:flutter/material.dart';

class AppColors {
  // ── Brand (from Stitch DESIGN.md) ──────────────────────────────────────────
  static const primary = Color(0xFF031634);
  static const primaryContainer = Color(0xFF1A2B4A);
  static const accent = Color(0xFFFE6A34);
  static const secondaryContainer = Color(0xFFFE6A34);

  // ── Surfaces (from Stitch DESIGN.md) ───────────────────────────────────────
  static const background = Color(0xFFF8F9FF);
  static const surface = Color(0xFFF8F9FF);
  static const surfaceContainerLowest = Color(0xFFFFFFFF);
  static const surfaceContainerLow = Color(0xFFEFF4FF);
  static const surfaceContainer = Color(0xFFE5EEFF);
  static const surfaceContainerHigh = Color(0xFFDCE9FF);
  static const surfaceContainerHighest = Color(0xFFD3E4FE);
  static const surfaceVariant = Color(0xFFD3E4FE);

  // ── Text (from Stitch DESIGN.md) ───────────────────────────────────────────
  static const onSurface = Color(0xFF0B1C30);
  static const onSurfaceVariant = Color(0xFF44474E);
  static const onPrimary = Color(0xFFFFFFFF);
  static const onPrimaryContainer = Color(0xFF8293B7);
  static const onSecondaryContainer = Color(0xFF5D1900);
  static const textPrimary = Color(0xFF0B1C30);
  static const textSecondary = Color(0xFF44474E);
  static const textHint = Color(0xFF9CA3AF);

  // ── Borders & Outline (from Stitch DESIGN.md) ──────────────────────────────
  static const outline = Color(0xFF75777E);
  static const outlineVariant = Color(0xFFC5C6CF);
  static const divider = Color(0xFFE2E8F0);

  // ── Semantic (from Stitch) ─────────────────────────────────────────────────
  static const error = Color(0xFFBA1A1A);
  static const errorContainer = Color(0xFFFFDAD6);
  static const success = Color(0xFF22C55E);
  static const warning = Color(0xFFF59E0B);

  // ── Status ─────────────────────────────────────────────────────────────────
  static const statusPlanned = Color(0xFF3B82F6);
  static const statusConfirmed = Color(0xFF22C55E);
  static const statusCompleted = Color(0xFF6B7280);
  static const statusCancelled = Color(0xFFEF4444);
  static const statusNoShow = Color(0xFFF59E0B);

  // ── Shadows (from Stitch DESIGN.md) ────────────────────────────────────────
  static final cardShadow = [
    BoxShadow(
      color: const Color(0xFF031634).withValues(alpha: 0.1),
      blurRadius: 8,
      offset: const Offset(0, 2),
    ),
  ];
  static final cardShadowLg = [
    BoxShadow(
      color: const Color(0xFF031634).withValues(alpha: 0.15),
      blurRadius: 16,
      offset: const Offset(0, 4),
    ),
  ];
}

class AppTheme {
  static ThemeData light() {
    return ThemeData(
      useMaterial3: true,
      colorScheme: ColorScheme.fromSeed(
        seedColor: AppColors.primaryContainer,
        primary: AppColors.primaryContainer,
        secondary: AppColors.accent,
        surface: AppColors.surface,
        error: AppColors.error,
        brightness: Brightness.light,
      ),
      scaffoldBackgroundColor: AppColors.background,
      fontFamily: 'Roboto',
      appBarTheme: AppBarTheme(
        backgroundColor: AppColors.background,
        foregroundColor: AppColors.primary,
        elevation: 0,
        scrolledUnderElevation: 0,
        centerTitle: false,
        titleTextStyle: const TextStyle(
          fontFamily: 'Roboto',
          fontSize: 20,
          fontWeight: FontWeight.w700,
          color: AppColors.primary,
        ),
      ),
      elevatedButtonTheme: ElevatedButtonThemeData(
        style: ElevatedButton.styleFrom(
          backgroundColor: AppColors.accent,
          foregroundColor: Colors.white,
          elevation: 0,
          minimumSize: const Size(double.infinity, 52),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(12),
          ),
          textStyle: const TextStyle(
            fontFamily: 'Roboto',
            fontSize: 16,
            fontWeight: FontWeight.w600,
          ),
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          foregroundColor: AppColors.primaryContainer,
          minimumSize: const Size(double.infinity, 52),
          side: const BorderSide(color: AppColors.outlineVariant),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(12),
          ),
          textStyle: const TextStyle(
            fontFamily: 'Roboto',
            fontSize: 16,
            fontWeight: FontWeight.w500,
          ),
        ),
      ),
      textButtonTheme: TextButtonThemeData(
        style: TextButton.styleFrom(
          foregroundColor: AppColors.accent,
          textStyle: const TextStyle(
            fontFamily: 'Roboto',
            fontSize: 14,
            fontWeight: FontWeight.w500,
          ),
        ),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: AppColors.surfaceContainerLowest,
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: AppColors.outlineVariant),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: AppColors.outlineVariant),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: AppColors.primary, width: 1.5),
        ),
        contentPadding:
            const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
        hintStyle: const TextStyle(color: AppColors.outline),
        labelStyle: const TextStyle(color: AppColors.onSurfaceVariant),
      ),
      cardTheme: CardThemeData(
        color: AppColors.surfaceContainerLowest,
        elevation: 0,
        shadowColor: Colors.transparent,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(12),
          side: const BorderSide(color: AppColors.outlineVariant),
        ),
        margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
      ),
      bottomNavigationBarTheme: const BottomNavigationBarThemeData(
        backgroundColor: AppColors.surfaceContainerLowest,
        selectedItemColor: AppColors.accent,
        unselectedItemColor: AppColors.onSurfaceVariant,
        selectedLabelStyle: TextStyle(
          fontSize: 12,
          fontWeight: FontWeight.w500,
        ),
        unselectedLabelStyle: TextStyle(fontSize: 12),
        type: BottomNavigationBarType.fixed,
        elevation: 0,
      ),
      chipTheme: ChipThemeData(
        backgroundColor: AppColors.surfaceVariant,
        selectedColor: AppColors.primaryContainer,
        labelStyle: const TextStyle(
          fontSize: 13,
          color: AppColors.onSurface,
        ),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(20),
        ),
        side: BorderSide.none,
      ),
      dividerTheme: const DividerThemeData(
        color: AppColors.outlineVariant,
        thickness: 0.5,
        space: 1,
      ),
      snackBarTheme: SnackBarThemeData(
        behavior: SnackBarBehavior.floating,
        backgroundColor: AppColors.primaryContainer,
        contentTextStyle: const TextStyle(
          fontFamily: 'Roboto',
          fontSize: 14,
          color: Colors.white,
        ),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(12),
        ),
      ),
    );
  }
}

// ── Extensions ────────────────────────────────────────────────────────────────

extension BookingStatusColor on String {
  Color get statusColor {
    switch (this) {
      case 'planned':
        return AppColors.statusPlanned;
      case 'confirmed':
        return AppColors.statusConfirmed;
      case 'completed':
        return AppColors.statusCompleted;
      case 'cancelled':
        return AppColors.statusCancelled;
      case 'no_show':
        return AppColors.statusNoShow;
      default:
        return AppColors.onSurfaceVariant;
    }
  }

  String get statusLabel {
    switch (this) {
      case 'planned':
        return 'Запланировано';
      case 'confirmed':
        return 'Подтверждено';
      case 'in_progress':
        return 'Идёт занятие';
      case 'completed':
        return 'Завершено';
      case 'cancelled':
        return 'Отменено';
      case 'no_show':
        return 'Не явился';
      default:
        return this;
    }
  }
}
