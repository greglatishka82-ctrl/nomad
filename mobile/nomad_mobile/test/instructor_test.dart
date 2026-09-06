import 'package:flutter_test/flutter_test.dart';
import 'package:nomad_mobile/shared/models/models.dart';

void main() {
  group('Instructor.normalizeTransmission', () {
    test('accepts transmission values returned by the public API', () {
      expect(Instructor.normalizeTransmission('Механика'), 'manual');
      expect(Instructor.normalizeTransmission('Автомат'), 'automatic');
      expect(Instructor.normalizeTransmission('Механика и автомат'), 'both');
    });

    test('keeps the backend transmission codes', () {
      expect(Instructor.normalizeTransmission('manual'), 'manual');
      expect(Instructor.normalizeTransmission('automatic'), 'automatic');
      expect(Instructor.normalizeTransmission('both'), 'both');
    });
  });
}
