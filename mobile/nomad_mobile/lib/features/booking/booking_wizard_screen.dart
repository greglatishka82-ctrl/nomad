import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:dio/dio.dart';
import 'package:table_calendar/table_calendar.dart';

import '../../core/api/api_client.dart';
import '../../core/theme/app_theme.dart';
import '../../core/utils/formatters.dart';
import '../../shared/models/models.dart';
import '../my_bookings/bookings_provider.dart';

// ── State ────────────────────────────────────────────────────────────────────

class _WizardState {
  final int step;
  final String? serviceType;     // training | exam
  final String? transmission;    // manual | automatic
  final DateTime? date;
  final String? slot;            // "10:00"
  final String? certificateCode;
  final int? packageId;
  final List<String> slots;
  final bool slotsLoading;

  const _WizardState({
    this.step = 0,
    this.serviceType,
    this.transmission,
    this.date,
    this.slot,
    this.certificateCode,
    this.packageId,
    this.slots = const [],
    this.slotsLoading = false,
  });

  _WizardState copyWith({
    int? step,
    String? serviceType,
    String? transmission,
    DateTime? date,
    String? slot,
    String? certificateCode,
    int? packageId,
    List<String>? slots,
    bool? slotsLoading,
  }) =>
      _WizardState(
        step: step ?? this.step,
        serviceType: serviceType ?? this.serviceType,
        transmission: transmission ?? this.transmission,
        date: date ?? this.date,
        slot: slot ?? this.slot,
        certificateCode: certificateCode ?? this.certificateCode,
        packageId: packageId ?? this.packageId,
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

class _BookingWizardScreenState
    extends ConsumerState<BookingWizardScreen> {
  _WizardState _state = const _WizardState();
  bool _submitting = false;

  void _update(_WizardState s) => setState(() => _state = s);

  Future<void> _loadSlots() async {
    if (_state.date == null ||
        _state.serviceType == null ||
        _state.transmission == null) return;
    _update(_state.copyWith(slotsLoading: true, slots: []));
    try {
      final dio = ref.read(dioProvider);
      final resp = await dio.get('/api/mobile/slots', queryParameters: {
        'booking_date':
            _state.date!.toIso8601String().substring(0, 10),
        'service_type': _state.serviceType,
        'transmission': _state.transmission,
      });
      final data = resp.data as Map<String, dynamic>;
      final slots =
          (data['slots'] as List).map((e) => e.toString()).toList();
      _update(_state.copyWith(slots: slots, slotsLoading: false));
    } catch (_) {
      _update(_state.copyWith(slotsLoading: false));
    }
  }

  Future<void> _submit() async {
    setState(() => _submitting = true);
    try {
      final dio = ref.read(dioProvider);
      await dio.post('/api/mobile/bookings', data: {
        'service_type': _state.serviceType,
        'transmission': _state.transmission,
        'booking_date':
            _state.date!.toIso8601String().substring(0, 10),
        'start_time': _state.slot,
        if (_state.certificateCode != null &&
            _state.certificateCode!.isNotEmpty)
          'certificate_code': _state.certificateCode,
        if (_state.packageId != null)
          'use_package_id': _state.packageId,
      });
      ref.invalidate(upcomingBookingsProvider);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('Вы успешно записаны! 🎉'),
            backgroundColor: AppColors.success,
          ),
        );
        context.go('/bookings');
      }
    } on DioException catch (e) {
      final msg =
          (e.response?.data as Map?)?['detail'] ?? 'Ошибка записи';
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
              content: Text(msg.toString()),
              backgroundColor: AppColors.error),
        );
      }
    } finally {
      if (mounted) setState(() => _submitting = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final steps = ['Тип', 'КПП', 'Дата', 'Время', 'Итог'];
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
          // Прогресс-бар
          _StepIndicator(currentStep: _state.step, steps: steps),
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
                    if (_state.step < 4) {
                      final next = _state.step + 1;
                      _update(_state.copyWith(step: next));
                      if (next == 3) _loadSlots();
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
      case 0:
        return _state.serviceType != null;
      case 1:
        return _state.transmission != null;
      case 2:
        return _state.date != null;
      case 3:
        return _state.slot != null;
      case 4:
        return true;
      default:
        return false;
    }
  }

  Widget _buildStep() {
    switch (_state.step) {
      case 0:
        return _Step1ServiceType(
          selected: _state.serviceType,
          onSelect: (v) =>
              _update(_state.copyWith(serviceType: v, slot: null)),
        );
      case 1:
        return _Step2Transmission(
          selected: _state.transmission,
          onSelect: (v) =>
              _update(_state.copyWith(transmission: v, slot: null)),
        );
      case 2:
        return _Step3Date(
          selected: _state.date,
          onSelect: (d) =>
              _update(_state.copyWith(date: d, slot: null)),
        );
      case 3:
        return _Step4Time(
          slots: _state.slots,
          selected: _state.slot,
          loading: _state.slotsLoading,
          onSelect: (s) => _update(_state.copyWith(slot: s)),
        );
      case 4:
        return _Step5Confirm(
          state: _state,
          onCertChange: (v) =>
              _update(_state.copyWith(certificateCode: v)),
        );
      default:
        return const SizedBox();
    }
  }
}

// ── Step 1: Тип занятия ───────────────────────────────────────────────────────

class _Step1ServiceType extends ConsumerWidget {
  final String? selected;
  final void Function(String) onSelect;
  const _Step1ServiceType(
      {required this.selected, required this.onSelect});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final configAsync = ref.watch(appConfigProvider);
    return configAsync.when(
      loading: () => const Center(child: CircularProgressIndicator()),
      error: (_, __) => const Center(child: Text('Ошибка загрузки конфигурации')),
      data: (config) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const _StepTitle('Выберите тип занятия'),
          const SizedBox(height: 24),
          _ChoiceCard(
            selected: selected == 'training',
            icon: Icons.directions_car,
            title: 'Урок вождения',
            subtitle: 'Учебная площадка • ${config.locationMain}',
            price: '${formatPrice(config.priceTraining)}/час • ${config.trainingDurationMinutes} минут',
            color: AppColors.primary,
            onTap: () => onSelect('training'),
          ),
          const SizedBox(height: 16),
          _ChoiceCard(
            selected: selected == 'exam',
            icon: Icons.assignment_turned_in_outlined,
            title: 'Пробный экзамен',
            subtitle: 'Экзаменационная площадка • ${config.locationExam}',
            price: '${formatPrice(config.priceExam)} • ${config.examDurationMinutes} минут',
            color: AppColors.accent,
            onTap: () => onSelect('exam'),
          ),
        ],
      ),
    );
  }
}

// ── Step 2: КПП ───────────────────────────────────────────────────────────────

class _Step2Transmission extends ConsumerWidget {
  final String? selected;
  final void Function(String) onSelect;
  const _Step2Transmission(
      {required this.selected, required this.onSelect});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final configAsync = ref.watch(appConfigProvider);
    return configAsync.when(
      loading: () => const Center(child: CircularProgressIndicator()),
      error: (_, __) => const Center(child: Text('Ошибка загрузки конфигурации')),
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

// ── Step 3: Дата ──────────────────────────────────────────────────────────────

class _Step3Date extends StatelessWidget {
  final DateTime? selected;
  final void Function(DateTime) onSelect;
  const _Step3Date({required this.selected, required this.onSelect});

  @override
  Widget build(BuildContext context) {
    final now = DateTime.now();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const _StepTitle('Выберите дату'),
        const SizedBox(height: 16),
        Container(
          decoration: BoxDecoration(
            color: Colors.white,
            borderRadius: BorderRadius.circular(16),
            border: Border.all(color: AppColors.divider),
          ),
          child: TableCalendar(
            locale: 'ru_RU',
            firstDay: now,
            lastDay: now.add(const Duration(days: 30)),
            focusedDay: selected ?? now,
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
              weekendTextStyle:
                  const TextStyle(color: AppColors.error),
            ),
            headerStyle: const HeaderStyle(
              formatButtonVisible: false,
              titleCentered: true,
              titleTextStyle: TextStyle(
                  fontWeight: FontWeight.w700, fontSize: 16),
            ),
          ),
        ),
      ],
    );
  }
}

// ── Step 4: Время ─────────────────────────────────────────────────────────────

class _Step4Time extends StatelessWidget {
  final List<String> slots;
  final String? selected;
  final bool loading;
  final void Function(String) onSelect;

  const _Step4Time({
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
                Icon(Icons.event_busy,
                    size: 48, color: AppColors.textHint),
                SizedBox(height: 12),
                Text('На эту дату нет свободных слотов',
                    textAlign: TextAlign.center,
                    style:
                        TextStyle(color: AppColors.textSecondary)),
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
      {required this.time,
      required this.selected,
      required this.onTap});

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
            color:
                selected ? AppColors.primary : AppColors.divider,
            width: selected ? 2 : 1,
          ),
        ),
        child: Center(
          child: Text(
            time,
            style: TextStyle(
              fontSize: 16,
              fontWeight: FontWeight.w600,
              color:
                  selected ? Colors.white : AppColors.textPrimary,
            ),
          ),
        ),
      ),
    );
  }
}

// ── Step 5: Подтверждение ─────────────────────────────────────────────────────

class _Step5Confirm extends ConsumerWidget {
  final _WizardState state;
  final void Function(String) onCertChange;
  const _Step5Confirm(
      {required this.state, required this.onCertChange});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final s = state;
    final configAsync = ref.watch(appConfigProvider);
    return configAsync.when(
      loading: () => const Center(child: CircularProgressIndicator()),
      error: (_, __) => const Center(child: Text('Ошибка загрузки конфигурации')),
      data: (config) {
        final price = s.serviceType == 'training'
            ? config.priceTraining
            : config.priceExam;
        final location = s.serviceType == 'training'
            ? config.locationMain
            : config.locationExam;
        return _Step5ConfirmBody(
          state: s,
          price: price,
          location: location,
          paymentMethod: config.paymentMethod,
          onCertChange: onCertChange,
        );
      },
    );
  }
}

class _Step5ConfirmBody extends StatefulWidget {
  final _WizardState state;
  final int price;
  final String location;
  final String paymentMethod;
  final void Function(String) onCertChange;
  const _Step5ConfirmBody({
    required this.state,
    required this.price,
    required this.location,
    required this.paymentMethod,
    required this.onCertChange,
  });

  @override
  State<_Step5ConfirmBody> createState() => _Step5ConfirmBodyState();
}

class _Step5ConfirmBodyState extends State<_Step5ConfirmBody> {
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
        _ConfirmRow(
            label: 'Тип',
            value: serviceTypeLabel(s.serviceType ?? '')),
        _ConfirmRow(
            label: 'Коробка',
            value: transmissionLabel(s.transmission ?? '')),
        _ConfirmRow(
            label: 'Площадка', value: widget.location),
        _ConfirmRow(
            label: 'Дата',
            value: formatDate(
                s.date!.toIso8601String().substring(0, 10))),
        _ConfirmRow(label: 'Время', value: s.slot ?? ''),
        _ConfirmRow(label: 'Оплата', value: widget.paymentMethod),
        const Divider(height: 32),
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            const Text('Стоимость',
                style: TextStyle(
                    fontSize: 16, fontWeight: FontWeight.w600)),
            Text(formatPrice(widget.price),
                style: const TextStyle(
                    fontSize: 20,
                    fontWeight: FontWeight.w800,
                    color: AppColors.primary)),
          ],
        ),
        const SizedBox(height: 24),
        // Сертификат
        TextFormField(
          controller: _certCtrl,
          textCapitalization: TextCapitalization.characters,
          onChanged: widget.onCertChange,
          decoration: const InputDecoration(
            labelText: 'Промокод / сертификат (необязательно)',
            prefixIcon: Icon(Icons.card_giftcard_outlined),
          ),
        ),
        const SizedBox(height: 12),
        Container(
          padding: const EdgeInsets.all(14),
          decoration: BoxDecoration(
            color: AppColors.primary.withValues(alpha: 0.06),
            borderRadius: BorderRadius.circular(12),
          ),
          child: const Row(
            children: [
              Icon(Icons.info_outline,
                  size: 18, color: AppColors.primary),
              SizedBox(width: 10),
              Expanded(
                child: Text(
                  'Инструктор назначается автоматически из свободных специалистов с наивысшим рейтингом.',
                  style: TextStyle(
                      color: AppColors.primary,
                      fontSize: 13,
                      height: 1.4),
                ),
              ),
            ],
          ),
        ),
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
              child: Icon(icon,
                  color: selected ? Colors.white : color, size: 28),
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
                          color: selected
                              ? Colors.white
                              : AppColors.textPrimary)),
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
                            color: selected
                                ? Colors.white
                                : color)),
                  ],
                ],
              ),
            ),
            if (selected)
              const Icon(Icons.check_circle,
                  color: Colors.white, size: 24),
          ],
        ),
      ),
    );
  }
}

class _StepIndicator extends StatelessWidget {
  final int currentStep;
  final List<String> steps;
  const _StepIndicator(
      {required this.currentStep, required this.steps});

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
                  color: done || active
                      ? AppColors.primary
                      : AppColors.background,
                  shape: BoxShape.circle,
                  border: Border.all(
                    color: done || active
                        ? AppColors.primary
                        : AppColors.divider,
                  ),
                ),
                child: Center(
                  child: done
                      ? const Icon(Icons.check,
                          size: 16, color: Colors.white)
                      : Text('${idx + 1}',
                          style: TextStyle(
                            fontSize: 12,
                            fontWeight: FontWeight.w600,
                            color: active
                                ? Colors.white
                                : AppColors.textHint,
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
                style: OutlinedButton.styleFrom(
                    minimumSize: const Size(0, 52)),
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
                  : Text(step == 4 ? 'Записаться' : 'Далее'),
            ),
          ),
        ],
      ),
    );
  }
}
