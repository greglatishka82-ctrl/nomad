import psycopg2
DB_URL = 'postgresql://nomad_db_02mo_user:hNEbJscQPq89CuMTfbiWCW50bCtzt9NE@dpg-d90kfnlaeets73e63ing-a.oregon-postgres.render.com/nomad_db_02mo'
conn = psycopg2.connect(DB_URL)
conn.autocommit = True
cur = conn.cursor()
cur.execute("DELETE FROM instructors WHERE name IN ('Арина', 'Роман')")
print('Deleted:', cur.rowcount)
cur.execute('SELECT id, name FROM instructors')
for r in cur.fetchall():
    print('Left:', r)
cur.close()
conn.close()
