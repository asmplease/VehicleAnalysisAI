"""
Athena query helpers – gps_analytics database.

All 5 tables are Hive-partitioned by year/month/day/hour.
camelCase JSON keys are correctly mapped via Glue SerDe (mapping.* props).
Columns: deviceid, clientid, gpstime, devicespeed, orientation,
         latitude, longitude, vehicletype, alert, alerttype,
         hasalert, alertdisplayname
"""
from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
from cachetools import TTLCache, cached
from cachetools.keys import hashkey
from dotenv import load_dotenv
from pyathena import connect
from pyathena.pandas.cursor import PandasCursor

load_dotenv()

log = logging.getLogger(__name__)

ATHENA_DB        = os.environ.get("ATHENA_DB",        "gps_analytics")
ATHENA_OUTPUT    = os.environ.get("ATHENA_OUTPUT",    "s3://aws-bucket-logs-monetize/athena-results/")
ATHENA_WORKGROUP = os.environ.get("ATHENA_WORKGROUP", "primary")
AWS_REGION       = os.environ.get("AWS_DEFAULT_REGION", "ap-south-1")

_MARCH     = "year=2026 AND month=3"
_DATE_EXPR = "date(concat(cast(year AS VARCHAR),'-',lpad(cast(month AS VARCHAR),2,'0'),'-',lpad(cast(day AS VARCHAR),2,'0')))"

# ─── Caches ────────────────────────────────────────────────────────────────────
# Fleet queries are heavy (scan 500 M rows) but March data is static → cache 30 min.
# Device queries are lighter but numerous            → cache 10 min.
_FLEET_CACHE  = TTLCache(maxsize=64,  ttl=1800)   # 30 min
_DEVICE_CACHE = TTLCache(maxsize=256, ttl=600)    # 10 min
_FLEET_LOCK   = threading.RLock()
_DEVICE_LOCK  = threading.RLock()


def cache_info() -> dict:
    """Return sizes of both caches (for /api/cache/status)."""
    return {
        "fleet_cache":  {"size": len(_FLEET_CACHE),  "maxsize": _FLEET_CACHE.maxsize,  "ttl": _FLEET_CACHE.ttl},
        "device_cache": {"size": len(_DEVICE_CACHE), "maxsize": _DEVICE_CACHE.maxsize, "ttl": _DEVICE_CACHE.ttl},
    }


def cache_clear():
    """Evict all cached entries."""
    with _FLEET_LOCK:
        _FLEET_CACHE.clear()
    with _DEVICE_LOCK:
        _DEVICE_CACHE.clear()


def _cursor() -> PandasCursor:
    return connect(
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
        region_name=AWS_REGION,
        s3_staging_dir=ATHENA_OUTPUT,
        schema_name=ATHENA_DB,
        work_group=ATHENA_WORKGROUP,
        cursor_class=PandasCursor,
    ).cursor()


def query_df(sql: str, retries: int = 4) -> pd.DataFrame:
    """Execute SQL with exponential-backoff retry on transient Athena errors."""
    for attempt in range(retries):
        try:
            return _cursor().execute(sql).as_pandas()
        except Exception as exc:
            if attempt < retries - 1:
                wait = 2 ** attempt          # 1 s, 2 s, 4 s
                log.warning("Athena error (attempt %d/%d): %s — retrying in %ds",
                            attempt + 1, retries, exc, wait)
                time.sleep(wait)
            else:
                raise


def query_rows(sql: str) -> list[dict]:
    df = query_df(sql)
    return df.where(pd.notnull(df), None).to_dict(orient="records")


def _safe_float(v, default: float = 0.0) -> float:
    """Convert pandas/numpy value to plain Python float.
    Maps NaN / None / NaT → default so json.dumps never sees NaN.
    """
    try:
        f = float(v)
        return default if f != f else f  # f != f is True only for NaN
    except (TypeError, ValueError):
        return default


# ─── Fleet-level ──────────────────────────────────────────────────────────────

@cached(_FLEET_CACHE, key=lambda: hashkey('fleet_summary'), lock=_FLEET_LOCK)
def get_fleet_summary() -> dict:
    """Count alerts per table + safe vs risky driver split — 5 queries run in parallel."""
    sql_hb = f"""
        SELECT COUNT(*) AS n, COUNT(DISTINCT deviceid) AS dv,
               ROUND(AVG(CAST(devicespeed AS DOUBLE)),1) AS avg_s,
               ROUND(MAX(CAST(devicespeed AS DOUBLE)),1) AS max_s
        FROM harsh_braking WHERE {_MARCH}"""
    sql_ha  = f"SELECT COUNT(*) AS n FROM harsh_acceleration WHERE {_MARCH}"
    sql_rt  = f"SELECT COUNT(*) AS n FROM harsh_cornering    WHERE {_MARCH}"
    sql_raw = f"SELECT COUNT(*) AS n, COUNT(DISTINCT deviceid) AS total_devs FROM raw WHERE {_MARCH}"
    sql_adv = f"""
        SELECT COUNT(DISTINCT deviceid) AS n FROM (
            SELECT deviceid FROM harsh_braking      WHERE {_MARCH}
            UNION
            SELECT deviceid FROM harsh_acceleration  WHERE {_MARCH}
            UNION
            SELECT deviceid FROM harsh_cornering     WHERE {_MARCH}
        )"""

    with ThreadPoolExecutor(max_workers=5) as pool:
        f_hb  = pool.submit(query_df, sql_hb)
        f_ha  = pool.submit(query_df, sql_ha)
        f_rt  = pool.submit(query_df, sql_rt)
        f_raw = pool.submit(query_df, sql_raw)
        f_adv = pool.submit(query_df, sql_adv)
        hb         = f_hb.result().iloc[0]
        ha         = f_ha.result().iloc[0]
        rt         = f_rt.result().iloc[0]
        raw        = f_raw.result().iloc[0]
        alert_devs = f_adv.result().iloc[0]

    hb_n       = int(hb.get("n")          or 0)
    ha_n       = int(ha.get("n")          or 0)
    rt_n       = int(rt.get("n")          or 0)
    total_gps  = int(raw.get("n")         or 0)
    total_devs = int(raw.get("total_devs") or 0)
    risky_devs = int(alert_devs.get("n")  or 0)
    safe_devs  = max(0, total_devs - risky_devs)
    total_alts = hb_n + ha_n + rt_n
    alert_rate = round(total_alts / total_gps * 1000, 1) if total_gps else 0

    return {
        "total_alerts":       total_alts,
        "unique_devices":     risky_devs,
        "harsh_braking":      hb_n,
        "harsh_acceleration": ha_n,
        "harsh_cornering":    rt_n,
        "avg_speed_at_alert": float(hb.get("avg_s") or 0),
        "max_speed_at_alert": float(hb.get("max_s") or 0),
        "total_gps_points":   total_gps,
        "total_devices":      total_devs,
        "safe_drivers":       safe_devs,
        "risky_drivers":      risky_devs,
        "safe_driver_pct":    round(safe_devs / total_devs * 100, 1) if total_devs else 0,
        "alert_rate_per_1k":  alert_rate,
    }


@cached(_FLEET_CACHE, key=lambda limit=25: hashkey('top_safe_drivers', limit), lock=_FLEET_LOCK)
def get_top_safe_drivers(limit: int = 25) -> list[dict]:
    """Devices with most GPS activity in March but ZERO alert events."""
    return query_rows(f"""
        SELECT r.deviceid,
               r.gps_points,
               r.active_days,
               ROUND(CAST(r.gps_points AS DOUBLE) / r.active_days, 0) AS avg_daily_pts
        FROM (
            SELECT deviceid, COUNT(*) AS gps_points, COUNT(DISTINCT day) AS active_days
            FROM raw WHERE {_MARCH}
            GROUP BY deviceid
        ) r
        LEFT JOIN (
            SELECT deviceid FROM harsh_braking     WHERE {_MARCH}
            UNION
            SELECT deviceid FROM harsh_acceleration WHERE {_MARCH}
            UNION
            SELECT deviceid FROM harsh_cornering    WHERE {_MARCH}
        ) a ON r.deviceid = a.deviceid
        WHERE a.deviceid IS NULL
        ORDER BY r.gps_points DESC
        LIMIT {limit}
    """)


@cached(_FLEET_CACHE, key=lambda: hashkey('daily_trend'), lock=_FLEET_LOCK)
def get_daily_alert_trend() -> list[dict]:
    return query_rows(f"""
        SELECT
            d AS date,
            SUM(CASE WHEN src='hb' THEN cnt ELSE 0 END) AS harsh_braking,
            SUM(CASE WHEN src='ha' THEN cnt ELSE 0 END) AS harsh_acceleration,
            SUM(CASE WHEN src='rt' THEN cnt ELSE 0 END) AS harsh_cornering
        FROM (
            SELECT {_DATE_EXPR} AS d, 'hb' AS src, COUNT(*) AS cnt
            FROM harsh_braking WHERE {_MARCH} GROUP BY {_DATE_EXPR}
            UNION ALL
            SELECT {_DATE_EXPR}, 'ha', COUNT(*)
            FROM harsh_acceleration WHERE {_MARCH} GROUP BY {_DATE_EXPR}
            UNION ALL
            SELECT {_DATE_EXPR}, 'rt', COUNT(*)
            FROM harsh_cornering WHERE {_MARCH} GROUP BY {_DATE_EXPR}
        )
        GROUP BY d ORDER BY d
    """)


@cached(_FLEET_CACHE, key=lambda: hashkey('hourly_dist'), lock=_FLEET_LOCK)
def get_hourly_alert_distribution() -> list[dict]:
    return query_rows(f"""
        SELECT hour,
            SUM(CASE WHEN src='hb' THEN cnt ELSE 0 END) AS harsh_braking,
            SUM(CASE WHEN src='ha' THEN cnt ELSE 0 END) AS harsh_acceleration,
            SUM(CASE WHEN src='rt' THEN cnt ELSE 0 END) AS harsh_cornering
        FROM (
            SELECT hour, 'hb' AS src, COUNT(*) AS cnt FROM harsh_braking     WHERE {_MARCH} GROUP BY hour
            UNION ALL
            SELECT hour, 'ha', COUNT(*)                FROM harsh_acceleration WHERE {_MARCH} GROUP BY hour
            UNION ALL
            SELECT hour, 'rt', COUNT(*)                FROM harsh_cornering    WHERE {_MARCH} GROUP BY hour
        )
        GROUP BY hour ORDER BY hour
    """)


@cached(_FLEET_CACHE, key=lambda alert_type='harsh_braking': hashkey('speed_dist', alert_type), lock=_FLEET_LOCK)
def get_speed_distribution(alert_type: str = "harsh_braking") -> list[dict]:
    safe = alert_type.replace("-", "_")
    return query_rows(f"""
        SELECT
            CAST(ROUND(CAST(devicespeed AS DOUBLE) / 10) * 10 AS INT) AS speed_bucket,
            COUNT(*) AS events
        FROM {safe}
        WHERE {_MARCH}
          AND devicespeed IS NOT NULL
          AND CAST(devicespeed AS DOUBLE) BETWEEN 0 AND 200
        GROUP BY ROUND(CAST(devicespeed AS DOUBLE) / 10) * 10
        ORDER BY speed_bucket
    """)



@cached(_FLEET_CACHE, key=lambda alert_type='harsh_braking', limit=500: hashkey('hotspots', alert_type, limit), lock=_FLEET_LOCK)
def get_alert_hotspots(alert_type: str = "harsh_braking", limit: int = 500) -> list[dict]:
    safe = alert_type.replace("-", "_")
    return query_rows(f"""
        SELECT
            ROUND(CAST(latitude  AS DOUBLE), 3) AS latitude,
            ROUND(CAST(longitude AS DOUBLE), 3) AS longitude,
            COUNT(*)                            AS events,
            COUNT(DISTINCT deviceid)            AS devices,
            ROUND(AVG(CAST(devicespeed AS DOUBLE)), 1) AS avg_speed
        FROM {safe}
        WHERE {_MARCH}
          AND latitude  IS NOT NULL AND longitude  IS NOT NULL
          AND CAST(latitude  AS DOUBLE) BETWEEN  -90 AND  90
          AND CAST(longitude AS DOUBLE) BETWEEN -180 AND 180
        GROUP BY
            ROUND(CAST(latitude  AS DOUBLE), 3),
            ROUND(CAST(longitude AS DOUBLE), 3)
        ORDER BY events DESC
        LIMIT {limit}
    """)


@cached(_FLEET_CACHE, key=lambda limit=50: hashkey('top_risky', limit), lock=_FLEET_LOCK)
def get_top_risky_devices(limit: int = 50) -> list[dict]:
    return query_rows(f"""
        SELECT
            deviceid,
            SUM(hb) AS harsh_braking,
            SUM(ha) AS harsh_acceleration,
            SUM(rt) AS harsh_cornering,
            SUM(hb+ha+rt) AS total_alerts
        FROM (
            SELECT deviceid, 1 AS hb, 0 AS ha, 0 AS rt FROM harsh_braking      WHERE {_MARCH}
            UNION ALL
            SELECT deviceid, 0, 1, 0                   FROM harsh_acceleration  WHERE {_MARCH}
            UNION ALL
            SELECT deviceid, 0, 0, 1                   FROM harsh_cornering     WHERE {_MARCH}
        )
        GROUP BY deviceid
        ORDER BY total_alerts DESC
        LIMIT {limit}
    """)


# ─── Device-level ─────────────────────────────────────────────────────────────

@cached(_DEVICE_CACHE, key=lambda device_id: hashkey('dev_summary', device_id), lock=_DEVICE_LOCK)
def get_device_summary(device_id: str) -> dict:
    safe = device_id.replace("'", "")
    hb = query_df(f"""
        SELECT COUNT(*) AS n, MAX(gpstime) AS last_seen,
               ROUND(AVG(CAST(devicespeed AS DOUBLE)),1) AS avg_s,
               ROUND(MAX(CAST(devicespeed AS DOUBLE)),1) AS max_s
        FROM harsh_braking WHERE {_MARCH} AND deviceid='{safe}'
    """).iloc[0]
    ha = query_df(f"SELECT COUNT(*) AS n FROM harsh_acceleration WHERE {_MARCH} AND deviceid='{safe}'").iloc[0]
    rt = query_df(f"SELECT COUNT(*) AS n FROM harsh_cornering    WHERE {_MARCH} AND deviceid='{safe}'").iloc[0]

    hb_n = int(hb.get("n") or 0)
    ha_n = int(ha.get("n") or 0)
    rt_n = int(rt.get("n") or 0)
    # Always return a summary — safe drivers have 0 alerts, that's valid
    return {
        "deviceid":           safe,
        "last_seen":          str(hb.get("last_seen") or "—"),
        "total_alerts":       hb_n + ha_n + rt_n,
        "harsh_braking":      hb_n,
        "harsh_acceleration": ha_n,
        "harsh_cornering":    rt_n,
        "avg_speed":          _safe_float(hb.get("avg_s")),
        "max_speed":          _safe_float(hb.get("max_s")),
        "is_safe_driver":     (hb_n + ha_n + rt_n) == 0,
    }


@cached(_DEVICE_CACHE, key=lambda device_id: hashkey('dev_timeline', device_id), lock=_DEVICE_LOCK)
def get_device_alert_timeline(device_id: str) -> list[dict]:
    safe = device_id.replace("'", "")
    return query_rows(f"""
        SELECT gpstime, alerttype, alertdisplayname,
               ROUND(CAST(devicespeed AS DOUBLE),1) AS speed,
               CAST(latitude    AS DOUBLE) AS latitude,
               CAST(longitude   AS DOUBLE) AS longitude
        FROM (
            SELECT gpstime, alerttype, alertdisplayname, devicespeed, latitude, longitude
            FROM harsh_braking      WHERE {_MARCH} AND deviceid='{safe}'
            UNION ALL
            SELECT gpstime, alerttype, alertdisplayname, devicespeed, latitude, longitude
            FROM harsh_acceleration  WHERE {_MARCH} AND deviceid='{safe}'
            UNION ALL
            SELECT gpstime, alerttype, alertdisplayname, devicespeed, latitude, longitude
            FROM harsh_cornering     WHERE {_MARCH} AND deviceid='{safe}'
        )
        ORDER BY gpstime DESC
        LIMIT 500
    """)


@cached(_DEVICE_CACHE, key=lambda device_id: hashkey('dev_daily', device_id), lock=_DEVICE_LOCK)
def get_device_daily_alerts(device_id: str) -> list[dict]:
    safe = device_id.replace("'", "")
    return query_rows(f"""
        SELECT
            d AS date,
            SUM(CASE WHEN src='hb' THEN cnt ELSE 0 END) AS harsh_braking,
            SUM(CASE WHEN src='ha' THEN cnt ELSE 0 END) AS harsh_acceleration,
            SUM(CASE WHEN src='rt' THEN cnt ELSE 0 END) AS harsh_cornering
        FROM (
            SELECT {_DATE_EXPR} AS d, 'hb' AS src, COUNT(*) AS cnt
            FROM harsh_braking WHERE {_MARCH} AND deviceid='{safe}' GROUP BY {_DATE_EXPR}
            UNION ALL
            SELECT {_DATE_EXPR}, 'ha', COUNT(*)
            FROM harsh_acceleration WHERE {_MARCH} AND deviceid='{safe}' GROUP BY {_DATE_EXPR}
            UNION ALL
            SELECT {_DATE_EXPR}, 'rt', COUNT(*)
            FROM harsh_cornering WHERE {_MARCH} AND deviceid='{safe}' GROUP BY {_DATE_EXPR}
        )
        GROUP BY d ORDER BY d
    """)


@cached(_DEVICE_CACHE, key=lambda device_id: hashkey('dev_map', device_id), lock=_DEVICE_LOCK)
def get_device_alert_map(device_id: str) -> list[dict]:
    safe = device_id.replace("'", "")
    return query_rows(f"""
        SELECT
            CAST(latitude  AS DOUBLE) AS latitude,
            CAST(longitude AS DOUBLE) AS longitude,
            alerttype, alertdisplayname, gpstime,
            ROUND(CAST(devicespeed AS DOUBLE),1) AS speed
        FROM (
            SELECT latitude, longitude, alerttype, alertdisplayname, gpstime, devicespeed
            FROM harsh_braking      WHERE {_MARCH} AND deviceid='{safe}'
            UNION ALL
            SELECT latitude, longitude, alerttype, alertdisplayname, gpstime, devicespeed
            FROM harsh_acceleration  WHERE {_MARCH} AND deviceid='{safe}'
            UNION ALL
            SELECT latitude, longitude, alerttype, alertdisplayname, gpstime, devicespeed
            FROM harsh_cornering     WHERE {_MARCH} AND deviceid='{safe}'
        )
        WHERE latitude IS NOT NULL AND longitude IS NOT NULL
        LIMIT 1000
    """)


@cached(_DEVICE_CACHE, key=lambda device_id, day=None: hashkey('dev_route', device_id, day), lock=_DEVICE_LOCK)
def get_device_gps_route(device_id: str, day: int = None) -> list[dict]:
    """Ordered GPS track for a device from raw table — for polyline on map.
    If day is given, returns that day only; otherwise returns the latest day
    with data (to keep response size manageable).
    """
    safe = device_id.replace("'", "")
    day_filter = f"AND day={day}" if day else ""
    # Get the latest day that has data for this device
    day_df = query_df(f"""
        SELECT day FROM raw
        WHERE {_MARCH} AND deviceid='{safe}' {day_filter}
        GROUP BY day ORDER BY day DESC LIMIT 1
    """)
    if day_df.empty:
        return []
    target_day = int(day_df.iloc[0]["day"])
    rows = query_rows(f"""
        SELECT
            CAST(latitude  AS DOUBLE) AS latitude,
            CAST(longitude AS DOUBLE) AS longitude,
            gpstime,
            ROUND(CAST(devicespeed AS DOUBLE),1) AS speed
        FROM raw
        WHERE {_MARCH} AND day={target_day} AND deviceid='{safe}'
          AND latitude  IS NOT NULL AND longitude  IS NOT NULL
          AND CAST(latitude  AS DOUBLE) BETWEEN  -90 AND  90
          AND CAST(longitude AS DOUBLE) BETWEEN -180 AND 180
        ORDER BY gpstime
        LIMIT 2000
    """)
    # Deduplicate near-consecutive GPS pings (device parked / GPS jitter).
    # Threshold ~16 m in degrees; keeps the polyline smooth without gaps.
    THRESH = 0.00015
    out, prev = [], None
    for r in rows:
        lat, lon = r.get('latitude'), r.get('longitude')
        if lat is None or lon is None:
            continue
        if prev and abs(lat - prev[0]) < THRESH and abs(lon - prev[1]) < THRESH:
            continue
        out.append(r)
        prev = (lat, lon)
    return out


@cached(_DEVICE_CACHE, key=lambda device_id: hashkey('dev_gps_days', device_id), lock=_DEVICE_LOCK)
def get_device_gps_days(device_id: str) -> list[dict]:
    """Days in March that have GPS track data for this device (route date picker)."""
    safe = device_id.replace("'", "")
    return query_rows(f"""
        SELECT day, COUNT(*) AS gps_points
        FROM raw
        WHERE {_MARCH} AND deviceid='{safe}'
          AND latitude IS NOT NULL AND longitude IS NOT NULL
        GROUP BY day ORDER BY day
    """)


# search_devices intentionally NOT cached — user types live queries
def search_devices(q: str, limit: int = 30) -> list[dict]:
    safe = q.replace("'", "")
    return query_rows(f"""
        SELECT deviceid, COUNT(*) AS total_alerts, MAX(gpstime) AS last_seen
        FROM (
            SELECT deviceid, gpstime FROM harsh_braking      WHERE {_MARCH} AND deviceid LIKE '{safe}%'
            UNION ALL
            SELECT deviceid, gpstime FROM harsh_acceleration  WHERE {_MARCH} AND deviceid LIKE '{safe}%'
            UNION ALL
            SELECT deviceid, gpstime FROM harsh_cornering     WHERE {_MARCH} AND deviceid LIKE '{safe}%'
        )
        GROUP BY deviceid
        ORDER BY total_alerts DESC
        LIMIT {limit}
    """)


def prewarm_fleet_caches() -> None:
    """Run all heavy fleet queries in parallel threads to populate caches on startup."""
    log.info("Pre-warming fleet caches in background…")
    jobs = [
        (get_fleet_summary,            ()),
        (get_daily_alert_trend,        ()),
        (get_hourly_alert_distribution,()),
        (get_speed_distribution,       ('harsh_braking',)),
        (get_speed_distribution,       ('harsh_acceleration',)),
        (get_speed_distribution,       ('harsh_cornering',)),
        (get_alert_hotspots,           ('harsh_braking',   500)),
        (get_alert_hotspots,           ('harsh_acceleration', 500)),
        (get_alert_hotspots,           ('harsh_cornering', 500)),
        (get_top_risky_devices,        (50,)),
        (get_top_safe_drivers,         (25,)),
    ]
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(fn, *args): fn.__name__ for fn, args in jobs}
        for fut, name in futures.items():
            try:
                fut.result()
                log.info("Prewarm OK: %s", name)
            except Exception as exc:
                log.warning("Prewarm FAILED: %s — %s", name, exc)
    log.info("Fleet cache pre-warm complete.")
