import enum
from datetime import datetime, date, time

from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime, Date, Time,
    ForeignKey, Enum, Text, UniqueConstraint
)
from sqlalchemy.orm import relationship

from app.database import Base


class BookingStatus(str, enum.Enum):
    PLANNED = "planned"
    CONFIRMED = "confirmed"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"


class ServiceType(str, enum.Enum):
    TRAINING = "training"
    EXAM = "exam"


class TransmissionType(str, enum.Enum):
    MANUAL = "manual"
    AUTOMATIC = "automatic"
    BOTH = "both"


class RatingVote(str, enum.Enum):
    GOOD = "good"
    NORMAL = "normal"
    BAD = "bad"


class Admin(Base):
    __tablename__ = "admins"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(100), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class Instructor(Base):
    __tablename__ = "instructors"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    telegram_id = Column(String(50), nullable=True)
    telegram_username = Column(String(100), nullable=True)
    transmission = Column(Enum(TransmissionType), nullable=False, default=TransmissionType.BOTH)
    experience_years = Column(Integer, default=0)
    rating = Column(Float, default=5.0)
    is_active = Column(Boolean, default=True)
    working_hours_start = Column(Time, default=time(9, 0))
    working_hours_end = Column(Time, default=time(19, 0))
    lunch_start = Column(Time, nullable=True)
    lunch_end = Column(Time, nullable=True)
    description = Column(Text, nullable=True)
    days_off = Column(String(200), default="Суббота,Воскресенье")
    avatar_url = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    bookings = relationship("Booking", back_populates="instructor")


class Client(Base):
    __tablename__ = "clients"

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(String(50), unique=True, nullable=False)
    name = Column(String(200), nullable=False)
    phone = Column(String(30), nullable=True)
    referral_code = Column(String(50), unique=True, nullable=True)
    referred_by_client_id = Column(Integer, ForeignKey("clients.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    bookings = relationship("Booking", back_populates="client")
    referrer = relationship("Client", remote_side=[id], foreign_keys=[referred_by_client_id])


class Booking(Base):
    __tablename__ = "bookings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    instructor_id = Column(Integer, ForeignKey("instructors.id"), nullable=False)
    service_type = Column(Enum(ServiceType), nullable=False)
    transmission = Column(Enum(TransmissionType), nullable=False)
    location = Column(String(200), nullable=False)
    booking_date = Column(Date, nullable=False)
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)
    status = Column(Enum(BookingStatus), default=BookingStatus.PLANNED)
    price = Column(Integer, nullable=False)
    package_id = Column(Integer, ForeignKey("packages.id"), nullable=True)
    certificate_id = Column(Integer, ForeignKey("certificates.id"), nullable=True)
    confirmation_sent = Column(Boolean, default=False)
    confirmed_by_client = Column(Boolean, default=False)
    rating_sent = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    client = relationship("Client", back_populates="bookings")
    instructor = relationship("Instructor", back_populates="bookings")
    package = relationship("Package", foreign_keys=[package_id])
    certificate = relationship("Certificate", foreign_keys=[certificate_id])


class RatingRecord(Base):
    __tablename__ = "rating_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    booking_id = Column(Integer, ForeignKey("bookings.id"), nullable=False)
    instructor_id = Column(Integer, ForeignKey("instructors.id"), nullable=False)
    vote = Column(Enum(RatingVote), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class Package(Base):
    __tablename__ = "packages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    sessions_count = Column(Integer, nullable=False)
    price = Column(Integer, nullable=False)
    is_active = Column(Boolean, default=True)


class ClientPackage(Base):
    __tablename__ = "client_packages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    package_id = Column(Integer, ForeignKey("packages.id"), nullable=False)
    remaining_sessions = Column(Integer, nullable=False)
    purchased_at = Column(DateTime, default=datetime.utcnow)


class Certificate(Base):
    __tablename__ = "certificates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(50), unique=True, nullable=False)
    nominal = Column(Integer, nullable=False)
    remaining = Column(Integer, nullable=False)
    is_used = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    activated_by_client_id = Column(Integer, ForeignKey("clients.id"), nullable=True)


class ReferralRecord(Base):
    __tablename__ = "referral_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    referrer_client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    referred_client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    discount_applied = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class FAQItem(Base):
    __tablename__ = "faq_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)
    sort_order = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    admin_username = Column(String(100), nullable=True)
    action = Column(String(200), nullable=False)
    details = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class NotificationSent(Base):
    __tablename__ = "notifications_sent"

    id = Column(Integer, primary_key=True, autoincrement=True)
    instructor_id = Column(Integer, ForeignKey("instructors.id"), nullable=False)
    notification_type = Column(String(50), nullable=False)
    sent_at = Column(DateTime, default=datetime.utcnow)

    instructor_rel = relationship("Instructor", foreign_keys=[instructor_id])


# ── Mobile app models (shared DB) ──────────────────────────────────────

class MobileUser(Base):
    __tablename__ = "mobile_users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    phone = Column(String(30), nullable=False)
    email = Column(String(200), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    fcm_token = Column(String(500), nullable=True)
    referral_code = Column(String(50), unique=True, nullable=True)
    referred_by_id = Column(Integer, ForeignKey("mobile_users.id"), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    support_messages = relationship("SupportMessage", back_populates="user",
                                    foreign_keys="SupportMessage.user_id")


class MobileBooking(Base):
    __tablename__ = "mobile_bookings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("mobile_users.id"), nullable=False)
    instructor_id = Column(Integer, ForeignKey("instructors.id"), nullable=False)
    service_type = Column(Enum(ServiceType), nullable=False)
    transmission = Column(Enum(TransmissionType), nullable=False)
    location = Column(String(200), nullable=False)
    booking_date = Column(Date, nullable=False)
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)
    status = Column(Enum(BookingStatus), default=BookingStatus.PLANNED)
    price = Column(Integer, nullable=False)
    package_usage_id = Column(Integer, nullable=True)
    certificate_id = Column(Integer, nullable=True)
    rating_vote = Column(Enum(RatingVote), nullable=True)
    rating_sent = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    instructor = relationship("Instructor")


class SupportMessage(Base):
    __tablename__ = "support_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("mobile_users.id"), nullable=False)
    sender = Column(String(10), nullable=False)
    text = Column(Text, nullable=False)
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("MobileUser", back_populates="support_messages",
                        foreign_keys=[user_id])


class AppReview(Base):
    """Оценки приложения от мобильных пользователей."""
    __tablename__ = "app_reviews"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("mobile_users.id"), nullable=True)
    stars = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("MobileUser", foreign_keys=[user_id])
