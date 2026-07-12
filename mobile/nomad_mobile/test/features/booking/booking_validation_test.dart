import 'package:flutter_test/flutter_test.dart';

void main() {
  group('Booking Validation', () {
    test('Нельзя записаться менее чем за 30 минут до слота', () {
      final now = DateTime.now();
      final slot = now.add(const Duration(minutes: 20));
      
      final canBook = slot.difference(now).inMinutes >= 30;
      expect(canBook, false);
    });

    test('Можно записаться за 30 минут и более до слота', () {
      final now = DateTime.now();
      final slot = now.add(const Duration(minutes: 40));
      
      final canBook = slot.difference(now).inMinutes >= 30;
      expect(canBook, true);
    });

    test('Нельзя отменить запись менее чем за 2 часа', () {
      final now = DateTime.now();
      final booking = now.add(const Duration(hours: 1, minutes: 30));
      
      final canCancel = booking.difference(now).inHours >= 2;
      expect(canCancel, false);
    });

    test('Можно отменить запись за 2 часа и более', () {
      final now = DateTime.now();
      final booking = now.add(const Duration(hours: 3));
      
      final canCancel = booking.difference(now).inHours >= 2;
      expect(canCancel, true);
    });

    test('Валидация телефона +7', () {
      final validPhones = [
        '+7 777 123 45 67',
        '+77771234567',
        '87771234567',
      ];

      final phonePattern = RegExp(r'^[+]?[78]\d{10}$');
      
      for (final phone in validPhones) {
        final normalized = phone.replaceAll(RegExp(r'[\s-]'), '');
        expect(phonePattern.hasMatch(normalized), true, reason: 'Phone: $phone');
      }
    });

    test('Невалидные телефоны', () {
      final invalidPhones = [
        '123456',
        '+7 123',
        'abc',
      ];

      final phonePattern = RegExp(r'^[+]?[78]\d{10}$');
      
      for (final phone in invalidPhones) {
        final normalized = phone.replaceAll(RegExp(r'[\s-]'), '');
        expect(phonePattern.hasMatch(normalized), false, reason: 'Phone: $phone');
      }
    });

    test('Валидация email', () {
      final validEmails = [
        'test@example.com',
        'user.name@domain.kz',
        'admin@nomad-driving.com',
      ];

      final emailPattern = RegExp(r'^[^@]+@[^@]+\.[^@]+$');
      
      for (final email in validEmails) {
        expect(emailPattern.hasMatch(email), true, reason: 'Email: $email');
      }
    });

    test('Невалидные email', () {
      final invalidEmails = [
        'test',
        '@example.com',
        'user@',
      ];

      final emailPattern = RegExp(r'^[^@]+@[^@]+\.[^@]+$');
      
      for (final email in invalidEmails) {
        expect(emailPattern.hasMatch(email), false, reason: 'Email: $email');
      }
    });

    test('Пароль минимум 6 символов', () {
      expect('12345'.length >= 6, false);
      expect('123456'.length >= 6, true);
      expect('secure_password'.length >= 6, true);
    });
  });
}
