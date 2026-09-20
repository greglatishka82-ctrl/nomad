import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:intl/date_symbol_data_local.dart';

import 'package:nomad_mobile/features/profile/profile_screen.dart';
import 'package:nomad_mobile/shared/models/models.dart';

UserProfile _profile({String? referralCode}) => UserProfile(
      id: 1,
      name: 'Тест Клиент',
      phone: '+7 702 000 00 00',
      referralCode: referralCode,
      createdAt: '2026-01-01T10:00:00',
    );

Future<void> _pump(WidgetTester tester, UserProfile profile, double width) async {
  tester.view.physicalSize = Size(width, 900);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);

  await tester.pumpWidget(ProviderScope(
    overrides: [
      profileProvider.overrideWith((ref) async => profile),
    ],
    child: const MaterialApp(home: ProfileScreen()),
  ));
  await tester.pumpAndSettle();
}

void main() {
  setUpAll(() async {
    await initializeDateFormatting('ru_RU');
  });

  for (final width in <double>[320, 360, 412]) {
    testWidgets(
        'профиль с реферальным кодом не ломает раскладку при ширине ${width.toInt()}',
        (tester) async {
      await _pump(tester, _profile(referralCode: 'NOMAD-1234567890'), width);
      expect(tester.takeException(), isNull);
      expect(find.text('Реферальный код'), findsOneWidget);
      expect(find.text('NOMAD-1234567890'), findsOneWidget);
    });
  }

  testWidgets('профиль без реферального кода рендерится', (tester) async {
    await _pump(tester, _profile(), 360);
    expect(tester.takeException(), isNull);
    expect(find.text('Тест Клиент'), findsWidgets);
  });
}
