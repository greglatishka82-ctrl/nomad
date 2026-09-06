import logging
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

logger = logging.getLogger(__name__)

db_url = settings.DATABASE_URL
if db_url.startswith("postgresql://"):
    db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = create_async_engine(db_url, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
_schema_verified = False


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with async_session() as session:
        yield session


def _ensure_pg_enums(conn):
    """Create PostgreSQL ENUM types if they don't exist yet.

    Must run before create_all so the enum types are available.
    Uses DO $$ ... IF NOT EXISTS so a pre-existing type does not abort the transaction.
    """
    is_pg = conn.dialect.name == "postgresql"
    if not is_pg:
        return

    enums = {
        "transmissiontype": ("manual", "automatic", "both"),
        "instructorgender": ("male", "female", "any"),
        "bookingstatus": ("planned", "confirmed", "completed", "cancelled", "no_show"),
        "servicetype": ("training", "exam"),
        "ratingvote": ("good", "normal", "bad"),
    }
    for type_name, values in enums.items():
        vals = ", ".join(f"'{v}'" for v in values)
        conn.exec_driver_sql(
            f"DO $$ BEGIN "
            f"  CREATE TYPE {type_name} AS ENUM ({vals}); "
            f"EXCEPTION WHEN duplicate_object THEN NULL; "
            f"END $$;"
        )
        logger.info("Ensured PG enum type: %s", type_name)


def _add_missing_columns(conn):
    """DB-agnostic migration: add columns declared in models but missing in DB.

    Работает и на SQLite, и на PostgreSQL. Падение одной колонки не должно
    ломать старт приложения, поэтому каждая операция обёрнута в try/except.
    """
    from sqlalchemy import inspect as sa_inspect, Enum as SAEnum

    is_pg = conn.dialect.name == "postgresql"
    inspector = sa_inspect(conn)
    for table_name, table in Base.metadata.tables.items():
        if not inspector.has_table(table_name):
            continue
        existing = {c["name"] for c in inspector.get_columns(table_name)}
        for col in table.columns:
            if col.name in existing:
                continue
            # For PostgreSQL ENUM columns, reference the type by name directly
            if is_pg and isinstance(col.type, SAEnum) and col.type.name:
                col_type_str = col.type.name
            else:
                col_type_str = col.type.compile(dialect=conn.dialect)

            nullable = "NULL" if col.nullable else "NOT NULL"
            default_clause = ""
            if col.server_default is not None:
                default_clause = f" DEFAULT {col.server_default.arg}"
            elif col.default is not None and hasattr(col.default, "arg"):
                arg = col.default.arg
                if not callable(arg):
                    default_clause = f" DEFAULT '{arg}'"

            conn.exec_driver_sql(
                f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS "
                f"{col.name} {col_type_str}{default_clause} {nullable}"
            )
            logger.info("Migration: added column %s.%s", table_name, col.name)


async def _clean_duplicate_mobile_users():
    """Удаляет дубликаты mobile_users (оставляет запись с минимальным id)."""
    from sqlalchemy import text as _text
    async with engine.begin() as conn:
            # Сначала удаляем связанные записи дубликатов из support_messages
            await conn.execute(_text("""
                DELETE FROM support_messages
                WHERE user_id IN (
                    SELECT a.id FROM mobile_users a
                    INNER JOIN mobile_users b ON a.phone = b.phone AND a.id > b.id
                );
            """))
            # Удаляем дубликаты по телефону
            await conn.execute(_text("""
                DELETE FROM mobile_users a
                USING mobile_users b
                WHERE a.id > b.id AND a.phone = b.phone;
            """))
    logger.info("Cleaned duplicate mobile_users")


async def _sync_mobile_users_to_clients():
    """Copy legacy mobile users into the unified clients table."""
    from sqlalchemy import text as _text
    async with engine.begin() as conn:
            await conn.execute(_text("""
                INSERT INTO clients (name, phone, password_hash, referral_code, created_at, referral_discount_available)
                SELECT mu.name, mu.phone, mu.password_hash, mu.referral_code, mu.created_at, FALSE
                FROM mobile_users mu
                WHERE NOT EXISTS (
                    SELECT 1 FROM clients c
                    WHERE c.phone = mu.phone
                );
            """))
            await conn.execute(_text("""
                UPDATE support_messages sm
                SET client_id = c.id
                FROM mobile_users mu
                JOIN clients c ON c.phone = mu.phone
                WHERE sm.user_id = mu.id AND sm.client_id IS NULL;
            """))
    logger.info("Synced legacy mobile_users to clients")


async def _seed_default_fleet() -> None:
    """Provide the same six-car baseline in local SQLite as PostgreSQL migration."""
    from sqlalchemy import select
    from app.models.models import Vehicle

    async with async_session() as db:
        if (await db.execute(select(Vehicle.id).limit(1))).scalar_one_or_none() is not None:
            return
        db.add_all([
            Vehicle(name="Машина 1", transmission="manual"),
            *[Vehicle(name=f"Машина {number}", transmission="automatic") for number in range(2, 7)],
        ])
        await db.commit()


def _verify_model_schema(conn):
    from sqlalchemy import inspect as sa_inspect

    inspector = sa_inspect(conn)
    missing = []
    for table_name, table in Base.metadata.tables.items():
        if not inspector.has_table(table_name):
            missing.append(f"table {table_name}")
            continue
        actual = {column["name"] for column in inspector.get_columns(table_name)}
        missing.extend(f"column {table_name}.{column.name}" for column in table.columns if column.name not in actual)
    if missing:
        raise RuntimeError("Database schema is incomplete: " + ", ".join(missing))


async def verify_database_ready():
    global _schema_verified
    from sqlalchemy import text

    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
        if _schema_verified:
            return
        await conn.run_sync(_verify_model_schema)
        if engine.dialect.name == "postgresql":
            definition = (await conn.execute(text("""
                SELECT pg_get_constraintdef(oid)
                FROM pg_constraint
                WHERE conrelid = 'bookings'::regclass
                  AND conname = 'bookings_no_instructor_time_overlap'
            """))).scalar_one_or_none()
            required = ("pending", "reschedule_pending", "cancellation_pending", "planned", "confirmed", "in_progress")
            if not definition or any(status not in definition for status in required):
                raise RuntimeError("Booking overlap protection is missing or incomplete")
            vehicle_definition = (await conn.execute(text("""
                SELECT pg_get_constraintdef(oid)
                FROM pg_constraint
                WHERE conrelid = 'bookings'::regclass
                  AND conname = 'bookings_no_vehicle_time_overlap'
            """))).scalar_one_or_none()
            if not vehicle_definition or "vehicle_id WITH =" not in vehicle_definition:
                raise RuntimeError("Vehicle overlap protection is missing")
            index_names = set((await conn.execute(text("""
                SELECT indexname FROM pg_indexes WHERE schemaname = current_schema()
            """))).scalars().all())
            required_indexes = {
                "uq_instructors_offline_operation_id", "uq_clients_offline_operation_id",
                "uq_bookings_offline_operation_id", "uq_packages_offline_operation_id",
                "uq_certificates_offline_operation_id", "uq_faq_items_offline_operation_id",
                "uq_waiting_list_offline_operation_id", "uq_support_messages_offline_operation_id",
                "uq_client_packages_package_id", "uq_vehicles_name", "ix_bookings_vehicle_slot",
            }
            if missing_indexes := required_indexes - index_names:
                raise RuntimeError("Database indexes are incomplete: " + ", ".join(sorted(missing_indexes)))
        _schema_verified = True


async def _run_explicit_migrations():
    """Run explicit SQL migrations for schema changes that ORM can miss.
    Each statement is idempotent (IF NOT EXISTS / EXCEPTION WHEN ...).
    """
    from sqlalchemy import text
    migrations = [
        # Instructor migrations
        "ALTER TABLE instructors ADD COLUMN IF NOT EXISTS gender VARCHAR(50) NOT NULL DEFAULT 'any';",
        "ALTER TABLE instructors ADD COLUMN IF NOT EXISTS is_lead BOOLEAN NOT NULL DEFAULT FALSE;",
        "ALTER TABLE instructors ADD COLUMN IF NOT EXISTS lesson_type VARCHAR(50) NOT NULL DEFAULT 'both';",
        "ALTER TABLE instructors ALTER COLUMN gender TYPE VARCHAR(50) USING gender::VARCHAR;",
        "ALTER TABLE instructors ALTER COLUMN transmission TYPE VARCHAR(50) USING transmission::VARCHAR;",

        # Fleet: six real cars replace the former global capacity-only rule.
        # This seed runs only once and preserves the established six-car
        # capacity while making the single manual car explicit.
        "CREATE TABLE IF NOT EXISTS vehicles (id SERIAL PRIMARY KEY, name VARCHAR(100) NOT NULL, transmission VARCHAR(50) NOT NULL, is_under_repair BOOLEAN NOT NULL DEFAULT FALSE, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP);",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_vehicles_name ON vehicles (name);",
        "ALTER TABLE vehicles ADD COLUMN IF NOT EXISTS is_under_repair BOOLEAN NOT NULL DEFAULT FALSE;",
        "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS vehicle_id INTEGER;",
        "CREATE INDEX IF NOT EXISTS ix_bookings_vehicle_slot ON bookings (vehicle_id, booking_date, start_time, end_time);",
        "INSERT INTO vehicles (name, transmission, created_at) SELECT fleet.name, fleet.transmission, NOW() FROM (VALUES ('Машина 1', 'manual'), ('Машина 2', 'automatic'), ('Машина 3', 'automatic'), ('Машина 4', 'automatic'), ('Машина 5', 'automatic'), ('Машина 6', 'automatic')) AS fleet(name, transmission) WHERE NOT EXISTS (SELECT 1 FROM vehicles);",
        
        # Booking migrations
        "ALTER TABLE clients ALTER COLUMN telegram_id DROP NOT NULL;",
        "ALTER TABLE clients DROP COLUMN IF EXISTS email;",
        "ALTER TABLE mobile_users DROP COLUMN IF EXISTS email;",
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS password_hash VARCHAR(255);",
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN NOT NULL DEFAULT FALSE;",
        "ALTER TABLE instructors ADD COLUMN IF NOT EXISTS offline_operation_id VARCHAR(128);",
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS offline_operation_id VARCHAR(128);",
        "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS offline_operation_id VARCHAR(128);",
        "ALTER TABLE packages ADD COLUMN IF NOT EXISTS offline_operation_id VARCHAR(128);",
        "ALTER TABLE certificates ADD COLUMN IF NOT EXISTS offline_operation_id VARCHAR(128);",
        "ALTER TABLE faq_items ADD COLUMN IF NOT EXISTS offline_operation_id VARCHAR(128);",
        "ALTER TABLE waiting_list ADD COLUMN IF NOT EXISTS offline_operation_id VARCHAR(128);",
        "ALTER TABLE support_messages ADD COLUMN IF NOT EXISTS offline_operation_id VARCHAR(128);",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_instructors_offline_operation_id ON instructors (offline_operation_id) WHERE offline_operation_id IS NOT NULL;",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_clients_offline_operation_id ON clients (offline_operation_id) WHERE offline_operation_id IS NOT NULL;",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_bookings_offline_operation_id ON bookings (offline_operation_id) WHERE offline_operation_id IS NOT NULL;",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_packages_offline_operation_id ON packages (offline_operation_id) WHERE offline_operation_id IS NOT NULL;",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_certificates_offline_operation_id ON certificates (offline_operation_id) WHERE offline_operation_id IS NOT NULL;",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_faq_items_offline_operation_id ON faq_items (offline_operation_id) WHERE offline_operation_id IS NOT NULL;",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_waiting_list_offline_operation_id ON waiting_list (offline_operation_id) WHERE offline_operation_id IS NOT NULL;",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_support_messages_offline_operation_id ON support_messages (offline_operation_id) WHERE offline_operation_id IS NOT NULL;",
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS referral_discount_available BOOLEAN NOT NULL DEFAULT FALSE;",
        "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS base_price INTEGER;",
        "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS certificate_amount INTEGER NOT NULL DEFAULT 0;",
        "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS referral_discount_amount INTEGER NOT NULL DEFAULT 0;",
        "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS payment_status VARCHAR(30) NOT NULL DEFAULT 'unpaid';",
        "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS paid_amount INTEGER NOT NULL DEFAULT 0;",
        "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS paid_at TIMESTAMP;",
        "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS source VARCHAR(30) NOT NULL DEFAULT 'telegram';",
        # Kept in both backend services because they share one database and
        # either service may be deployed first.
        "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS booking_number VARCHAR(6);",
        "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS admin_confirmed BOOLEAN NOT NULL DEFAULT FALSE;",
        "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS admin_confirmed_at TIMESTAMP;",
        "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS completed_at TIMESTAMP;",
        "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS archived_at TIMESTAMP;",
        "UPDATE bookings SET completed_at = COALESCE(paid_at, created_at) WHERE status = 'completed' AND completed_at IS NULL;",
        "CREATE INDEX IF NOT EXISTS ix_bookings_status_completed_at ON bookings (status, completed_at);",
        "CREATE INDEX IF NOT EXISTS ix_bookings_status_archived_at ON bookings (status, archived_at, booking_date);",
        "CREATE INDEX IF NOT EXISTS ix_bookings_status_archived_created ON bookings (status, archived_at, created_at);",
        "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS conflict_reason TEXT;",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_bookings_booking_number ON bookings (booking_number) WHERE booking_number IS NOT NULL;",
        "ALTER TABLE client_packages ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;",
        "ALTER TABLE packages ADD COLUMN IF NOT EXISTS validity_days INTEGER NOT NULL DEFAULT 30;",
        "ALTER TABLE packages ADD COLUMN IF NOT EXISTS bonus_exam BOOLEAN NOT NULL DEFAULT FALSE;",
        "ALTER TABLE packages ADD COLUMN IF NOT EXISTS description TEXT;",
        "ALTER TABLE client_packages ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP;",
        "ALTER TABLE client_packages ADD COLUMN IF NOT EXISTS remaining_bonus_exams INTEGER NOT NULL DEFAULT 0;",
        "ALTER TABLE packages ADD COLUMN IF NOT EXISTS code VARCHAR(24);",
        "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS package_bonus_exam_used BOOLEAN NOT NULL DEFAULT FALSE;",
        "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS cancellation_previous_status VARCHAR(50);",
        "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS reschedule_previous_status VARCHAR(50);",
        "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS requested_reschedule_date DATE;",
        "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS requested_reschedule_start_time TIME;",
        "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS requested_reschedule_end_time TIME;",
        "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS reschedule_requested_at TIMESTAMP;",
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS reschedule_count_24h INTEGER NOT NULL DEFAULT 0;",
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS reschedule_window_started_at TIMESTAMP;",
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS support_chat_opened_at TIMESTAMP;",
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS support_chat_closed_at TIMESTAMP;",
        "ALTER TABLE certificate_requests ADD COLUMN IF NOT EXISTS booking_id INTEGER;",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_packages_code ON packages (code) WHERE code IS NOT NULL;",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_client_packages_package_id ON client_packages (package_id);",
        "UPDATE client_packages cp SET remaining_bonus_exams = CASE WHEN p.bonus_exam THEN 1 ELSE 0 END FROM packages p WHERE cp.package_id = p.id AND cp.remaining_bonus_exams = 0;",
        "UPDATE client_packages SET expires_at = purchased_at + INTERVAL '30 days' WHERE expires_at IS NULL;",
        "UPDATE client_packages SET is_active = FALSE WHERE expires_at IS NOT NULL AND expires_at < NOW();",
        "ALTER TABLE certificates ADD COLUMN IF NOT EXISTS used_at TIMESTAMP;",
        "ALTER TABLE certificates ADD COLUMN IF NOT EXISTS used_by_user_id INTEGER;",
        "ALTER TABLE support_messages ADD COLUMN IF NOT EXISTS client_id INTEGER;",
        "ALTER TABLE support_messages ADD COLUMN IF NOT EXISTS instructor_id INTEGER;",
        "ALTER TABLE support_messages ADD COLUMN IF NOT EXISTS channel VARCHAR(30) NOT NULL DEFAULT 'client';",
        "ALTER TABLE support_messages ADD COLUMN IF NOT EXISTS is_admin_read BOOLEAN NOT NULL DEFAULT FALSE;",
        "ALTER TABLE mobile_app_reviews ADD COLUMN IF NOT EXISTS client_id INTEGER;",
        "ALTER TABLE mobile_app_reviews ALTER COLUMN user_id DROP NOT NULL;",
        "ALTER TABLE bookings ALTER COLUMN service_type TYPE VARCHAR(50) USING service_type::VARCHAR;",
        "ALTER TABLE bookings ALTER COLUMN transmission TYPE VARCHAR(50) USING transmission::VARCHAR;",
        "ALTER TABLE bookings ALTER COLUMN status TYPE VARCHAR(50) USING status::VARCHAR;",

        # Last-line protection against double-booking.  All live booking
        # channels use the unified `bookings` table; this also blocks races
        # between simultaneous requests and direct imports into that table.
        "CREATE EXTENSION IF NOT EXISTS btree_gist;",
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = 'bookings'::regclass
                  AND conname = 'bookings_no_instructor_time_overlap'
                  AND (
                      pg_get_constraintdef(oid) NOT LIKE '%''pending''%'
                      OR pg_get_constraintdef(oid) NOT LIKE '%''reschedule_pending''%'
                      OR pg_get_constraintdef(oid) NOT LIKE '%''cancellation_pending''%'
                  )
            ) THEN
                ALTER TABLE bookings DROP CONSTRAINT bookings_no_instructor_time_overlap;
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = 'bookings'::regclass
                  AND conname = 'bookings_no_instructor_time_overlap'
            ) THEN
                ALTER TABLE bookings
                ADD CONSTRAINT bookings_no_instructor_time_overlap
                EXCLUDE USING gist (
                    instructor_id WITH =,
                    booking_date WITH =,
                    tsrange((booking_date + start_time), (booking_date + end_time), '[)') WITH &&
                )
                WHERE (status IN ('pending', 'reschedule_pending', 'cancellation_pending', 'planned', 'confirmed', 'in_progress'));
            END IF;
        END $$;
        """,

        # A second database-level exclusion protects a concrete car from
        # concurrent booking requests. NULL keeps historical rows deploy-safe.
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = 'bookings'::regclass
                  AND conname = 'bookings_no_vehicle_time_overlap'
            ) THEN
                ALTER TABLE bookings
                ADD CONSTRAINT bookings_no_vehicle_time_overlap
                EXCLUDE USING gist (
                    vehicle_id WITH =,
                    booking_date WITH =,
                    tsrange((booking_date + start_time), (booking_date + end_time), '[)') WITH &&
                )
                WHERE (vehicle_id IS NOT NULL AND status IN ('pending', 'reschedule_pending', 'cancellation_pending', 'planned', 'confirmed', 'in_progress'));
            END IF;
        END $$;
        """,
        "ALTER TABLE bookings DROP CONSTRAINT IF EXISTS bookings_vehicle_id_fkey;",
        "ALTER TABLE bookings ADD CONSTRAINT bookings_vehicle_id_fkey FOREIGN KEY (vehicle_id) REFERENCES vehicles(id) ON DELETE SET NULL;",

        # RatingRecord migrations
        "ALTER TABLE rating_records ALTER COLUMN vote TYPE VARCHAR(50) USING vote::VARCHAR;",
        
        # Legacy/imported NULL means the flag was not stored.  Explicitly
        # inactive instructors must remain inactive after a service restart.
        "UPDATE instructors SET is_active = TRUE WHERE is_active IS NULL;",
        
        # MobileBooking migrations (if table exists, handled by the loop)
        "ALTER TABLE mobile_bookings ALTER COLUMN service_type TYPE VARCHAR(50) USING service_type::VARCHAR;",
        "ALTER TABLE mobile_bookings ALTER COLUMN transmission TYPE VARCHAR(50) USING transmission::VARCHAR;",
        "ALTER TABLE mobile_bookings ALTER COLUMN status TYPE VARCHAR(50) USING status::VARCHAR;",
        "ALTER TABLE mobile_bookings ALTER COLUMN rating_vote TYPE VARCHAR(50) USING rating_vote::VARCHAR;",

        # Historical records survive instructor deletion.  A completed or
        # cancelled lesson must no longer retain the instructor as a live
        # entity, so its foreign key is cleared rather than cascaded.
        "ALTER TABLE bookings ALTER COLUMN instructor_id DROP NOT NULL;",
        "ALTER TABLE bookings DROP CONSTRAINT IF EXISTS bookings_instructor_id_fkey;",
        "ALTER TABLE bookings ADD CONSTRAINT bookings_instructor_id_fkey FOREIGN KEY (instructor_id) REFERENCES instructors(id) ON DELETE SET NULL;",
        
        # bookings -> clients
        "ALTER TABLE bookings DROP CONSTRAINT IF EXISTS bookings_client_id_fkey;",
        "ALTER TABLE bookings ADD CONSTRAINT bookings_client_id_fkey FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE CASCADE;",
        
        # Ratings and other instructor history are retained as independent
        # records after the instructor is deleted.
        "ALTER TABLE rating_records ALTER COLUMN instructor_id DROP NOT NULL;",
        "ALTER TABLE rating_records DROP CONSTRAINT IF EXISTS rating_records_instructor_id_fkey;",
        "ALTER TABLE rating_records ADD CONSTRAINT rating_records_instructor_id_fkey FOREIGN KEY (instructor_id) REFERENCES instructors(id) ON DELETE SET NULL;",
        
        # rating_records -> bookings
        "ALTER TABLE rating_records DROP CONSTRAINT IF EXISTS rating_records_booking_id_fkey;",
        "ALTER TABLE rating_records ADD CONSTRAINT rating_records_booking_id_fkey FOREIGN KEY (booking_id) REFERENCES bookings(id) ON DELETE CASCADE;",

        # A rolling booking-history cleanup must not remove a client or fail
        # because historical event/certificate-request rows still refer to it.
        "ALTER TABLE events DROP CONSTRAINT IF EXISTS events_booking_id_fkey;",
        "ALTER TABLE events ADD CONSTRAINT events_booking_id_fkey FOREIGN KEY (booking_id) REFERENCES bookings(id) ON DELETE SET NULL;",
        "ALTER TABLE certificate_requests DROP CONSTRAINT IF EXISTS certificate_requests_booking_id_fkey;",
        "ALTER TABLE certificate_requests ADD CONSTRAINT certificate_requests_booking_id_fkey FOREIGN KEY (booking_id) REFERENCES bookings(id) ON DELETE SET NULL;",

        "ALTER TABLE events DROP CONSTRAINT IF EXISTS events_instructor_id_fkey;",
        "ALTER TABLE events ADD CONSTRAINT events_instructor_id_fkey FOREIGN KEY (instructor_id) REFERENCES instructors(id) ON DELETE SET NULL;",

        # notifications_sent -> instructors
        "ALTER TABLE notifications_sent ALTER COLUMN instructor_id DROP NOT NULL;",
        "ALTER TABLE notifications_sent DROP CONSTRAINT IF EXISTS notifications_sent_instructor_id_fkey;",
        "ALTER TABLE notifications_sent ADD CONSTRAINT notifications_sent_instructor_id_fkey FOREIGN KEY (instructor_id) REFERENCES instructors(id) ON DELETE SET NULL;",

        # mobile_bookings -> instructors
        "ALTER TABLE mobile_bookings ALTER COLUMN instructor_id DROP NOT NULL;",
        "ALTER TABLE mobile_bookings DROP CONSTRAINT IF EXISTS mobile_bookings_instructor_id_fkey;",
        "ALTER TABLE mobile_bookings ADD CONSTRAINT mobile_bookings_instructor_id_fkey FOREIGN KEY (instructor_id) REFERENCES instructors(id) ON DELETE SET NULL;",

        "ALTER TABLE support_messages DROP CONSTRAINT IF EXISTS support_messages_instructor_id_fkey;",
        "ALTER TABLE support_messages ADD CONSTRAINT support_messages_instructor_id_fkey FOREIGN KEY (instructor_id) REFERENCES instructors(id) ON DELETE SET NULL;",
        "ALTER TABLE waiting_list DROP CONSTRAINT IF EXISTS waiting_list_instructor_id_fkey;",
        "ALTER TABLE waiting_list ADD CONSTRAINT waiting_list_instructor_id_fkey FOREIGN KEY (instructor_id) REFERENCES instructors(id) ON DELETE SET NULL;",

        # Reminder flags on bookings
        "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS reminder_24h_sent BOOLEAN NOT NULL DEFAULT FALSE;",
        "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS reminder_1h_sent BOOLEAN NOT NULL DEFAULT FALSE;",
        "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS reminder_10min_sent BOOLEAN NOT NULL DEFAULT FALSE;",

        # Instructor: дежурный и привязка к площадке
        "ALTER TABLE instructors ADD COLUMN IF NOT EXISTS is_duty BOOLEAN NOT NULL DEFAULT FALSE;",
        "ALTER TABLE instructors ADD COLUMN IF NOT EXISTS preferred_location VARCHAR(200);",
        "ALTER TABLE instructors ADD COLUMN IF NOT EXISTS avatar_url VARCHAR(500);",
        
        # Clients: аватарка
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS avatar_url VARCHAR(500);",

        # Нормализация телефонов: приводим все номера к формату +7XXXXXXXXXX
        """
        DO $$
        BEGIN
            -- Нормализация в clients
            UPDATE clients SET phone = '+' || 
                CASE 
                    WHEN phone ~ '^[0-9]' THEN 
                        CASE 
                            WHEN length(regexp_replace(phone, '[^0-9]', '', 'g')) = 10 THEN '7' || regexp_replace(phone, '[^0-9]', '', 'g')
                            WHEN length(regexp_replace(phone, '[^0-9]', '', 'g')) = 11 AND regexp_replace(phone, '[^0-9]', '', 'g') LIKE '8%' THEN '7' || substring(regexp_replace(phone, '[^0-9]', '', 'g') from 2)
                            WHEN length(regexp_replace(phone, '[^0-9]', '', 'g')) = 11 THEN regexp_replace(phone, '[^0-9]', '', 'g')
                            ELSE phone
                        END
                    ELSE phone
                END
            WHERE phone IS NOT NULL AND phone != '' AND phone NOT LIKE '+%';
            
            -- Нормализация в instructors
            UPDATE instructors SET phone = '+' || 
                CASE 
                    WHEN phone ~ '^[0-9]' THEN 
                        CASE 
                            WHEN length(regexp_replace(phone, '[^0-9]', '', 'g')) = 10 THEN '7' || regexp_replace(phone, '[^0-9]', '', 'g')
                            WHEN length(regexp_replace(phone, '[^0-9]', '', 'g')) = 11 AND regexp_replace(phone, '[^0-9]', '', 'g') LIKE '8%' THEN '7' || substring(regexp_replace(phone, '[^0-9]', '', 'g') from 2)
                            WHEN length(regexp_replace(phone, '[^0-9]', '', 'g')) = 11 THEN regexp_replace(phone, '[^0-9]', '', 'g')
                            ELSE phone
                        END
                    ELSE phone
                END
            WHERE phone IS NOT NULL AND phone != '' AND phone NOT LIKE '+%';
            
            -- Нормализация в waiting_list
            UPDATE waiting_list SET phone = '+' || 
                CASE 
                    WHEN phone ~ '^[0-9]' THEN 
                        CASE 
                            WHEN length(regexp_replace(phone, '[^0-9]', '', 'g')) = 10 THEN '7' || regexp_replace(phone, '[^0-9]', '', 'g')
                            WHEN length(regexp_replace(phone, '[^0-9]', '', 'g')) = 11 AND regexp_replace(phone, '[^0-9]', '', 'g') LIKE '8%' THEN '7' || substring(regexp_replace(phone, '[^0-9]', '', 'g') from 2)
                            WHEN length(regexp_replace(phone, '[^0-9]', '', 'g')) = 11 THEN regexp_replace(phone, '[^0-9]', '', 'g')
                            ELSE phone
                        END
                    ELSE phone
                END
            WHERE phone IS NOT NULL AND phone != '' AND phone NOT LIKE '+%';
        END $$;
        """,
    ]
    async with engine.begin() as conn:
        await conn.execute(text("SELECT pg_advisory_xact_lock(2026082401)"))
        for sql in migrations:
            await conn.execute(text(sql))
            logger.info("Migration OK: %.60s...", sql.strip())


async def init_db():
    global _schema_verified
    _schema_verified = False
    from app.models import models as _  # noqa: F401

    async with engine.begin() as conn:
        if engine.dialect.name == "postgresql":
            await conn.execute(text("SELECT pg_advisory_xact_lock(2026082401)"))
        await conn.run_sync(_ensure_pg_enums)
        await conn.run_sync(Base.metadata.create_all)

    if engine.dialect.name != "postgresql":
        await _seed_default_fleet()
        return

    async with engine.begin() as conn:
        await conn.run_sync(_add_missing_columns)

    # Step 4: explicit SQL migrations (most reliable, idempotent)
    await _run_explicit_migrations()

    await _clean_duplicate_mobile_users()
    await _sync_mobile_users_to_clients()
    await verify_database_ready()
