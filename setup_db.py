import psycopg2

DB_URL = 'postgresql://nomad_db_02mo_user:hNEbJscQPq89CuMTfbiWCW50bCtzt9NE@dpg-d90kfnlaeets73e63ing-a.oregon-postgres.render.com/nomad_db_02mo'

conn = psycopg2.connect(DB_URL)
cur = conn.cursor()

tables = [
    """CREATE TABLE IF NOT EXISTS admins (
        id SERIAL PRIMARY KEY,
        username VARCHAR(100) UNIQUE NOT NULL,
        password_hash VARCHAR(255) NOT NULL,
        created_at TIMESTAMP DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS instructors (
        id SERIAL PRIMARY KEY,
        name VARCHAR(200) NOT NULL,
        telegram_id VARCHAR(50),
        telegram_username VARCHAR(100),
        transmission VARCHAR(20) NOT NULL DEFAULT 'both',
        experience_years INTEGER DEFAULT 0,
        rating FLOAT DEFAULT 5.0,
        is_active BOOLEAN DEFAULT true,
        working_hours_start TIME DEFAULT '09:00',
        working_hours_end TIME DEFAULT '19:00',
        lunch_start TIME,
        lunch_end TIME,
        description TEXT,
        days_off VARCHAR(200) DEFAULT 'Суббота,Воскресенье',
        created_at TIMESTAMP DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS clients (
        id SERIAL PRIMARY KEY,
        telegram_id VARCHAR(50) UNIQUE NOT NULL,
        name VARCHAR(200) NOT NULL,
        phone VARCHAR(30),
        referral_code VARCHAR(50) UNIQUE,
        referred_by_client_id INTEGER,
        created_at TIMESTAMP DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS bookings (
        id SERIAL PRIMARY KEY,
        client_id INTEGER NOT NULL,
        instructor_id INTEGER NOT NULL,
        service_type VARCHAR(20) NOT NULL,
        transmission VARCHAR(20) NOT NULL,
        location VARCHAR(200) NOT NULL,
        booking_date DATE NOT NULL,
        start_time TIME NOT NULL,
        end_time TIME NOT NULL,
        status VARCHAR(20) DEFAULT 'planned',
        price INTEGER NOT NULL,
        package_id INTEGER,
        certificate_id INTEGER,
        confirmation_sent BOOLEAN DEFAULT false,
        confirmed_by_client BOOLEAN DEFAULT false,
        rating_sent BOOLEAN DEFAULT false,
        created_at TIMESTAMP DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS rating_records (
        id SERIAL PRIMARY KEY,
        booking_id INTEGER NOT NULL,
        instructor_id INTEGER NOT NULL,
        vote VARCHAR(10) NOT NULL,
        created_at TIMESTAMP DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS packages (
        id SERIAL PRIMARY KEY,
        name VARCHAR(200) NOT NULL,
        sessions_count INTEGER NOT NULL,
        price INTEGER NOT NULL,
        is_active BOOLEAN DEFAULT true
    )""",
    """CREATE TABLE IF NOT EXISTS client_packages (
        id SERIAL PRIMARY KEY,
        client_id INTEGER NOT NULL,
        package_id INTEGER NOT NULL,
        remaining_sessions INTEGER NOT NULL,
        purchased_at TIMESTAMP DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS certificates (
        id SERIAL PRIMARY KEY,
        code VARCHAR(50) UNIQUE NOT NULL,
        nominal INTEGER NOT NULL,
        remaining INTEGER NOT NULL,
        is_used BOOLEAN DEFAULT false,
        created_at TIMESTAMP DEFAULT NOW(),
        activated_by_client_id INTEGER
    )""",
    """CREATE TABLE IF NOT EXISTS referral_records (
        id SERIAL PRIMARY KEY,
        referrer_client_id INTEGER NOT NULL,
        referred_client_id INTEGER NOT NULL,
        discount_applied BOOLEAN DEFAULT false,
        created_at TIMESTAMP DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS faq_items (
        id SERIAL PRIMARY KEY,
        question TEXT NOT NULL,
        answer TEXT NOT NULL,
        sort_order INTEGER DEFAULT 0,
        is_active BOOLEAN DEFAULT true
    )""",
    """CREATE TABLE IF NOT EXISTS audit_logs (
        id SERIAL PRIMARY KEY,
        admin_username VARCHAR(100) NOT NULL,
        action VARCHAR(200) NOT NULL,
        details TEXT,
        created_at TIMESTAMP DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS notifications_sent (
        id SERIAL PRIMARY KEY,
        instructor_id INTEGER NOT NULL,
        notification_type VARCHAR(50) NOT NULL,
        sent_at TIMESTAMP DEFAULT NOW()
    )""",
]

for t in tables:
    cur.execute(t)
    print("OK:", t.split("(")[0].strip().split()[-1])

conn.commit()
print("All tables created!")
cur.close()
conn.close()
