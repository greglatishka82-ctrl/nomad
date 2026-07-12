import psycopg2
DB_URL = 'postgresql://nomad_db_02mo_user:hNEbJscQPq89CuMTfbiWCW50bCtzt9NE@dpg-d90kfnlaeets73e63ing-a.oregon-postgres.render.com/nomad_db_02mo'
conn = psycopg2.connect(DB_URL)
conn.autocommit = True
cur = conn.cursor()

cur.execute("SELECT typname, enumlabel FROM pg_type t JOIN pg_enum e ON t.oid = e.enumtypid ORDER BY typname, enumsortorder")
rows = cur.fetchall()
for r in rows:
    print(r)

print("---")
cur.execute("SELECT column_name, udt_name FROM information_schema.columns WHERE table_name = 'instructors' AND column_name = 'transmission'")
print('transmission col:', cur.fetchone())

cur.close()
conn.close()
