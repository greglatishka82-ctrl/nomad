import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:dio/dio.dart';
import 'package:table_calendar/table_calendar.dart';

import '../../core/api/api_client.dart';
import '../../core/theme/app_theme.dart';
import '../../core/utils/booking_date_window.dart';
import '../../core/utils/formatters.dart';
import '../my_bookings/bookings_provider.dart';

// ── State ────────────────────────────────────────────────────────────────────

class _WizardState {
  final int step;
  final String? transmission; // manual | automatic (ШАГ 0)
  final String? instructorGender; // male | female | any (ШАГ 1)
  final String? serviceType; // training | exam (ШАГ 2)
  final String? location; // всегда Циолковского 30
  final DateTime? date; // ШАГ 3
  final String? slot; // "10:00" (ШАГ 4)
  final String? certificateCode;
  final List<String> slots;
  final bool slotsLoading;

  const _WizardState({
    this.step = 0,
    this.transmission,
    this.instructorGender,
    this.serviceType,
    this.location,
    this.date,
    this.slot,
    this.certificateCode,
    this.slots = const [],
    this.slotsLoading = false,
  });

  _WizardState copyWith({
    int? step,
    String? transmission,
    String? instructorGender,
    String? serviceType,
    String? location,
    DateTime? date,
    String? slot,
    String? certificateCode,
    List<String>? slots,
    bool? slotsLoading,
    bool clearDate = false,
    bool clearSlot = false,
    bool clearCertificateCode = false,
  }) =>
      _WizardState(
        step: step ?? this.step,
        transmission: transmission ?? this.transmission,
        instructorGender: instructorGender ?? this.instructorGender,
        serviceType: serviceType ?? this.serviceType,
        location: location ?? this.location,
        date: clearDate ? null : (date ?? this.date),
        slot: clearSlot ? null : (slot ?? this.slot),
        certificateCode: clearCertificateCode
            ? null
            : (certificateCode ?? this.certificateCode),
        slots: slots ?? this.slots,
        slotsLoading: slotsLoading ?? this.slotsLoading,
      );
}

// ── Screen ───────────────────────────────────────────────────────────────────

class BookingWizardScreen extends ConsumerStatefulWidget {
  const BookingWizardScreen({super.key});

  @override
  ConsumerState<BookingWizardScreen> createState() =>
      _BookingWizardScreenState();
}

class _BookingWizardScreenState extends ConsumerState<BookingWizardScreen> {
  _WizardState _state = const _WizardState();
  bool _submitting = false;

  void _update(_WizardState s) => setState(() => _state = s);

  Future<void> _loadSlots() async {
    if (_state.date == null ||
        _state.serviceType == null ||
        _state.transmission == null) {
      return;
    }
    _update(_state.copyWith(slotsLoading: true, slots: []));
    try {
      final dio = ref.read(dioProvider);
      final params = {
        'booking_date': _state.date!.toIso8601String().substring(0, 10),
        'service_type': _state.serviceType,
        'transmission': _state.transmission,
        'instructor_gender': _state.instructorGender ?? 'any',
      };
      params['location_preference'] = kFixedBookingLocation;
      final resp = await dio.get('/api/mobile/slots', queryParameters: params);
      final data = resp.data as Map<String, dynamic>;
      final slots = (data['slots'] as List).map((e) => e.toString()).toList();
      _update(_state.copyWith(slots: slots, slotsLoading: false));
    } catch (_) {
      _update(_state.copyWith(slotsLoading: false));
    }
  }

  Future<void> _submit() async {
    setState(() => _submitting = true);
    try {
      final dio = ref.read(dioProvider);

      final body = {
        'service_type': _state.serviceType,
        'transmission': _state.transmission,
        'instructor_gender': _state.instructorGender ?? 'any',
        'booking_date': _state.date!.toIso8601String().substring(0, 10),
        'start_time': _state.slot,
        'location': kFixedBookingLocation,
      };
      if (_state.certificateCode != null &&
          _state.certificateCode!.isNotEmpty) {
        body['certificate_code'] = _state.certificateCode!;
      }

      await dio.post('/api/mobile/bookings', data: body);
      ref.invalidate(upcomingBookingsProvider);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text(
                'Ваша заявка находится в обработке. Ожидайте подтверждения.'),
            backgroundColor: AppColors.primary,
          ),
        );
        context.go('/bookings');
      }
    } on DioException catch (e) {
      final msg = (e.response?.data as Map?)?['detail'] ?? 'Ошибка записи';
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
              content: Text(msg.toString()), backgroundColor: AppColors.error),
        );
      }
    } finally {
      if (mounted) setState(() => _submitting = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    const stepsCount = 6;
    const stepLabels = ['КПП', 'Пол', 'Тип', 'Дата', 'Время', 'Итог'];

    return Scaffold(
      appBar: AppBar(
        title: const Text('Запись на занятие'),
        leading: IconButton(
          icon: const Icon(Icons.close),
          onPressed: () => context.pop(),
        ),
      ),
      body: Column(
        children: [
          _StepIndicator(currentStep: _state.step, steps: stepLabels),
          Expanded(
            child: SingleChildScrollView(
              padding: const EdgeInsets.all(20),
              child: _buildStep(),
            ),
          ),
          _BottomBar(
            step: _state.step,
            canNext: _canNext(),
            submitting: _submitting,
            onBack: _state.step > 0
                ? () => _update(_state.copyWith(step: _state.step - 1))
                : null,
            onNext: _canNext()
                ? () {
                    const maxStep = stepsCount - 1;
                    if (_state.step < maxStep) {
                      final next = _state.step + 1;
                      _update(_state.copyWith(step: next));
                      if (next == 3) {
                        WidgetsBinding.instance.addPostFrameCallback((_) {
                          _loadSlots();
                        });
                      }
                    } else {
                      _submit();
                    }
                  }
                : null,
          ),
        ],
      ),
    );
  }

  bool _canNext() {
    switch (_state.step) {
      case 0: // КПП
        return _state.transmission != null;
      case 1: // Пол инструктора
        return _state.instructorGender != null;
      case 2: // Тип
        return _state.serviceType != null;
      case 3: // Дата
        return _state.date != null;
      case 4: // Время
        return _state.slot != null;
      case 5: // Итог
        return true;
      default:
        return false;
    }
  }

  Widget _buildStep() {
    if (_state.step == 0) {
      // ШАГ 1: КПП
      return _Step1Transmission(
        selected: _state.transmission,
        onSelect: (v) =>
            _update(_state.copyWith(transmission: v, clearSlot: true)),
      );
    }

    if (_state.step == 1) {
      // ШАГ 2: Пол инструктора
      return _Step1Gender(
        selected: _state.instructorGender,
        onSelect: (v) =>
            _update(_state.copyWith(instructorGender: v, clearSlot: true)),
      );
    }

    if (_state.step == 2) {
      // ШАГ 3: Тип
      return _Step2ServiceType(
        selected: _state.serviceType,
        onSelect: (v) {
          _update(_state.copyWith(
            serviceType: v,
            location: kFixedBookingLocation,
            clearDate: true,
            clearSlot: true,
            clearCertificateCode: true,
          ));
        },
      );
    }

    if (_state.step == 3) {
      return _Step4Date(
        selected: _state.date,
        onSelect: (d) {
          _update(_state.copyWith(date: d, clearSlot: true));
          WidgetsBinding.instance.addPostFrameCallback((_) {
            _loadSlots();
          });
        },
      );
    }

    if (_state.step == 4) {
      return _Step5Time(
        slots: _state.slots,
        selected: _state.slot,
        loading: _state.slotsLoading,
        onSelect: (s) => _update(_state.copyWith(slot: s)),
      );
    }

    if (_state.step == 5) {
      return _Step6Confirm(
        state: _state,
        onCertChange: (v) => _update(_state.copyWith(certificateCode: v)),
      );
    }

    return const SizedBox();
  }
}

// ── Шаг 1: КПП ───────────────────────────────────────────────────────────────

class _Step1Transmission extends ConsumerWidget {
  final String? selected;
  final void Function(String) onSelect;
  const _Step1Transmission({required this.selected, required this.onSelect});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final configAsync = ref.watch(appConfigProvider);
    return configAsync.when(
      loading: () => const Center(child: CircularProgressIndicator()),
      error: (_, __) =>
          const Center(child: Text('Ошибка загрузки конфигурации')),
      data: (config) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const _StepTitle('Коробка передач'),
          const SizedBox(height: 24),
          _ChoiceCard(
            selected: selected == 'manual',
            icon: Icons.settings,
            title: 'Механика (МКПП)',
            subtitle: config.carModelManual,
            price: '',
            color: AppColors.primary,
            onTap: () => onSelect('manual'),
          ),
          const SizedBox(height: 16),
          _ChoiceCard(
            selected: selected == 'automatic',
            icon: Icons.auto_mode,
            title: 'Автомат (АКПП)',
            subtitle: config.carModelAutomatic,
            price: '',
            color: AppColors.accent,
            onTap: () => onSelect('automatic'),
          ),
        ],
      ),
    );
  }
}

// ── Шаг 2: Пол инструктора ──────────────────────────────────────────────────

class _Step1Gender extends ConsumerWidget {
  final String? selected;
  final void Function(String) onSelect;
  const _Step1Gender({required this.selected, required this.onSelect});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const _StepTitle('Пол инструктора'),
        const SizedBox(height: 24),
        _ChoiceCard(
          selected: selected == 'male',
          icon: Icons.person,
          title: 'Мужчина',
          subtitle: 'Предпочтительный пол инструктора',
          price: '',
          color: AppColors.primary,
          onTap: () => onSelect('male'),
        ),
        const SizedBox(height: 16),
        _ChoiceCard(
          selected: selected == 'female',
          icon: Icons.person_outline,
          title: 'Женщина',
          subtitle: 'Предпочтительный пол инструктора',
          price: '',
          color: AppColors.accent,
          onTap: () => onSelect('female'),
        ),
        const SizedBox(height: 16),
        _ChoiceCard(
          selected: selected == 'any',
          icon: Icons.people_outline,
          title: 'Не важно',
          subtitle: 'Любой доступный инструктор',
          price: '',
          color: AppColors.success,
          onTap: () => onSelect('any'),
        ),
      ],
    );
  }
}

// ── Шаг 3: Тип занятия ───────────────────────────────────────────────────────

class _Step2ServiceType extends ConsumerWidget {
  final String? selected;
  final void Function(String) onSelect;
  const _Step2ServiceType({required this.selected, required this.onSelect});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final configAsync = ref.watch(appConfigProvider);
    return configAsync.when(
      loading: () => const Center(child: CircularProgressIndicator()),
      error: (_, __) =>
          const Center(child: Text('Ошибка загрузки конфигурации')),
      data: (config) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const _StepTitle('Выберите тип занятия'),
          const SizedBox(height: 24),
          _ChoiceCard(
            selected: selected == 'training',
            icon: Icons.directions_car,
            title: 'Урок вождения',
            subtitle: 'Площадка: $kFixedBookingLocation',
            price: '${formatPrice(config.priceTraining)}/час',
            color: AppColors.primary,
            onTap: () => onSelect('training'),
          ),
          const SizedBox(height: 16),
          _ChoiceCard(
            selected: selected == 'exam',
            icon: Icons.assignment_turned_in_outlined,
            title: 'Пробный экзамен',
            subtitle: 'Площадка: $kFixedBookingLocation',
            price:
                '${formatPrice(config.priceExam)} • ${config.examDurationMinutes} минут',
            color: AppColors.accent,
            onTap: () => onSelect('exam'),
          ),
        ],
      ),
    );
  }
}

// ── Шаг 4: Дата ──────────────────────────────────────────────────────────────

class _Step4Date extends ConsumerWidget {
  final DateTime? selected;
  final void Function(DateTime) onSelect;
  const _Step4Date({required this.selected, required this.onSelect});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final configAsync = ref.watch(appConfigProvider);
    return configAsync.when(
      loading: () => const Center(child: CircularProgressIndicator()),
      error: (_, __) =>
          const Center(child: Text('Ошибка загрузки конфигурации')),
      data: (config) {
        final firstDay =
            bookingWindowStart(workingHoursEnd: config.workingHoursEnd);
        final lastDay =
            bookingWindowEnd(workingHoursEnd: config.workingHoursEnd);
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const _StepTitle('Выберите дату'),
            const SizedBox(height: 8),
            const Text(
              'Доступна запись только на ближайшие 7 дней',
              style: TextStyle(color: AppColors.textSecondary, fontSize: 13),
            ),
            const SizedBox(height: 16),
            Container(
              decoration: BoxDecoration(
                color: Colors.white,
                borderRadius: BorderRadius.circular(16),
                border: Border.all(color: AppColors.divider),
              ),
              child: TableCalendar(
                locale: 'ru_RU',
                firstDay: firstDay,
                lastDay: lastDay,
                focusedDay: selected ?? firstDay,
                selectedDayPredicate: (d) =>
                    selected != null && isSameDay(d, selected!),
                onDaySelected: (selected, _) => onSelect(selected),
                calendarStyle: CalendarStyle(
                  selectedDecoration: const BoxDecoration(
                    color: AppColors.primary,
                    shape: BoxShape.circle,
                  ),
                  todayDecoration: BoxDecoration(
                    color: AppColors.accent.withValues(alpha: 0.3),
                    shape: BoxShape.circle,
                  ),
                  weekendTextStyle: const TextStyle(color: AppColors.error),
                ),
                headerStyle: const HeaderStyle(
                  formatButtonVisible: false,
                  titleCentered: true,
                  titleTextStyle:
                      TextStyle(fontWeight: FontWeight.w700, fontSize: 16),
                ),
              ),
            ),
          ],
        );
      },
    );
  }
}

// ── Шаг 5: Время ─────────────────────────────────────────────────────────────

class _Step5Time extends StatelessWidget {
  final List<String> slots;
  final String? selected;
  final bool loading;
  final void Function(String) onSelect;

  const _Step5Time({
    required this.slots,
    required this.selected,
    required this.loading,
    required this.onSelect,
  });

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const _StepTitle('Выберите время'),
        const SizedBox(height: 24),
        if (loading)
          const Center(child: CircularProgressIndicator())
        else if (slots.isEmpty)
          Container(
            padding: const EdgeInsets.all(24),
            decoration: BoxDecoration(
              color: Colors.white,
              borderRadius: BorderRadius.circular(16),
              border: Border.all(color: AppColors.divider),
            ),
            child: const Column(
              children: [
                Icon(Icons.event_busy, size: 48, color: AppColors.textHint),
                SizedBox(height: 12),
                Text('На эту дату нет свободных слотов',
                    textAlign: TextAlign.center,
                    style: TextStyle(color: AppColors.textSecondary)),
              ],
            ),
          )
        else
          Wrap(
            spacing: 12,
            runSpacing: 12,
            children: slots
                .map((s) => _TimeChip(
                      time: s,
                      selected: s == selected,
                      onTap: () => onSelect(s),
                    ))
                .toList(),
          ),
      ],
    );
  }
}

class _TimeChip extends StatelessWidget {
  final String time;
  final bool selected;
  final VoidCallback onTap;
  const _TimeChip(
      {required this.time, required this.selected, required this.onTap});

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 150),
        width: 90,
        padding: const EdgeInsets.symmetric(vertical: 14),
        decoration: BoxDecoration(
          color: selected ? AppColors.primary : Colors.white,
          borderRadius: BorderRadius.circular(12),
          border: Border.all(
            color: selected ? AppColors.primary : AppColors.divider,
            width: selected ? 2 : 1,
          ),
        ),
        child: Center(
          child: Text(
            time,
            style: TextStyle(
              fontSize: 16,
              fontWeight: FontWeight.w600,
              color: selected ? Colors.white : AppColors.textPrimary,
            ),
          ),
        ),
      ),
    );
  }
}

// ── Шаг 6: Подтверждение ─────────────────────────────────────────────────────

class _Step6Confirm extends ConsumerWidget {
  final _WizardState state;
  final void Function(String) onCertChange;
  const _Step6Confirm({required this.state, required this.onCertChange});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final s = state;
    final configAsync = ref.watch(appConfigProvider);
    return configAsync.when(
      loading: () => const Center(child: CircularProgressIndicator()),
      error: (_, __) =>
          const Center(child: Text('Ошибка загрузки конфигурации')),
      data: (config) {
        // Определяем цену
        int price;
        if (s.serviceType == 'exam') {
          price = config.priceExam;
        } else {
          price = config.priceTraining;
        }

        return _Step6ConfirmBody(
          state: s,
          price: price,
          paymentMethod: config.paymentMethod,
          onCertChange: onCertChange,
        );
      },
    );
  }
}

class _Step6ConfirmBody extends StatefulWidget {
  final _WizardState state;
  final int price;
  final String paymentMethod;
  final void Function(String) onCertChange;
  const _Step6ConfirmBody({
    required this.state,
    required this.price,
    required this.paymentMethod,
    required this.onCertChange,
  });

  @override
  State<_Step6ConfirmBody> createState() => _Step6ConfirmBodyState();
}

class _Step6ConfirmBodyState extends State<_Step6ConfirmBody> {
  final _certCtrl = TextEditingController();

  @override
  void dispose() {
    _certCtrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final s = widget.state;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const _StepTitle('Подтверждение записи'),
        const SizedBox(height: 24),
        _ConfirmRow(label: 'Тип', value: serviceTypeLabel(s.serviceType ?? '')),
        _ConfirmRow(
            label: 'Коробка', value: transmissionLabel(s.transmission ?? '')),
        const _ConfirmRow(label: 'Площадка', value: kFixedBookingLocation),
        _ConfirmRow(
            label: 'Дата',
            value: formatDate(s.date!.toIso8601String().substring(0, 10))),
        _ConfirmRow(label: 'Время', value: s.slot ?? ''),
        _ConfirmRow(label: 'Оплата', value: widget.paymentMethod),
        const Divider(height: 32),
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            const Text('Стоимость',
                style: TextStyle(fontSize: 16, fontWeight: FontWeight.w600)),
            Text(formatPrice(widget.price),
                style: const TextStyle(
                    fontSize: 20,
                    fontWeight: FontWeight.w800,
                    color: AppColors.primary)),
          ],
        ),
        const SizedBox(height: 24),
        TextFormField(
          controller: _certCtrl,
          textCapitalization: TextCapitalization.characters,
          onChanged: widget.onCertChange,
          decoration: const InputDecoration(
            labelText: 'Код сертификата (необязательно)',
            helperText: 'Сертификат должен точно совпадать\nс ценой услуги',
            helperMaxLines: 2,
            prefixIcon: Icon(Icons.card_giftcard_outlined),
          ),
        ),
        const SizedBox(height: 12),
      ],
    );
  }
}

class _ConfirmRow extends StatelessWidget {
  final String label;
  final String value;
  const _ConfirmRow({required this.label, required this.value});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Row(
        children: [
          SizedBox(
            width: 90,
            child: Text(label,
                style: const TextStyle(
                    color: AppColors.textSecondary, fontSize: 14)),
          ),
          Expanded(
            child: Text(value,
                style: const TextStyle(
                    fontWeight: FontWeight.w600,
                    color: AppColors.textPrimary,
                    fontSize: 14)),
          ),
        ],
      ),
    );
  }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

class _StepTitle extends StatelessWidget {
  final String text;
  const _StepTitle(this.text);

  @override
  Widget build(BuildContext context) => Text(
        text,
        style: const TextStyle(
            fontSize: 20,
            fontWeight: FontWeight.w700,
            color: AppColors.textPrimary),
      );
}

class _ChoiceCard extends StatelessWidget {
  final bool selected;
  final IconData icon;
  final String title;
  final String subtitle;
  final String price;
  final Color color;
  final VoidCallback onTap;

  const _ChoiceCard({
    required this.selected,
    required this.icon,
    required this.title,
    required this.subtitle,
    required this.price,
    required this.color,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 150),
        padding: const EdgeInsets.all(20),
        decoration: BoxDecoration(
          color: selected ? color : Colors.white,
          borderRadius: BorderRadius.circular(16),
          border: Border.all(
            color: selected ? color : AppColors.divider,
            width: selected ? 2 : 1,
          ),
        ),
        child: Row(
          children: [
            Container(
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: selected
                    ? Colors.white.withValues(alpha: 0.2)
                    : color.withValues(alpha: 0.1),
                shape: BoxShape.circle,
              ),
              child:
                  Icon(icon, color: selected ? Colors.white : color, size: 28),
            ),
            const SizedBox(width: 16),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(title,
                      style: TextStyle(
                          fontWeight: FontWeight.w700,
                          fontSize: 16,
                          color:
                              selected ? Colors.white : AppColors.textPrimary)),
                  const SizedBox(height: 4),
                  Text(subtitle,
                      style: TextStyle(
                          fontSize: 13,
                          color: selected
                              ? Colors.white70
                              : AppColors.textSecondary)),
                  if (price.isNotEmpty) ...[
                    const SizedBox(height: 4),
                    Text(price,
                        style: TextStyle(
                            fontSize: 13,
                            fontWeight: FontWeight.w600,
                            color: selected ? Colors.white : color)),
                  ],
                ],
              ),
            ),
            if (selected)
              const Icon(Icons.check_circle, color: Colors.white, size: 24),
          ],
        ),
      ),
    );
  }
}

class _StepIndicator extends StatelessWidget {
  final int currentStep;
  final List<String> steps;
  const _StepIndicator({required this.currentStep, required this.steps});

  @override
  Widget build(BuildContext context) {
    return Container(
      color: Colors.white,
      padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 12),
      child: Row(
        children: List.generate(steps.length * 2 - 1, (i) {
          if (i.isOdd) {
            return Expanded(
              child: Container(
                height: 2,
                color: i ~/ 2 < currentStep
                    ? AppColors.primary
                    : AppColors.divider,
              ),
            );
          }
          final idx = i ~/ 2;
          final done = idx < currentStep;
          final active = idx == currentStep;
          return Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              AnimatedContainer(
                duration: const Duration(milliseconds: 200),
                width: 28,
                height: 28,
                decoration: BoxDecoration(
                  color:
                      done || active ? AppColors.primary : AppColors.background,
                  shape: BoxShape.circle,
                  border: Border.all(
                    color:
                        done || active ? AppColors.primary : AppColors.divider,
                  ),
                ),
                child: Center(
                  child: done
                      ? const Icon(Icons.check, size: 16, color: Colors.white)
                      : Text('${idx + 1}',
                          style: TextStyle(
                            fontSize: 12,
                            fontWeight: FontWeight.w600,
                            color: active ? Colors.white : AppColors.textHint,
                          )),
                ),
              ),
            ],
          );
        }),
      ),
    );
  }
}

class _BottomBar extends StatelessWidget {
  final int step;
  final bool canNext;
  final bool submitting;
  final VoidCallback? onBack;
  final VoidCallback? onNext;

  const _BottomBar({
    required this.step,
    required this.canNext,
    required this.submitting,
    this.onBack,
    this.onNext,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.fromLTRB(20, 12, 20, 24),
      decoration: const BoxDecoration(
        color: Colors.white,
        border: Border(top: BorderSide(color: AppColors.divider)),
      ),
      child: Row(
        children: [
          if (onBack != null) ...[
            Expanded(
              child: OutlinedButton(
                onPressed: onBack,
                style: OutlinedButton.styleFrom(minimumSize: const Size(0, 52)),
                child: const Text('Назад'),
              ),
            ),
            const SizedBox(width: 12),
          ],
          Expanded(
            flex: 2,
            child: ElevatedButton(
              onPressed: canNext && !submitting ? onNext : null,
              child: submitting
                  ? const SizedBox(
                      width: 24,
                      height: 24,
                      child: CircularProgressIndicator(
                          color: Colors.white, strokeWidth: 2))
                  : Text(step >= 5 ? 'Записаться' : 'Далее'),
            ),
          ),
        ],
      ),
    );
  }
}
