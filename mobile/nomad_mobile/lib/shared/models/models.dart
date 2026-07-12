// ─── App Config ─────────────────────────────────────────────────────────────

class AppConfig {
  final int priceTraining;
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
        priceTraining: j['price_training'] as int? ?? 6000,
        priceExam: j['price_exam'] as int? ?? 5000,
        trainingDurationMinutes: j['training_duration_minutes'] as int? ?? 60,
        examDurationMinutes: j['exam_duration_minutes'] as int? ?? 15,
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
  final String email;

  TokenResponse({
    required this.accessToken,
    required this.refreshToken,
    required this.userId,
    required this.name,
    required this.email,
  });

  factory TokenResponse.fromJson(Map<String, dynamic> j) => TokenResponse(
        accessToken: j['access_token'] as String,
        refreshToken: j['refresh_token'] as String,
        userId: j['user_id'] as int,
        name: j['name'] as String,
        email: j['email'] as String,
      );
}

// ─── Profile ───────────────────────────────────────────────────────────────

class UserProfile {
  final int id;
  final String name;
  final String phone;
  final String email;
  final String? referralCode;
  final String createdAt;

  UserProfile({
    required this.id,
    required this.name,
    required this.phone,
    required this.email,
    this.referralCode,
    required this.createdAt,
  });

  factory UserProfile.fromJson(Map<String, dynamic> j) => UserProfile(
        id: j['id'] as int,
        name: j['name'] as String,
        phone: j['phone'] as String,
        email: j['email'] as String,
        referralCode: j['referral_code'] as String?,
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

  factory Instructor.fromJson(Map<String, dynamic> j) => Instructor(
        id: j['id'] as int? ?? 0,
        name: j['name'] as String,
        transmission: j['transmission'] as String? ?? 'both',
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
  final int price;
  final Instructor? instructor;
  final String? ratingVote;
  final String createdAt;

  Booking({
    required this.id,
    required this.serviceType,
    required this.transmission,
    required this.location,
    required this.bookingDate,
    required this.startTime,
    required this.endTime,
    required this.status,
    required this.price,
    this.instructor,
    this.ratingVote,
    required this.createdAt,
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
        price: j['price'] as int,
        instructor: j['instructor'] != null
            ? Instructor.fromJson(j['instructor'] as Map<String, dynamic>)
            : null,
        ratingVote: j['rating_vote'] as String?,
        createdAt: j['created_at'] as String,
      );

  bool get isUpcoming =>
      status == 'planned' || status == 'confirmed' || status == 'in_progress';
  bool get canCancel => status == 'planned' || status == 'confirmed';
  bool get canRate => status == 'completed' && ratingVote == null;
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

  UserPackage({
    required this.id,
    required this.packageId,
    required this.name,
    required this.sessionsCount,
    required this.remainingSessions,
    required this.isActive,
    required this.purchasedAt,
  });

  factory UserPackage.fromJson(Map<String, dynamic> j) => UserPackage(
        id: j['id'] as int,
        packageId: j['package_id'] as int,
        name: j['name'] as String,
        sessionsCount: j['sessions_count'] as int,
        remainingSessions: j['remaining_sessions'] as int,
        isActive: j['is_active'] as bool,
        purchasedAt: j['purchased_at'] as String,
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
