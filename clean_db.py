import psycopg2

DB_URL = 'postgresql://nomad_db_02mo_user:hNEbJscQPq89CuMTfbiWCW50bCtzt9NE@dpg-d90kfnlaeets73e63ing-a.oregon-postgres.render.com/nomad_db_02mo'

conn = psycopg2.connect(DB_URL)
conn.autocommit = True
cur = conn.cursor()

tables = [
    "notifications_sent", "audit_logs", "rating_records", "referral_records",
    "client_packages", "certificates", "bookings", "faq_items",
    "packages", "clients", "instructors", "admins"
]
for t in tables:
    cur.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
    print(f"Dropped {t}")

for e in ["bookingstatus", "servicetype", "transmissiontype", "ratingvote"]:
    cur.execute(f"DROP TYPE IF EXISTS {e} CASCADE")
    print(f"Dropped enum {e}")

print("All clean!")
cur.close()
conn.close()
