import psycopg2

DB_URL = 'postgresql://nomad_db_02mo_user:hNEbJscQPq89CuMTfbiWCW50bCtzt9NE@dpg-d90kfnlaeets73e63ing-a.oregon-postgres.render.com/nomad_db_02mo'
conn = psycopg2.connect(DB_URL)
conn.autocommit = True
cur = conn.cursor()

cur.execute("""
INSERT INTO instructors (name, transmission, experience_years, rating, is_active, working_hours_start, working_hours_end, days_off, description) VALUES
('Арина', 'BOTH', 3, 5.0, true, '09:00', '19:00', 'Суббота,Воскресенье', 'Молодой и энергичный инструктор. Терпеливая и внимательная, идеально подходит для новичков.'),
('Роман', 'MANUAL', 8, 4.9, true, '09:00', '19:00', 'Суббота,Воскресенье', 'Опытный инструктор с 8-летним стажем. Спокойный и методичный.'),
('Ерлан', 'AUTOMATIC', 10, 4.8, true, '09:00', '19:00', 'Суббота,Воскресенье', 'Практикующий водитель с 10-летним опытом. Индивидуальный подход к каждому ученику.')
""")
print("Instructors: OK")

cur.execute("""
INSERT INTO packages (name, sessions_count, price, is_active) VALUES
('Базовый', 10, 60000, true),
('Стандарт', 20, 110000, true),
('Премиум', 30, 150000, true)
""")
print("Packages: OK")

conn.commit()
cur.close()
conn.close()
