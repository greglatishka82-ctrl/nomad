import 'package:flutter_test/flutter_test.dart';
import 'package:nomad_mobile/core/utils/formatters.dart';

void main() {
  group('Formatters', () {
    test('formatPrice форматирует цену корректно', () {
      // NumberFormat использует неразрывный пробел \u00A0
      expect(formatPrice(6000), '6\u00A0000 ₸');
      expect(formatPrice(54000), '54\u00A0000 ₸');
      expect(formatPrice(100), '100 ₸');
    });

    test('formatTime обрезает секунды', () {
      expect(formatTime('09:00:00'), '09:00');
      expect(formatTime('14:30:00'), '14:30');
      expect(formatTime('09:00'), '09:00');
    });

    test('serviceTypeLabel возвращает правильные метки', () {
      expect(serviceTypeLabel('training'), 'Урок вождения');
      expect(serviceTypeLabel('exam'), 'Пробный экзамен');
    });

    test('transmissionLabel возвращает правильные метки', () {
      expect(transmissionLabel('manual'), 'Механика');
      expect(transmissionLabel('automatic'), 'Автомат');
      expect(transmissionLabel('both'), 'Механика и автомат');
    });
  });

  group('StatusExtension', () {
    test('statusLabel возвращает правильные названия', () {
      expect('planned'.statusLabel, 'Запланирована');
      expect('confirmed'.statusLabel, 'Подтверждена');
      expect('completed'.statusLabel, 'Завершена');
      expect('cancelled'.statusLabel, 'Отменена');
      expect('no_show'.statusLabel, 'Не явился');
    });

    test('statusColor возвращает правильные цвета', () {
      expect('planned'.statusColor.value, 0xFF2563EB);
      expect('confirmed'.statusColor.value, 0xFF16A34A);
      expect('completed'.statusColor.value, 0xFF64748B);
      expect('cancelled'.statusColor.value, 0xFFDC2626);
      expect('no_show'.statusColor.value, 0xFFF59E0B);
    });
  });
}
