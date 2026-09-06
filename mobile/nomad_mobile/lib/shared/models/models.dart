// ─── App Config ─────────────────────────────────────────────────────────────

class AppConfig {
  final int priceTraining;
  final int priceTrainingNew;
  final int priceExam;
  final int trainingDurationMinutes;
  final int examDurationMinutes;
  final String locationMain;
  final String locationExam;
  final int workingHoursStart;
  final int workingHoursEnd;
  final String phone;
  final String paymentMethod;
  final String carModelManual;
  final String carModelAutomatic;

  AppConfig({
    required this.priceTraining,
    required this.priceTrainingNew,
    required this.priceExam,
    required this.trainingDurationMinutes,
    required this.examDurationMinutes,
    required this.locationMain,
    required this.locationExam,
    required this.workingHoursStart,
    required this.workingHoursEnd,
    required this.phone,
    required this.paymentMethod,
    required this.carModelManual,
    required this.carModelAutomatic,
  });

  factory AppConfig.fromJson(Map<String, dynamic> j) => AppConfig(
        priceTraining: j['price_training'] as int? ?? 10000,
        priceTrainingNew: j['price_training_new'] as int? ?? 10000,
        priceExam: j['price_exam'] as int? ?? 5000,
        trainingDurationMinutes: j['training_duration_minutes'] as int? ?? 60,
        examDurationMinutes: j['exam_duration_minutes'] as int? ?? 20,
        locationMain: j['location_main'] as String? ?? '',
        locationExam: j['location_exam'] as String? ?? '',
        workingHoursStart: j['working_hours_start'] as int? ?? 9,
        workingHoursEnd: j['working_hours_end'] as int? ?? 19,
        phone: j['phone'] as String? ?? '',
        paymentMethod: j['payment_method'] as String? ?? '',
        carModelManual: j['car_model_manual'] as String? ?? '',
        carModelAutomatic: j['car_model_automatic'] as String? ?? '',
      );
}

// ─── Auth ──────────────────────────────────────────────────────────────────

class TokenResponse {
  final String accessToken;
  final String refreshToken;
  final int userId;
  final String name;

  TokenResponse({
    required this.accessToken,
    required this.refreshToken,
    required this.userId,
    required this.name,
  });

  factory TokenResponse.fromJson(Map<String, dynamic> j) => TokenResponse(
        accessToken: j['access_token'] as String,
        refreshToken: j['refresh_token'] as String,
        userId: j['user_id'] as int,
        name: j['name'] as String,
      );
}

// ─── Profile ───────────────────────────────────────────────────────────────

class UserProfile {
  final int id;
  final String name;
  final String phone;
  final String? referralCode;
  final String? avatarUrl;
  final String createdAt;

  UserProfile({
    required this.id,
    required this.name,
    required this.phone,
    this.referralCode,
    this.avatarUrl,
    required this.createdAt,
  });

  factory UserProfile.fromJson(Map<String, dynamic> j) => UserProfile(
        id: j['id'] as int,
        name: j['name'] as String,
        phone: j['phone'] as String,
        referralCode: j['referral_code'] as String?,
        avatarUrl: j['avatar_url'] as String?,
        createdAt: j['created_at'] as String,
      );
}

// ─── Instructor ────────────────────────────────────────────────────────────

class Instructor {
  final int id;
  final String name;
  final String transmission;
  final int experienceYears;
  final String description;
  final double rating;
  final String? avatarUrl;

  Instructor({
    required this.id,
    required this.name,
    required this.transmission,
    required this.experienceYears,
    required this.description,
    required this.rating,
    this.avatarUrl,
  });

  /// The public API may contain either the technical values used by the
  /// backend (`manual`, `automatic`, `both`) or the Russian labels saved in
  /// older instructor records.  Keep one canonical value in the app so the
  /// label and the transmission filters always use the actual value from DB.
  static String normalizeTransmission(Object? value) {
    final transmission = value?.toString().trim().toLowerCase() ?? '';

    final isManual = transmission == 'manual' ||
        transmission.contains('механ') ||
        transmission.contains('мкпп');
    final isAutomatic = transmission == 'automatic' ||
        transmission == 'auto' ||
        transmission.contains('автомат') ||
        transmission.contains('акпп');

    if (transmission == 'both' ||
        transmission == 'all' ||
        (isManual && isAutomatic)) {
      return 'both';
    }
    if (isManual) return 'manual';
    if (isAutomatic) return 'automatic';

    // An empty or unknown legacy value remains available in both sections.
    return 'both';
  }

  factory Instructor.fromJson(Map<String, dynamic> j) => Instructor(
        id: j['id'] as int? ?? 0,
        name: j['name'] as String,
        transmission: normalizeTransmission(j['transmission']),
        experienceYears: j['experience_years'] as int? ?? 0,
        description: j['description'] as String? ?? '',
        rating: (j['rating'] as num?)?.toDouble() ?? 5.0,
        avatarUrl: j['avatar_url'] as String?,
      );
}

// ─── Booking ───────────────────────────────────────────────────────────────

class Booking {
  final int id;
  final String serviceType;
  final String transmission;
  final String location;
  final String bookingDate;
  final String startTime;
  final String endTime;
  final String status;
  final String? bookingNumber;
  final int price;
  final int basePrice;
  final int certificateAmount;
  final int referralDiscountAmount;
  final String paymentStatus;
  final int paidAmount;
  final int? packageSessionsUsed;
  final int? packageSessionsTotal;
  final Instructor? instructor;
  final String? ratingVote;
  final String createdAt;
  final bool confirmedByClient;

  Booking({
    required this.id,
    required this.serviceType,
    required this.transmission,
    required this.location,
    required this.bookingDate,
    required this.startTime,
    required this.endTime,
    required this.status,
    this.bookingNumber,
    required this.price,
    required this.basePrice,
    this.certificateAmount = 0,
    this.referralDiscountAmount = 0,
    this.paymentStatus = 'unpaid',
    this.paidAmount = 0,
    this.packageSessionsUsed,
    this.packageSessionsTotal,
    this.instructor,
    this.ratingVote,
    required this.createdAt,
    this.confirmedByClient = false,
  });

  factory Booking.fromJson(Map<String, dynamic> j) => Booking(
        id: j['id'] as int,
        serviceType: j['service_type'] as String,
        transmission: j['transmission'] as String,
        location: j['location'] as String,
        bookingDate: j['booking_date'] as String,
        startTime: j['start_time'] as String,
        endTime: j['end_time'] as String,
        status: j['status'] as String,
        bookingNumber: j['booking_number'] as String?,
        price: j['price'] as int,
        basePrice: j['base_price'] as int? ?? j['price'] as int,
        certificateAmount: j['certificate_amount'] as int? ?? 0,
        referralDiscountAmount: j['referral_discount_amount'] as int? ?? 0,
        paymentStatus: j['payment_status'] as String? ?? 'unpaid',
        paidAmount: j['paid_amount'] as int? ?? 0,
        packageSessionsUsed: j['package_sessions_used'] as int?,
        packageSessionsTotal: j['package_sessions_total'] as int?,
        instructor: j['instructor'] != null
            ? Instructor.fromJson(j['instructor'] as Map<String, dynamic>)
            : null,
        ratingVote: j['rating_vote'] as String?,
        confirmedByClient: j['confirmed_by_client'] as bool? ?? false,
        createdAt: j['created_at'] as String,
      );

  bool get hasCertificate => certificateAmount > 0;
  bool get hasReferralDiscount => referralDiscountAmount > 0;
  bool get isPaid => paymentStatus == 'paid';
  bool get isPending => status == 'pending';
  bool get isCancellationPending => status == 'cancellation_pending';
  bool get isReschedulePending => status == 'reschedule_pending';
  bool get isUpcoming =>
      status == 'pending' ||
      status == 'cancellation_pending' ||
      status == 'reschedule_pending' ||
      status == 'planned' ||
      status == 'confirmed' ||
      status == 'in_progress';
  bool get canCancel =>
      status == 'pending' || status == 'planned' || status == 'confirmed';
  bool get canRate => status == 'completed' && ratingVote == null;

  /// Человекочитаемый тип занятия для текста уведомления.
  String get lessonTypeLabel =>
      serviceType == 'exam' ? 'Пробный экзамен' : 'Урок вождения';

  /// Дата и время начала занятия, либо null если не удалось распознать.
  DateTime? get startDateTime {
    try {
      final d = DateTime.parse(bookingDate);
      final parts = startTime.split(':');
      final h = int.parse(parts[0]);
      final m = parts.length > 1 ? int.parse(parts[1]) : 0;
      return DateTime(d.year, d.month, d.day, h, m);
    } catch (_) {
      return null;
    }
  }

  /// Дата и время окончания занятия, либо null.
  DateTime? get endDateTime {
    try {
      final d = DateTime.parse(bookingDate);
      final parts = endTime.split(':');
      final h = int.parse(parts[0]);
      final m = parts.length > 1 ? int.parse(parts[1]) : 0;
      return DateTime(d.year, d.month, d.day, h, m);
    } catch (_) {
      return null;
    }
  }
}

// ─── Package ───────────────────────────────────────────────────────────────

class Package {
  final int id;
  final String name;
  final int sessionsCount;
  final int price;

  Package({
    required this.id,
    required this.name,
    required this.sessionsCount,
    required this.price,
  });

  factory Package.fromJson(Map<String, dynamic> j) => Package(
        id: j['id'] as int,
        name: j['name'] as String,
        sessionsCount: j['sessions_count'] as int,
        price: j['price'] as int,
      );
}

class UserPackage {
  final int id;
  final int packageId;
  final String name;
  final int sessionsCount;
  final int remainingSessions;
  final bool isActive;
  final String purchasedAt;
  final String? expiresAt;
  final String? code;
  final int remainingBonusExams;

  UserPackage({
    required this.id,
    required this.packageId,
    required this.name,
    required this.sessionsCount,
    required this.remainingSessions,
    required this.isActive,
    required this.purchasedAt,
    this.expiresAt,
    this.code,
    this.remainingBonusExams = 0,
  });

  factory UserPackage.fromJson(Map<String, dynamic> j) => UserPackage(
        id: j['id'] as int,
        packageId: j['package_id'] as int,
        name: j['name'] as String,
        sessionsCount: j['sessions_count'] as int,
        remainingSessions: j['remaining_sessions'] as int,
        isActive: j['is_active'] as bool,
        purchasedAt: j['purchased_at'] as String,
        expiresAt: j['expires_at'] as String?,
        code: j['code'] as String?,
        remainingBonusExams: j['remaining_bonus_exams'] as int? ?? 0,
      );
}

// ─── Certificate ───────────────────────────────────────────────────────────

class UserCertificate {
  final int id;
  final String code;
  final int nominal;
  final int remaining;
  final bool isSpent;
  final String activatedAt;

  UserCertificate({
    required this.id,
    required this.code,
    required this.nominal,
    required this.remaining,
    required this.isSpent,
    required this.activatedAt,
  });

  factory UserCertificate.fromJson(Map<String, dynamic> j) => UserCertificate(
        id: j['id'] as int,
        code: j['code'] as String,
        nominal: j['nominal'] as int,
        remaining: j['remaining'] as int,
        isSpent: j['is_spent'] as bool,
        activatedAt: j['activated_at'] as String,
      );
}

// ─── FAQ ───────────────────────────────────────────────────────────────────

class FaqItem {
  final int id;
  final String question;
  final String answer;

  FaqItem({required this.id, required this.question, required this.answer});

  factory FaqItem.fromJson(Map<String, dynamic> j) => FaqItem(
        id: j['id'] as int,
        question: j['question'] as String,
        answer: j['answer'] as String,
      );
}

// ─── Support message ───────────────────────────────────────────────────────

class SupportMessage {
  final int id;
  final String sender; // 'user' | 'admin'
  final String text;
  final bool isRead;
  final String createdAt;

  SupportMessage({
    required this.id,
    required this.sender,
    required this.text,
    required this.isRead,
    required this.createdAt,
  });

  factory SupportMessage.fromJson(Map<String, dynamic> j) => SupportMessage(
        id: j['id'] as int,
        sender: j['sender'] as String,
        text: j['text'] as String,
        isRead: j['is_read'] as bool,
        createdAt: j['created_at'] as String,
      );
}

// ─── Referral ──────────────────────────────────────────────────────────────

class ReferralInfo {
  final String referralCode;
  final String referralLink;
  final int referredCount;
  final List<ReferredUser> referredUsers;

  ReferralInfo({
    required this.referralCode,
    required this.referralLink,
    required this.referredCount,
    required this.referredUsers,
  });

  factory ReferralInfo.fromJson(Map<String, dynamic> j) => ReferralInfo(
        referralCode: j['referral_code'] as String? ?? '',
        referralLink: j['referral_link'] as String? ?? '',
        referredCount: j['referred_count'] as int? ?? 0,
        referredUsers: (j['referred_users'] as List<dynamic>? ?? [])
            .map((e) => ReferredUser.fromJson(e as Map<String, dynamic>))
            .toList(),
      );
}

class ReferredUser {
  final int id;
  final String name;
  final String joinedAt;
  final bool discountApplied;

  ReferredUser({
    required this.id,
    required this.name,
    required this.joinedAt,
    required this.discountApplied,
  });

  factory ReferredUser.fromJson(Map<String, dynamic> j) => ReferredUser(
        id: j['id'] as int,
        name: j['name'] as String,
        joinedAt: j['joined_at'] as String,
        discountApplied: j['discount_applied'] as bool,
      );
}
