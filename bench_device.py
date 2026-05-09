import time
import psycopg

DEVICE_ID = "862567073534757"
DSN = "postgresql://postgres:Arjun2827@13.201.191.155:5432/gps_analytics"

queries = {
    "profile": """
        select p.device_id, p.risk_category, p.score_today, p.score_7day_avg,
               p.score_30day_avg, p.last_updated
        from public.device_behaviour_profile p
        where p.device_id = %s
    """,
    "scores": """
        select min(score_date), max(score_date), count(*), round(avg(current_score)::numeric, 2),
               min(current_score), max(current_score), sum(total_hb), sum(total_ha), sum(total_rt),
               sum(total_deductions)
        from public.driver_daily_scores
        where device_id = %s
    """,
    "gps_aggregate": """
        select max(gps_time), max(gps_time::date), round(avg(device_speed)::numeric, 2),
               max(device_speed), count(*), count(*) filter (where device_speed > 180)
        from public.gps_points
        where device_id = %s
          and gps_time >= now() - interval '30 days'
    """,
}

with psycopg.connect(DSN, connect_timeout=30) as conn:
    with conn.cursor() as cur:
        for name, sql in queries.items():
            started = time.time()
            cur.execute(sql, (DEVICE_ID,))
            row = cur.fetchone()
            print(f"{name}: {time.time() - started:.2f}s -> {row}")

        print("\nEXPLAIN gps_aggregate:")
        cur.execute("EXPLAIN (ANALYZE, BUFFERS) " + queries["gps_aggregate"], (DEVICE_ID,))
        for row in cur.fetchall():
            print(row[0])
