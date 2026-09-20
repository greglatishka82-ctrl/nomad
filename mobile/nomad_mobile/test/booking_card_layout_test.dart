import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:intl/date_symbol_data_local.dart';

import 'package:nomad_mobile/core/utils/formatters.dart';
import 'package:nomad_mobile/features/my_bookings/my_bookings_screen.dart';
import 'package:nomad_mobile/shared/models/models.dart';

Booking _booking({
  int price = 10000,
  String paymentStatus = 'unpaid',
  String status = 'pending',
  String serviceType = 'training',
}) =>
    Booking(
      id: 1,
      serviceType: serviceType,
      transmission: 'automatic',
      location: 'Циолковского 30',
      bookingDate: '2026-09-19',
      startTime: '01:00',
      endTime: '02:00',
      status: status,
      price: price,
      basePrice: price,
      paymentStatus: paymentStatus,
      createdAt: '2026-09-18T20:00:00',
    );

Future<void> _pumpCard(WidgetTester tester, Booking booking, double width) async {
  tester.view.physicalSize = Size(width, 900);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);

  await tester.pumpWidget(MaterialApp(
    home: Scaffold(
      body: ListView(children: [BookingCard(booking: booking)]),
    ),
  ));
  await tester.pumpAndSettle();
}

void main() {
  setUpAll(() async {
    await initializeDateFormatting('ru_RU');
  });

  testWidgets('контроль: переполнение строки действительно ловится тестом',
      (tester) async {
    tester.view.physicalSize = const Size(320, 400);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: Row(children: [
          Text('Очень длинный текст без переноса ' * 6),
          const Text('Автомат'),
        ]),
      ),
    ));
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNotNull,
        reason: 'если здесь null, тест не умеет ловить переполнение');
  });

  for (final width in <double>[320, 360, 375, 411]) {
    testWidgets('карточка записи не переполняется при ширине ${width.toInt()}',
        (tester) async {
      await _pumpCard(tester, _booking(), width);
      expect(tester.takeException(), isNull);
      expect(find.text('Урок вождения'), findsOneWidget);
      expect(find.text('Оплата наличными или через Kaspi QR'), findsOneWidget);
    });
  }

  testWidgets('оплаченная запись не показывает подсказку об оплате',
      (tester) async {
    await _pumpCard(tester, _booking(paymentStatus: 'paid', status: 'confirmed'), 360);
    expect(tester.takeException(), isNull);
    expect(find.text('Оплата наличными или через Kaspi QR'), findsNothing);
  });

  testWidgets('запись с сертификатом и скидкой не переполняется',
      (tester) async {
    final booking = Booking(
      id: 2,
      serviceType: 'exam',
      transmission: 'manual',
      location: 'Циолковского 30',
      bookingDate: '2026-09-19',
      startTime: '20:00',
      endTime: '20:20',
      status: 'confirmed',
      price: 2500,
      basePrice: 3500,
      certificateAmount: 0,
      referralDiscountAmount: 1000,
      paymentStatus: 'unpaid',
      createdAt: '2026-09-18T20:00:00',
    );
    await _pumpCard(tester, booking, 320);
    expect(tester.takeException(), isNull);
    expect(find.text('Пробный экзамен'), findsOneWidget);
    expect(find.text('Скидка −${formatPrice(1000)}'), findsOneWidget);
  });
}
