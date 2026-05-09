"""One-time setup: create gps_hotspot_summary table and do initial data population."""
import psycopg, time

conn = psycopg.connect(
    'postgresql://postgres:Arjun2827@13.201.191.155:5432/gps_analytics',
    connect_timeout=60
)
conn.autocommit = False
cur = conn.cursor()

print("Creating gps_hotspot_summary table...")
cur.execute("""
    CREATE TABLE IF NOT EXISTS gps_hotspot_summary (
        alert      VARCHAR(5)       NOT NULL,
        latitude   DOUBLE PRECISION NOT NULL,
        longitude  DOUBLE PRECISION NOT NULL,
        events     INTEGER          NOT NULL,
        avg_speed  DOUBLE PRECISION
    )
""")
cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_hotspot_summary_lookup
    ON gps_hotspot_summary (alert, events DESC)
""")
conn.commit()
print("Table created.")

for code in ('HB', 'HA', 'RT'):
    print("Populating %s..." % code, end=' ', flush=True)
    t = time.time()
    cur.execute("DELETE FROM gps_hotspot_summary WHERE alert = %s", (code,))
    cur.execute("""
        INSERT INTO gps_hotspot_summary (alert, latitude, longitude, events, avg_speed)
        SELECT %s,
               ROUND(latitude::numeric, 2)::float,
               ROUND(longitude::numeric, 2)::float,
               COUNT(*),
               ROUND(AVG(device_speed)::numeric, 1)::float
        FROM gps_points
        WHERE gps_time >= NOW() - INTERVAL '3 days'
          AND alert = %s
          AND latitude  BETWEEN -90 AND 90
          AND longitude BETWEEN -180 AND 180
        GROUP BY ROUND(latitude::numeric, 2), ROUND(longitude::numeric, 2)
    """, (code, code))
    conn.commit()
    cur.execute("SELECT COUNT(*) FROM gps_hotspot_summary WHERE alert = %s", (code,))
    cnt = cur.fetchone()[0]
    print("%.1fs, %d rows" % (time.time()-t, cnt))

# Benchmark reading from summary table
print("\nBenchmark: reading from summary table...")
t = time.time()
cur.execute("SELECT * FROM gps_hotspot_summary WHERE alert = 'HB' ORDER BY events DESC LIMIT 200")
rows = cur.fetchall()
print("Query from summary table: %.4fs, rows=%d" % (time.time()-t, len(rows)))
conn.close()


