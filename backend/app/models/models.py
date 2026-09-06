import enum
from datetime import datetime, date, time
from zoneinfo import ZoneInfo

from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime, Date, Time,
    ForeignKey, Text, UniqueConstraint, text
)
from sqlalchemy.orm import relationship

from app.database import Base

# Таймзона Павлодар/Алматы (UTC+6)
KZ_TZ = ZoneInfo("Asia/Almaty")

def now_kz():
    """Возвращает текущее время в таймзоне Казахстана (naive datetime для PostgreSQL TIMESTAMP WITHOUT TIME ZONE)"""
    return datetime.now(KZ_TZ).replace(tzinfo=None)


class BookingStatus(str, enum.Enum):
    PENDING = "pending"
    PLANNED = "planned"
    CONFIRMED = "confirmed"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"
    CONFLICT = "conflict"
    DISPUTED = "disputed"
    RESCHEDULE_PENDING = "reschedule_pending"


class ServiceType(str, enum.Enum):
    TRAINING = "training"
    EXAM = "exam"


class TransmissionType(str, enum.Enum):
    MANUAL = "manual"
    AUTOMATIC = "automatic"
    BOTH = "both"


class InstructorGender(str, enum.Enum):
    MALE = "male"
    FEMALE = "female"
    ANY = "any"


class RatingVote(str, enum.Enum):
    GOOD = "good"
    NORMAL = "normal"
    BAD = "bad"


class Admin(Base):
    __tablename__ = "admins"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(100), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=now_kz)


class Instructor(Base):
    __tablename__ = "instructors"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    phone = Column(String(30), nullable=True)
    telegram_id = Column(String(50), nullable=True)
    telegram_username = Column(String(100), nullable=True)
    transmission = Column(String(50), nullable=False, default="both")
    # training | exam | both. Existing instructors default to both so the
    # rollout never makes an already working schedule unavailable.
    lesson_type = Column(String(50), nullable=False, default="both")
    gender = Column(String(50), nullable=False, default="any")
    experience_years = Column(Integer, default=0)
    rating = Column(Float, default=5.0)
    is_active = Column(Boolean, default=True)
    is_duty = Column(Boolean, default=False)
    is_lead = Column(Boolean, default=False, nullable=False)
    working_hours_start = Column(Time, default=time(9, 0))
    working_hours_end = Column(Time, default=time(20, 0))
    lunch_start = Column(Time, nullable=True)
    lunch_end = Column(Time, nullable=True)
    description = Column(Text, nullable=True)
    avatar_url = Column(String(500), nullable=True)
    days_off = Column(String(200), default="Суббота,Воскресенье")
    offline_operation_id = Column(String(128), unique=True, nullable=True)
    created_at = Column(DateTime, default=now_kz)

    bookings = relationship("Booking", back_populates="instructor")
    days_off_dates = relationship("InstructorDayOff", back_populates="instructor", cascade="all, delete-orphan")
    daily_schedules = relationship("InstructorDailySchedule", back_populates="instructor", cascade="all, delete-orphan")


class Vehicle(Base):
    """A real training car that can be assigned to one active booking at a time."""
    __tablename__ = "vehicles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True)
    transmission = Column(String(50), nullable=False)
    # A car under repair remains in the fleet history but must not be offered
    # to new bookings. The server default keeps every existing car available
    # during the rolling database migration.
    is_under_repair = Column(Boolean, default=False, server_default="false", nullable=False)
    created_at = Column(DateTime, default=now_kz, nullable=False)

    bookings = relationship("Booking", back_populates="vehicle")


class InstructorDayOff(Base):
    __tablename__ = "instructor_days_off"

    id = Column(Integer, primary_key=True, autoincrement=True)
    instructor_id = Column(Integer, ForeignKey("instructors.id", ondelete="CASCADE"), nullable=False)
    day_off_date = Column(Date, nullable=False)
    created_at = Column(DateTime, default=now_kz)

    instructor = relationship("Instructor", back_populates="days_off_dates")

    __table_args__ = (UniqueConstraint("instructor_id", "day_off_date", name="uq_instructor_dayoff"),)


class InstructorDailySchedule(Base):
    __tablename__ = "instructor_daily_schedules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    instructor_id = Column(Integer, ForeignKey("instructors.id", ondelete="CASCADE"), nullable=False)
    schedule_date = Column(Date, nullable=False)
    is_day_off = Column(Boolean, default=False, nullable=False)
    working_hours_start = Column(Time, nullable=True)
    working_hours_end = Column(Time, nullable=True)
    lunch_start = Column(Time, nullable=True)
    lunch_end = Column(Time, nullable=True)
    created_at = Column(DateTime, default=now_kz)

    instructor = relationship("Instructor", back_populates="daily_schedules")

    __table_args__ = (UniqueConstraint("instructor_id", "schedule_date", name="uq_instructor_daily_schedule"),)


class InstructorRotation(Base):
    __tablename__ = "instructor_rotations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    instructor_id = Column(Integer, ForeignKey("instructors.id", ondelete="CASCADE"), nullable=False)
    last_booking_date = Column(Date, nullable=True)
    last_booking_time = Column(Time, nullable=True)
    rotation_count = Column(Integer, default=0)
    updated_at = Column(DateTime, default=now_kz, onupdate=now_kz)

    instructor = relationship("Instructor")


class Client(Base):
    __tablename__ = "clients"

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(String(50), unique=True, nullable=True)
    name = Column(String(200), nullable=False)
    phone = Column(String(30), unique=True, nullable=True)
    password_hash = Column(String(255), nullable=True)
    referral_code = Column(String(50), unique=True, nullable=True)
    referred_by_client_id = Column(Integer, ForeignKey("clients.id"), nullable=True)
    referral_discount_available = Column(Boolean, default=False, nullable=False)
    avatar_url = Column(String(500), nullable=True)
    # A deleted client remains as a hidden tombstone so events, restrictions
    # and referral history can keep their foreign-key links.
    is_deleted = Column(Boolean, default=False, server_default="false", nullable=False)
    offline_operation_id = Column(String(128), unique=True, nullable=True)
    created_at = Column(DateTime, default=now_kz)
    # Общий для Telegram и приложения лимит самостоятельных переносов.
    reschedule_count_24h = Column(Integer, default=0, nullable=False)
    reschedule_window_started_at = Column(DateTime, nullable=True)
    # Состояние Telegram-чата поддержки хранится в общей БД, чтобы оба
    # backend-сервиса и бот видели один и тот же открытый диалог.
    support_chat_opened_at = Column(DateTime, nullable=True)
    support_chat_closed_at = Column(DateTime, nullable=True)

    bookings = relationship("Booking", back_populates="client")
    referrer = relationship("Client", remote_side=[id], foreign_keys=[referred_by_client_id])


class MobileSession(Base):
    __tablename__ = "mobile_sessions"

    id = Column(String(64), primary_key=True)
    client_id = Column(Integer, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=now_kz, nullable=False)
    expires_at = Column(DateTime, nullable=False)


class GenderAnalytics(Base):
    """Cached name-based client gender estimate shared with the admin backend."""
    __tablename__ = "gender_analytics"

    id = Column(Integer, primary_key=True, default=1)
    male_count = Column(Integer, default=0, nullable=False)
    female_count = Column(Integer, default=0, nullable=False)
    unknown_count = Column(Integer, default=0, nullable=False)
    total_count = Column(Integer, default=0, nullable=False)
    model = Column(String(100), nullable=True)
    updated_at = Column(DateTime, nullable=True)


class Booking(Base):
    __tablename__ = "bookings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    instructor_id = Column(Integer, ForeignKey("instructors.id", ondelete="SET NULL"), nullable=True)
    # Legacy bookings can remain unassigned during a rolling deployment.
    # Every new booking receives a concrete compatible car.
    vehicle_id = Column(Integer, ForeignKey("vehicles.id", ondelete="SET NULL"), nullable=True)
    service_type = Column(String(50), nullable=False)
    transmission = Column(String(50), nullable=False)
    location = Column(String(200), nullable=False)
    booking_date = Column(Date, nullable=False)
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)
    status = Column(String(50), default="planned")
    booking_number = Column(String(6), unique=True, nullable=True)
    offline_operation_id = Column(String(128), unique=True, nullable=True)
    price = Column(Integer, nullable=False)
    base_price = Column(Integer, nullable=True)
    certificate_amount = Column(Integer, default=0, nullable=False)
    referral_discount_amount = Column(Integer, default=0, nullable=False)
    payment_status = Column(String(30), default="unpaid", nullable=False)
    paid_amount = Column(Integer, default=0, nullable=False)
    paid_at = Column(DateTime, nullable=True)
    source = Column(String(30), default="telegram", nullable=False)
    package_id = Column(Integer, ForeignKey("packages.id"), nullable=True)
    # A package lesson and its complimentary exam are accounted for separately.
    package_bonus_exam_used = Column(Boolean, default=False, nullable=False)
    cancellation_previous_status = Column(String(50), nullable=True)
    reschedule_previous_status = Column(String(50), nullable=True)
    requested_reschedule_date = Column(Date, nullable=True)
    requested_reschedule_start_time = Column(Time, nullable=True)
    requested_reschedule_end_time = Column(Time, nullable=True)
    reschedule_requested_at = Column(DateTime, nullable=True)
    certificate_id = Column(Integer, ForeignKey("certificates.id"), nullable=True)
    confirmation_sent = Column(Boolean, default=False)
    confirmed_by_client = Column(Boolean, default=False)
    admin_confirmed = Column(Boolean, default=False, nullable=False)
    admin_confirmed_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    archived_at = Column(DateTime, nullable=True)
    conflict_reason = Column(Text, nullable=True)
    rating_sent = Column(Boolean, default=False)
    reminder_24h_sent = Column(Boolean, default=False)
    reminder_1h_sent = Column(Boolean, default=False)
    reminder_10min_sent = Column(Boolean, default=False)
    admin_viewed = Column(Boolean, default=True)
    created_at = Column(DateTime, default=now_kz)

    client = relationship("Client", back_populates="bookings")
    instructor = relationship("Instructor", back_populates="bookings")
    vehicle = relationship("Vehicle", back_populates="bookings")
    package = relationship("Package", foreign_keys=[package_id])
    certificate = relationship("Certificate", foreign_keys=[certificate_id])


class RatingRecord(Base):
    __tablename__ = "rating_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    booking_id = Column(Integer, ForeignKey("bookings.id", ondelete="CASCADE"), nullable=False)
    instructor_id = Column(Integer, ForeignKey("instructors.id", ondelete="SET NULL"), nullable=True)
    vote = Column(String(50), nullable=False)
    created_at = Column(DateTime, default=now_kz)


class Package(Base):
    __tablename__ = "packages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    sessions_count = Column(Integer, nullable=False)
    price = Column(Integer, nullable=False)
    description = Column(Text, nullable=True)
    validity_days = Column(Integer, default=30, nullable=False)
    bonus_exam = Column(Boolean, default=False, nullable=False)
    # Code is printed on the issued package and lets the administrator select
    # the exact package instance when attaching it to a client.
    code = Column(String(24), unique=True, nullable=True)
    offline_operation_id = Column(String(128), unique=True, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=now_kz)


class ClientPackage(Base):
    __tablename__ = "client_packages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    package_id = Column(Integer, ForeignKey("packages.id"), nullable=False)
    remaining_sessions = Column(Integer, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    purchased_at = Column(DateTime, default=now_kz)
    expires_at = Column(DateTime, nullable=True)
    remaining_bonus_exams = Column(Integer, default=0, nullable=False)

    package = relationship("Package", foreign_keys=[package_id])

    __table_args__ = (UniqueConstraint("package_id", name="uq_client_packages_package_id"),)


class Certificate(Base):
    __tablename__ = "certificates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(50), unique=True, nullable=False)
    nominal = Column(Integer, nullable=False)
    remaining = Column(Integer, nullable=False)
    is_used = Column(Boolean, default=False)
    used_by_user_id = Column(Integer, ForeignKey("clients.id"), nullable=True)
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=now_kz)
    activated_by_client_id = Column(Integer, ForeignKey("clients.id"), nullable=True)
    offline_operation_id = Column(String(128), unique=True, nullable=True)


class ReferralRecord(Base):
    __tablename__ = "referral_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    referrer_client_id = Column(Integer, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    referred_client_id = Column(Integer, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    discount_applied = Column(Boolean, default=False)
    created_at = Column(DateTime, default=now_kz)


class FAQItem(Base):
    __tablename__ = "faq_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)
    sort_order = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    offline_operation_id = Column(String(128), unique=True, nullable=True)


FAQ = FAQItem


class AuditLog(Base):
    """Логи действий администратора (для вкладки Аудит в админке)"""
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    admin_username = Column(String(100), nullable=True)
    action = Column(String(200), nullable=False)
    details = Column(Text, nullable=True)
    created_at = Column(DateTime, default=now_kz)


class Event(Base):
    """События клиентов и инструкторов (для вкладки События в админке)"""
    __tablename__ = "events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String(100), nullable=False)  # "new_booking", "booking_cancelled", etc.
    source = Column(String(50), nullable=False)  # "telegram", "mobile", "instructor_bot"
    client_id = Column(Integer, ForeignKey("clients.id", ondelete="CASCADE"), nullable=True)
    instructor_id = Column(Integer, ForeignKey("instructors.id", ondelete="SET NULL"), nullable=True)
    booking_id = Column(Integer, ForeignKey("bookings.id", ondelete="SET NULL"), nullable=True)
    message = Column(Text, nullable=False)  # Описание на русском языке
    created_at = Column(DateTime, default=now_kz)


class ArchivedLog(Base):
    """Неизменяемая история дневных журналов, общая с админ-панелью."""
    __tablename__ = "archived_logs"
    __table_args__ = (
        UniqueConstraint("source_type", "source_log_id", "created_at", name="uq_archived_logs_source"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_type = Column(String(20), nullable=False)
    source_log_id = Column(Integer, nullable=False)
    admin_username = Column(String(100), nullable=True)
    action = Column(String(200), nullable=True)
    details = Column(Text, nullable=True)
    event_type = Column(String(100), nullable=True)
    event_source = Column(String(50), nullable=True)
    client_id = Column(Integer, nullable=True)
    instructor_id = Column(Integer, nullable=True)
    booking_id = Column(Integer, nullable=True)
    message = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=True)
    archived_at = Column(DateTime, default=now_kz, server_default=text("CURRENT_TIMESTAMP"), nullable=False)


class NotificationSent(Base):
    __tablename__ = "notifications_sent"

    id = Column(Integer, primary_key=True, autoincrement=True)
    instructor_id = Column(Integer, ForeignKey("instructors.id", ondelete="SET NULL"), nullable=True)
    notification_type = Column(String(50), nullable=False)
    sent_at = Column(DateTime, default=datetime.utcnow)

    instructor_rel = relationship("Instructor", foreign_keys=[instructor_id])



class MobileUser(Base):
    __tablename__ = "mobile_users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    phone = Column(String(30), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    referral_code = Column(String(50), unique=True, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class MobileBooking(Base):
    __tablename__ = "mobile_bookings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("mobile_users.id", ondelete="CASCADE"), nullable=False)
    instructor_id = Column(Integer, ForeignKey("instructors.id", ondelete="SET NULL"), nullable=True)
    booking_date = Column(Date, nullable=False)
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=True)
    service_type = Column(String(50), nullable=False)
    transmission = Column(String(50), default="both")
    location = Column(String(200), nullable=False)
    status = Column(String(50), default="planned")
    price = Column(Float, nullable=False)
    rating_vote = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class SupportMessage(Base):
    __tablename__ = "support_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("mobile_users.id", ondelete="CASCADE"), nullable=True)
    client_id = Column(Integer, ForeignKey("clients.id", ondelete="CASCADE"), nullable=True)
    instructor_id = Column(Integer, ForeignKey("instructors.id", ondelete="SET NULL"), nullable=True)
    channel = Column(String(30), default="client", nullable=False)
    sender = Column(String(20), nullable=False)
    text = Column(Text, nullable=False)
    is_read = Column(Boolean, default=False)
    is_admin_read = Column(Boolean, default=False)
    offline_operation_id = Column(String(128), unique=True, nullable=True)
    created_at = Column(DateTime, default=now_kz)


class MobileUserPackage(Base):
    __tablename__ = "mobile_user_packages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("mobile_users.id", ondelete="CASCADE"), nullable=False)
    package_id = Column(Integer, ForeignKey("packages.id"), nullable=False)
    remaining_sessions = Column(Integer, nullable=False)
    is_active = Column(Boolean, default=True)
    purchased_at = Column(DateTime, default=datetime.utcnow)

    package = relationship("Package", foreign_keys=[package_id])


class MobileAppReview(Base):
    __tablename__ = "mobile_app_reviews"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("mobile_users.id", ondelete="CASCADE"), nullable=True)
    client_id = Column(Integer, ForeignKey("clients.id", ondelete="CASCADE"), nullable=True)
    stars = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class WaitingListEntry(Base):
    __tablename__ = "waiting_list"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    phone = Column(String(30), nullable=True)
    desired_date = Column(Date, nullable=True)
    desired_time_start = Column(Time, nullable=True)
    desired_time_end = Column(Time, nullable=True)
    transmission = Column(String(50), nullable=True)
    instructor_id = Column(Integer, ForeignKey("instructors.id", ondelete="SET NULL"), nullable=True)
    instructor_gender = Column(String(50), nullable=True)
    status = Column(String(30), default="waiting", nullable=False)
    notes = Column(Text, nullable=True)
    offline_operation_id = Column(String(128), unique=True, nullable=True)
    created_at = Column(DateTime, default=now_kz)

    instructor = relationship("Instructor", foreign_keys=[instructor_id])


class ClientBlock(Base):
    __tablename__ = "client_blocks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    blocked_until = Column(DateTime, nullable=False)
    reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=now_kz)

    client = relationship("Client", foreign_keys=[client_id])


class CertificateRequest(Base):
    __tablename__ = "certificate_requests"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    code_entered = Column(String(50), nullable=False)
    matched_certificate_id = Column(Integer, ForeignKey("certificates.id"), nullable=True)
    booking_id = Column(Integer, ForeignKey("bookings.id", ondelete="SET NULL"), nullable=True)
    status = Column(String(30), default="pending", nullable=False)
    created_at = Column(DateTime, default=now_kz)

    client = relationship("Client", foreign_keys=[client_id])
    certificate = relationship("Certificate", foreign_keys=[matched_certificate_id])
