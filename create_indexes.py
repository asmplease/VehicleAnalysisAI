"""
One-time script: create performance indexes on gps_points (31.5 M rows).

Run once:
    .venv\Scripts\python.exe create_indexes.py

Uses CONCURRENTLY so the table stays readable while indexes build.
Each index takes a few minutes on a large table — this is expected.
"""
from __future__ import annotations

import os
import time

import psycopg
from dotenv import load_dotenv

load_dotenv()

PG_DSN = os.environ.get("PG_DSN", "")
if not PG_DSN:
    raise SystemExit("PG_DSN environment variable is not set")

INDEXES = [
    (
        "idx_gps_points_time",
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_gps_points_time
            ON public.gps_points (gps_time DESC)
        """,
        "gps_time DESC — speeds up all fleet-wide time-range queries",
    ),
    (
        "idx_gps_points_device_time",
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_gps_points_device_time
            ON public.gps_points (device_id, gps_time DESC)
        """,
        "(device_id, gps_time) — speeds up all per-device time-range queries",
    ),
    (
        "idx_gps_points_alert_time",
        """
        CREATE INDEX IF NOT EXISTS idx_gps_points_alert_time
            ON public.gps_points (alert, gps_time DESC)
            WHERE alert IS NOT NULL
        """,
        "(alert, gps_time) partial — speeds up alert-type filtering queries",
    ),
    (
        "idx_gps_points_device_alert_time",
        """
        CREATE INDEX IF NOT EXISTS idx_gps_points_device_alert_time
            ON public.gps_points (device_id, alert, gps_time DESC)
            INCLUDE (latitude, longitude, device_speed)
            WHERE alert IN ('HB', 'HA', 'RT')
        """,
        "(device_id, alert, gps_time) covering partial — speeds up device timelines",
    ),
]


def main() -> None:
    # autocommit=True is required for CREATE INDEX CONCURRENTLY
    with psycopg.connect(PG_DSN, autocommit=True) as conn:
        with conn.cursor() as cur:
            # Check existing indexes first
            cur.execute("""
                SELECT indexname
                FROM pg_indexes
                WHERE tablename = 'gps_points'
                ORDER BY indexname
            """)
            existing = {r[0] for r in cur.fetchall()}
            print("Existing indexes on gps_points:")
            for idx in sorted(existing):
                print(f"  {idx}")
            print()

            for name, sql, desc in INDEXES:
                if name in existing:
                    print(f"[SKIP]  {name} — already exists")
                    continue
                print(f"[BUILD] {name}")
                print(f"        {desc}")
                t0 = time.time()
                cur.execute(sql)
                elapsed = time.time() - t0
                print(f"        Done in {elapsed:.1f}s\n")

    print("All indexes created.")


if __name__ == "__main__":
    main()
