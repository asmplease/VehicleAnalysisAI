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
import re
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

def _current_partition_filter() -> str:
    """Return a single-condition Athena partition WHERE fragment for the current month.
    Intentionally one month only — an OR across months breaks SQL operator precedence
    when combined with AND deviceid=... filters in device-specific queries.
    Restart the server (or POST /api/cache/clear) after a month boundary.
    """
    from datetime import date
    today = date.today()
    return f"year={today.year} AND month={today.month}"

_MARCH = _current_partition_filter()   # evaluated once at startup / reload
_DATE_EXPR = "date(concat(cast(year AS VARCHAR),'-',lpad(cast(month AS VARCHAR),2,'0'),'-',lpad(cast(day AS VARCHAR),2,'0')))"

# Ignore alert events recorded while the vehicle was travelling below this threshold.
# Business rule: alerts at < 20 km/h are considered noise (e.g. inching in traffic).
_MIN_KMH   = 20
_SPEED_FILTER = f"AND CAST(devicespeed AS DOUBLE) >= {_MIN_KMH}"

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
        duration_seconds=120,   # 2-min hard timeout — prevents infinite pending
    ).cursor()


def _drop_stale_partition(s3_path: str) -> None:
    """Parse a failing S3 partition path from a HIVE_FILESYSTEM_ERROR and delete
    that entry from the Glue Data Catalog so subsequent Athena queries succeed.

    Expected path format:
        s3://bucket/alerts/{table}/year=YYYY/month=M/day=D/hour=H
    """
    try:
        import boto3

        m = re.search(
            r's3://[^/]+/alerts/(\w+)/year=(\d+)/month=0*(\d+)/day=0*(\d+)/hour=0*(\d+)',
            s3_path,
        )
        if not m:
            log.warning("_drop_stale_partition: could not parse path: %s", s3_path)
            return

        tbl_name = m.group(1)
        year, month, day, hour = m.group(2), m.group(3), m.group(4), m.group(5)

        allowed = {"harsh_braking", "harsh_acceleration", "harsh_cornering", "non_alerts", "raw"}
        if tbl_name not in allowed:
            log.warning("_drop_stale_partition: unexpected table '%s'", tbl_name)
            return

        glue = boto3.client(
            "glue",
            aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
            region_name=AWS_REGION,
        )
        glue.delete_partition(
            DatabaseName=ATHENA_DB,
            TableName=tbl_name,
            PartitionValues=[year, month, day, hour],
        )
        log.info(
            "Dropped stale Glue partition: table=%s year=%s month=%s day=%s hour=%s",
            tbl_name, year, month, day, hour,
        )
    except Exception as exc:
        log.warning("_drop_stale_partition failed (non-fatal): %s", exc)


def query_df(sql: str, retries: int = 3) -> pd.DataFrame:
    """Execute SQL with retry on transient Athena errors.

    On HIVE_FILESYSTEM_ERROR the stale Glue partition is deleted reactively
    and the query is retried once.  All other transient errors use exponential
    back-off.  HIVE_PARTITION_SCHEMA_MISMATCH fails fast (not recoverable).
    """
    for attempt in range(retries):
        try:
            return _cursor().execute(sql).as_pandas()
        except Exception as exc:
            msg = str(exc)

            if "HIVE_PARTITION_SCHEMA_MISMATCH" in msg:
                raise

            if "HIVE_FILESYSTEM_ERROR" in msg:
                # Reactively drop the bad partition then retry
                path_match = re.search(r's3://\S+', msg)
                if path_match:
                    _drop_stale_partition(path_match.group(0).rstrip('/'))
                if attempt < retries - 1:
                    log.warning(
                        "HIVE_FILESYSTEM_ERROR on attempt %d — dropped stale partition, retrying",
                        attempt + 1,
                    )
                    continue
                raise

            if attempt < retries - 1:
                wait = 2 ** attempt          # 1 s, 2 s
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

def _query_per_type(sql_hb: str, sql_ha: str, sql_rt: str):
    """Run three per-type-table queries in parallel, return (df_hb, df_ha, df_rt).
    Each df may be empty if the table has no data or a transient error."""
    def _safe_query(sql):
        try:
            return query_df(sql)
        except Exception as exc:
            log.warning("Per-type query failed (returning empty): %s", exc)
            return pd.DataFrame()

    with ThreadPoolExecutor(max_workers=3) as pool:
        fhb = pool.submit(_safe_query, sql_hb)
        fha = pool.submit(_safe_query, sql_ha)
        frt = pool.submit(_safe_query, sql_rt)
        return fhb.result(), fha.result(), frt.result()


@cached(_FLEET_CACHE, key=lambda: hashkey('fleet_summary'), lock=_FLEET_LOCK)
def get_fleet_summary() -> dict:
    """Count alerts per type + safe vs risky driver split — 3 separate queries per table."""
    # Three simple COUNT queries — no CTE, no UNION ALL, Athena handles each independently
    sql_hb = f"""SELECT COUNT(*) AS n, ROUND(AVG(CAST(devicespeed AS DOUBLE)),1) AS avg_s, ROUND(MAX(CAST(devicespeed AS DOUBLE)),1) AS max_s FROM harsh_braking      WHERE {_MARCH} {_SPEED_FILTER}"""
    sql_ha = f"""SELECT COUNT(*) AS n, ROUND(AVG(CAST(devicespeed AS DOUBLE)),1) AS avg_s, ROUND(MAX(CAST(devicespeed AS DOUBLE)),1) AS max_s FROM harsh_acceleration  WHERE {_MARCH} {_SPEED_FILTER}"""
    sql_rt = f"""SELECT COUNT(*) AS n, ROUND(AVG(CAST(devicespeed AS DOUBLE)),1) AS avg_s, ROUND(MAX(CAST(devicespeed AS DOUBLE)),1) AS max_s FROM harsh_cornering     WHERE {_MARCH} {_SPEED_FILTER}"""
    sql_raw = f"SELECT COUNT(*) AS n, COUNT(DISTINCT deviceid) AS total_devs FROM raw WHERE {_MARCH}"
    sql_adv = f"""SELECT COUNT(DISTINCT deviceid) AS n FROM (
        SELECT deviceid FROM harsh_braking     WHERE {_MARCH} {_SPEED_FILTER}
        UNION SELECT deviceid FROM harsh_acceleration WHERE {_MARCH} {_SPEED_FILTER}
        UNION SELECT deviceid FROM harsh_cornering    WHERE {_MARCH} {_SPEED_FILTER}
    ) t"""

    def _safe_query(sql):
        try:
            return query_df(sql)
        except Exception as exc:
            log.warning("fleet_summary query failed: %s", exc)
            return pd.DataFrame()

    with ThreadPoolExecutor(max_workers=5) as pool:
        fhb  = pool.submit(_safe_query, sql_hb)
        fha  = pool.submit(_safe_query, sql_ha)
        frt  = pool.submit(_safe_query, sql_rt)
        fraw = pool.submit(_safe_query, sql_raw)
        fadv = pool.submit(_safe_query, sql_adv)
        df_hb  = fhb.result()
        df_ha  = fha.result()
        df_rt  = frt.result()
        df_raw = fraw.result()
        df_adv = fadv.result()

    def _row(df): return df.iloc[0] if not df.empty else {}

    hb_row = _row(df_hb); ha_row = _row(df_ha); rt_row = _row(df_rt)
    raw_row = _row(df_raw); adv_row = _row(df_adv)

    def _n(r): return int(r.get('n') or 0) if hasattr(r, 'get') else 0

    hb_n = _n(hb_row); ha_n = _n(ha_row); rt_n = _n(rt_row)
    total_gps  = _n(raw_row)
    total_devs = int(raw_row.get('total_devs') or 0) if hasattr(raw_row, 'get') else 0
    risky_devs = _n(adv_row)
    safe_devs  = max(0, total_devs - risky_devs)
    total_alts = hb_n + ha_n + rt_n
    alert_rate = round(total_alts / total_gps * 1000, 1) if total_gps else 0

    return {
        'total_alerts':       total_alts,
        'unique_devices':     risky_devs,
        'harsh_braking':      hb_n,
        'harsh_acceleration': ha_n,
        'harsh_cornering':    rt_n,
        'avg_speed_at_alert': _safe_float(hb_row.get('avg_s') if hasattr(hb_row, 'get') else 0),
        'max_speed_at_alert': _safe_float(hb_row.get('max_s') if hasattr(hb_row, 'get') else 0),
        'total_gps_points':   total_gps,
        'total_devices':      total_devs,
        'safe_drivers':       safe_devs,
        'risky_drivers':      risky_devs,
        'safe_driver_pct':    round(safe_devs / total_devs * 100, 1) if total_devs else 0,
        'alert_rate_per_1k':  alert_rate,
    }


@cached(_FLEET_CACHE, key=lambda limit=25: hashkey('top_safe_drivers', limit), lock=_FLEET_LOCK)
def get_top_safe_drivers(limit: int = 25) -> list[dict]:
    """Devices with most GPS activity in March but ZERO alert events."""
    # Collect alert device ids from each table separately, merge in Python
    def _safe_query(sql):
        try:
            return query_df(sql)
        except Exception as exc:
            log.warning("top_safe_drivers sub-query failed: %s", exc)
            return pd.DataFrame(columns=['deviceid'])

    with ThreadPoolExecutor(max_workers=4) as pool:
        f_raw = pool.submit(_safe_query, f"SELECT deviceid, COUNT(*) AS gps_points, COUNT(DISTINCT day) AS active_days FROM raw WHERE {_MARCH} GROUP BY deviceid")
        f_hb  = pool.submit(_safe_query, f"SELECT DISTINCT deviceid FROM harsh_braking     WHERE {_MARCH} {_SPEED_FILTER}")
        f_ha  = pool.submit(_safe_query, f"SELECT DISTINCT deviceid FROM harsh_acceleration WHERE {_MARCH} {_SPEED_FILTER}")
        f_rt  = pool.submit(_safe_query, f"SELECT DISTINCT deviceid FROM harsh_cornering    WHERE {_MARCH} {_SPEED_FILTER}")
        df_raw = f_raw.result()
        df_hb  = f_hb.result()
        df_ha  = f_ha.result()
        df_rt  = f_rt.result()

    if df_raw.empty:
        return []

    risky_ids = set(
        list(df_hb['deviceid'].dropna()) +
        list(df_ha['deviceid'].dropna()) +
        list(df_rt['deviceid'].dropna())
    )
    safe_df = df_raw[~df_raw['deviceid'].isin(risky_ids)].copy()
    safe_df['avg_daily_pts'] = (safe_df['gps_points'] / safe_df['active_days'].replace(0, 1)).round(0)
    safe_df = safe_df.sort_values('gps_points', ascending=False).head(limit)
    return safe_df.to_dict(orient='records')


@cached(_FLEET_CACHE, key=lambda: hashkey('daily_trend'), lock=_FLEET_LOCK)
def get_daily_alert_trend() -> list[dict]:
    """Daily counts per alert type — 3 separate queries merged in Python."""
    def _safe_query(sql):
        try:
            return query_df(sql)
        except Exception as exc:
            log.warning("daily_trend sub-query failed: %s", exc)
            return pd.DataFrame()

    date_col = f"{_DATE_EXPR} AS date"
    sql_hb = f"SELECT {date_col}, COUNT(*) AS harsh_braking      FROM harsh_braking      WHERE {_MARCH} {_SPEED_FILTER} GROUP BY {_DATE_EXPR}"
    sql_ha = f"SELECT {date_col}, COUNT(*) AS harsh_acceleration  FROM harsh_acceleration  WHERE {_MARCH} {_SPEED_FILTER} GROUP BY {_DATE_EXPR}"
    sql_rt = f"SELECT {date_col}, COUNT(*) AS harsh_cornering     FROM harsh_cornering     WHERE {_MARCH} {_SPEED_FILTER} GROUP BY {_DATE_EXPR}"

    with ThreadPoolExecutor(max_workers=3) as pool:
        df_hb, df_ha, df_rt = [f.result() for f in [pool.submit(_safe_query, s) for s in [sql_hb, sql_ha, sql_rt]]]

    # Merge on date, fill missing with 0
    from functools import reduce
    dfs = [df for df in [df_hb, df_ha, df_rt] if not df.empty]
    if not dfs:
        return []
    merged = reduce(lambda a, b: pd.merge(a, b, on='date', how='outer'), dfs).fillna(0)
    for col in ['harsh_braking', 'harsh_acceleration', 'harsh_cornering']:
        if col not in merged.columns:
            merged[col] = 0
        merged[col] = merged[col].astype(int)
    merged = merged.sort_values('date')
    return merged.to_dict(orient='records')


@cached(_FLEET_CACHE, key=lambda: hashkey('hourly_dist'), lock=_FLEET_LOCK)
def get_hourly_alert_distribution() -> list[dict]:
    """Hourly counts per alert type — 3 separate queries merged in Python."""
    def _safe_query(sql):
        try:
            return query_df(sql)
        except Exception as exc:
            log.warning("hourly_dist sub-query failed: %s", exc)
            return pd.DataFrame()

    sql_hb = f"SELECT hour, COUNT(*) AS harsh_braking      FROM harsh_braking      WHERE {_MARCH} {_SPEED_FILTER} GROUP BY hour"
    sql_ha = f"SELECT hour, COUNT(*) AS harsh_acceleration  FROM harsh_acceleration  WHERE {_MARCH} {_SPEED_FILTER} GROUP BY hour"
    sql_rt = f"SELECT hour, COUNT(*) AS harsh_cornering     FROM harsh_cornering     WHERE {_MARCH} {_SPEED_FILTER} GROUP BY hour"

    with ThreadPoolExecutor(max_workers=3) as pool:
        df_hb, df_ha, df_rt = [f.result() for f in [pool.submit(_safe_query, s) for s in [sql_hb, sql_ha, sql_rt]]]

    from functools import reduce
    dfs = [df for df in [df_hb, df_ha, df_rt] if not df.empty]
    if not dfs:
        return []
    all_hours = pd.DataFrame({'hour': range(24)})
    merged = all_hours
    for df in dfs:
        merged = pd.merge(merged, df, on='hour', how='left')
    for col in ['harsh_braking', 'harsh_acceleration', 'harsh_cornering']:
        if col not in merged.columns:
            merged[col] = 0
        merged[col] = merged[col].fillna(0).astype(int)
    return merged.to_dict(orient='records')


@cached(_FLEET_CACHE, key=lambda alert_type='harsh_braking': hashkey('speed_dist', alert_type), lock=_FLEET_LOCK)
def get_speed_distribution(alert_type: str = "harsh_braking") -> list[dict]:
    safe = alert_type.replace("-", "_")
    # Query the specific per-type table directly (avoids raw table alerttype naming issues)
    return query_rows(f"""
        SELECT
            CAST(ROUND(CAST(devicespeed AS DOUBLE) / 10) * 10 AS INT) AS speed_bucket,
            COUNT(*) AS events
        FROM {safe}
        WHERE {_MARCH}
          AND devicespeed IS NOT NULL
          AND CAST(devicespeed AS DOUBLE) BETWEEN {_MIN_KMH} AND 200
        GROUP BY ROUND(CAST(devicespeed AS DOUBLE) / 10) * 10
        ORDER BY speed_bucket
    """)



@cached(_FLEET_CACHE, key=lambda alert_type='harsh_braking', limit=500: hashkey('hotspots', alert_type, limit), lock=_FLEET_LOCK)
def get_alert_hotspots(alert_type: str = "harsh_braking", limit: int = 500) -> list[dict]:
    safe = alert_type.replace("-", "_")
    # Query the specific per-type table directly (avoids raw table alerttype naming issues)
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
          AND CAST(devicespeed AS DOUBLE) >= {_MIN_KMH}
        GROUP BY
            ROUND(CAST(latitude  AS DOUBLE), 3),
            ROUND(CAST(longitude AS DOUBLE), 3)
        ORDER BY events DESC
        LIMIT {limit}
    """)


@cached(_FLEET_CACHE, key=lambda limit=50: hashkey('top_risky', limit), lock=_FLEET_LOCK)
def get_top_risky_devices(limit: int = 50) -> list[dict]:
    """Top risky devices — 3 separate per-table counts merged in Python."""
    def _safe_query(sql):
        try:
            return query_df(sql)
        except Exception as exc:
            log.warning("top_risky sub-query failed: %s", exc)
            return pd.DataFrame()

    sql_hb = f"SELECT deviceid, COUNT(*) AS harsh_braking      FROM harsh_braking      WHERE {_MARCH} {_SPEED_FILTER} GROUP BY deviceid"
    sql_ha = f"SELECT deviceid, COUNT(*) AS harsh_acceleration  FROM harsh_acceleration  WHERE {_MARCH} {_SPEED_FILTER} GROUP BY deviceid"
    sql_rt = f"SELECT deviceid, COUNT(*) AS harsh_cornering     FROM harsh_cornering     WHERE {_MARCH} {_SPEED_FILTER} GROUP BY deviceid"

    with ThreadPoolExecutor(max_workers=3) as pool:
        df_hb, df_ha, df_rt = [f.result() for f in [pool.submit(_safe_query, s) for s in [sql_hb, sql_ha, sql_rt]]]

    from functools import reduce
    dfs = [df for df in [df_hb, df_ha, df_rt] if not df.empty]
    if not dfs:
        return []
    merged = reduce(lambda a, b: pd.merge(a, b, on='deviceid', how='outer'), dfs).fillna(0)
    for col in ['harsh_braking', 'harsh_acceleration', 'harsh_cornering']:
        if col not in merged.columns:
            merged[col] = 0
        merged[col] = merged[col].astype(int)
    merged['total_alerts'] = merged['harsh_braking'] + merged['harsh_acceleration'] + merged['harsh_cornering']
    merged = merged.sort_values('total_alerts', ascending=False).head(limit)
    return merged.to_dict(orient='records')


# ─── Device-level ─────────────────────────────────────────────────────────────

@cached(_DEVICE_CACHE, key=lambda device_id: hashkey('dev_summary', device_id), lock=_DEVICE_LOCK)
def get_device_summary(device_id: str) -> dict:
    safe = device_id.replace("'", "")

    def _safe_query(sql):
        try:
            return query_df(sql)
        except Exception as exc:
            log.warning("dev_summary sub-query failed for %s: %s", safe, exc)
            return pd.DataFrame()

    sql_hb = f"SELECT COUNT(*) AS n, MAX(gpstime) AS last_seen, ROUND(AVG(CAST(devicespeed AS DOUBLE)),1) AS avg_s, ROUND(MAX(CAST(devicespeed AS DOUBLE)),1) AS max_s FROM harsh_braking      WHERE {_MARCH} {_SPEED_FILTER} AND deviceid='{safe}'"
    sql_ha = f"SELECT COUNT(*) AS n, MAX(gpstime) AS last_seen, ROUND(AVG(CAST(devicespeed AS DOUBLE)),1) AS avg_s, ROUND(MAX(CAST(devicespeed AS DOUBLE)),1) AS max_s FROM harsh_acceleration  WHERE {_MARCH} {_SPEED_FILTER} AND deviceid='{safe}'"
    sql_rt = f"SELECT COUNT(*) AS n, MAX(gpstime) AS last_seen, ROUND(AVG(CAST(devicespeed AS DOUBLE)),1) AS avg_s, ROUND(MAX(CAST(devicespeed AS DOUBLE)),1) AS max_s FROM harsh_cornering     WHERE {_MARCH} {_SPEED_FILTER} AND deviceid='{safe}'"

    with ThreadPoolExecutor(max_workers=3) as pool:
        df_hb, df_ha, df_rt = [f.result() for f in [pool.submit(_safe_query, s) for s in [sql_hb, sql_ha, sql_rt]]]

    def _row(df): return df.iloc[0] if not df.empty else {}
    hb = _row(df_hb); ha = _row(df_ha); rt = _row(df_rt)

    def _n(r): return int(r.get('n') or 0) if hasattr(r, 'get') else 0
    hb_n = _n(hb); ha_n = _n(ha); rt_n = _n(rt)
    return {
        'deviceid':           safe,
        'last_seen':          str(hb.get('last_seen') or '—') if hasattr(hb, 'get') else '—',
        'total_alerts':       hb_n + ha_n + rt_n,
        'harsh_braking':      hb_n,
        'harsh_acceleration': ha_n,
        'harsh_cornering':    rt_n,
        'avg_speed':          _safe_float(hb.get('avg_s') if hasattr(hb, 'get') else 0),
        'max_speed':          _safe_float(hb.get('max_s') if hasattr(hb, 'get') else 0),
        'is_safe_driver':     (hb_n + ha_n + rt_n) == 0,
    }


@cached(_DEVICE_CACHE, key=lambda device_id: hashkey('dev_timeline', device_id), lock=_DEVICE_LOCK)
def get_device_alert_timeline(device_id: str) -> list[dict]:
    safe = device_id.replace("'", "")

    def _safe_rows(sql, alerttype):
        try:
            df = query_df(sql)
            if df.empty:
                return []
            df['alerttype'] = alerttype
            df['alertdisplayname'] = alerttype
            return df.to_dict(orient='records')
        except Exception as exc:
            log.warning("dev_timeline sub-query failed for %s: %s", safe, exc)
            return []

    sel = "gpstime, ROUND(CAST(devicespeed AS DOUBLE),1) AS speed, CAST(latitude AS DOUBLE) AS latitude, CAST(longitude AS DOUBLE) AS longitude"
    with ThreadPoolExecutor(max_workers=3) as pool:
        f_hb = pool.submit(_safe_rows, f"SELECT {sel} FROM harsh_braking      WHERE {_MARCH} {_SPEED_FILTER} AND deviceid='{safe}'", 'harsh_braking')
        f_ha = pool.submit(_safe_rows, f"SELECT {sel} FROM harsh_acceleration  WHERE {_MARCH} {_SPEED_FILTER} AND deviceid='{safe}'", 'harsh_acceleration')
        f_rt = pool.submit(_safe_rows, f"SELECT {sel} FROM harsh_cornering     WHERE {_MARCH} {_SPEED_FILTER} AND deviceid='{safe}'", 'harsh_cornering')
        rows = f_hb.result() + f_ha.result() + f_rt.result()

    rows.sort(key=lambda r: r.get('gpstime', ''), reverse=True)
    return rows[:500]


@cached(_DEVICE_CACHE, key=lambda device_id: hashkey('dev_daily', device_id), lock=_DEVICE_LOCK)
def get_device_daily_alerts(device_id: str) -> list[dict]:
    safe = device_id.replace("'", "")

    def _safe_query(sql):
        try:
            return query_df(sql)
        except Exception as exc:
            log.warning("dev_daily sub-query failed for %s: %s", safe, exc)
            return pd.DataFrame()

    date_col = f"{_DATE_EXPR} AS date"
    sql_hb = f"SELECT {date_col}, COUNT(*) AS harsh_braking      FROM harsh_braking      WHERE {_MARCH} {_SPEED_FILTER} AND deviceid='{safe}' GROUP BY {_DATE_EXPR}"
    sql_ha = f"SELECT {date_col}, COUNT(*) AS harsh_acceleration  FROM harsh_acceleration  WHERE {_MARCH} {_SPEED_FILTER} AND deviceid='{safe}' GROUP BY {_DATE_EXPR}"
    sql_rt = f"SELECT {date_col}, COUNT(*) AS harsh_cornering     FROM harsh_cornering     WHERE {_MARCH} {_SPEED_FILTER} AND deviceid='{safe}' GROUP BY {_DATE_EXPR}"

    with ThreadPoolExecutor(max_workers=3) as pool:
        df_hb, df_ha, df_rt = [f.result() for f in [pool.submit(_safe_query, s) for s in [sql_hb, sql_ha, sql_rt]]]

    from functools import reduce
    dfs = [df for df in [df_hb, df_ha, df_rt] if not df.empty]
    if not dfs:
        return []
    merged = reduce(lambda a, b: pd.merge(a, b, on='date', how='outer'), dfs).fillna(0)
    for col in ['harsh_braking', 'harsh_acceleration', 'harsh_cornering']:
        if col not in merged.columns:
            merged[col] = 0
        merged[col] = merged[col].astype(int)
    merged = merged.sort_values('date')
    return merged.to_dict(orient='records')


@cached(_DEVICE_CACHE, key=lambda device_id: hashkey('dev_map', device_id), lock=_DEVICE_LOCK)
def get_device_alert_map(device_id: str, day: int = None) -> list[dict]:
    safe = device_id.replace("'", "")
    day_filter = f"AND day = {int(day)}" if day else ""
    sel = f"CAST(latitude AS DOUBLE) AS latitude, CAST(longitude AS DOUBLE) AS longitude, gpstime, ROUND(CAST(devicespeed AS DOUBLE),1) AS speed"

    def _safe_rows(sql, alerttype):
        try:
            df = query_df(sql)
            if df.empty:
                return []
            df = df[df['latitude'].notna() & df['longitude'].notna()]
            df['alerttype'] = alerttype
            df['alertdisplayname'] = alerttype
            return df.to_dict(orient='records')
        except Exception as exc:
            log.warning("dev_map sub-query failed for %s: %s", safe, exc)
            return []

    with ThreadPoolExecutor(max_workers=3) as pool:
        f_hb = pool.submit(_safe_rows, f"SELECT {sel} FROM harsh_braking      WHERE {_MARCH} {_SPEED_FILTER} AND deviceid='{safe}' {day_filter} AND latitude IS NOT NULL AND longitude IS NOT NULL LIMIT 700", 'harsh_braking')
        f_ha = pool.submit(_safe_rows, f"SELECT {sel} FROM harsh_acceleration  WHERE {_MARCH} {_SPEED_FILTER} AND deviceid='{safe}' {day_filter} AND latitude IS NOT NULL AND longitude IS NOT NULL LIMIT 700", 'harsh_acceleration')
        f_rt = pool.submit(_safe_rows, f"SELECT {sel} FROM harsh_cornering     WHERE {_MARCH} {_SPEED_FILTER} AND deviceid='{safe}' {day_filter} AND latitude IS NOT NULL AND longitude IS NOT NULL LIMIT 700", 'harsh_cornering')
        return f_hb.result() + f_ha.result() + f_rt.result()


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
    # Threshold ~16 m in degrees.
    DEDUP = 0.00015
    # Max jump ~33 km in degrees — anything larger is a GPS teleport / bad datum.
    MAX_JUMP = 0.30
    out, prev = [], None
    for r in rows:
        lat, lon = r.get('latitude'), r.get('longitude')
        if lat is None or lon is None:
            continue
        if prev:
            dlat = abs(lat - prev[0])
            dlon = abs(lon - prev[1])
            # Skip GPS outlier — keep prev as anchor so next valid point continues
            if dlat > MAX_JUMP or dlon > MAX_JUMP:
                continue
            # Skip near-duplicate stationary ping
            if dlat < DEDUP and dlon < DEDUP:
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

    def _safe_query(sql):
        try:
            return query_df(sql)
        except Exception as exc:
            log.warning("search_devices sub-query failed: %s", exc)
            return pd.DataFrame()

    sql_hb = f"SELECT deviceid, COUNT(*) AS hb_n, MAX(gpstime) AS last_seen FROM harsh_braking      WHERE {_MARCH} {_SPEED_FILTER} AND deviceid LIKE '{safe}%' GROUP BY deviceid"
    sql_ha = f"SELECT deviceid, COUNT(*) AS ha_n, MAX(gpstime) AS last_seen FROM harsh_acceleration  WHERE {_MARCH} {_SPEED_FILTER} AND deviceid LIKE '{safe}%' GROUP BY deviceid"
    sql_rt = f"SELECT deviceid, COUNT(*) AS rt_n, MAX(gpstime) AS last_seen FROM harsh_cornering     WHERE {_MARCH} {_SPEED_FILTER} AND deviceid LIKE '{safe}%' GROUP BY deviceid"

    with ThreadPoolExecutor(max_workers=3) as pool:
        df_hb, df_ha, df_rt = [f.result() for f in [pool.submit(_safe_query, s) for s in [sql_hb, sql_ha, sql_rt]]]

    from functools import reduce
    dfs = []
    for df, col in [(df_hb, 'hb_n'), (df_ha, 'ha_n'), (df_rt, 'rt_n')]:
        if not df.empty:
            # keep max last_seen per deviceid, rename count col
            df = df[['deviceid', col, 'last_seen']]
            dfs.append(df)
    if not dfs:
        return []
    merged = reduce(lambda a, b: pd.merge(a, b, on='deviceid', how='outer', suffixes=('', '_r')), dfs).fillna(0)
    # Consolidate last_seen columns
    ls_cols = [c for c in merged.columns if 'last_seen' in c]
    merged['last_seen'] = merged[ls_cols].max(axis=1)
    for col in ['hb_n', 'ha_n', 'rt_n']:
        if col not in merged.columns:
            merged[col] = 0
    merged['total_alerts'] = merged['hb_n'].astype(int) + merged['ha_n'].astype(int) + merged['rt_n'].astype(int)
    merged = merged[['deviceid', 'total_alerts', 'last_seen']].sort_values('total_alerts', ascending=False).head(limit)
    return merged.to_dict(orient='records')


def repair_alert_table_partitions() -> None:
    """Remove stale Glue partition entries (S3 paths that don't exist) for each
    per-type alert table.  Uses boto3 Glue + S3 directly because Athena v3
    (Trino engine) does not support MSCK REPAIR TABLE syntax.

    Strategy for each table:
      1. List all registered Glue partitions.
      2. HEAD-check the S3 location of each partition.
      3. Batch-delete any partition whose S3 prefix has no objects.
    Non-fatal — a failure here is logged but does not abort startup.
    """
    import boto3

    glue = boto3.client(
        "glue",
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
        region_name=AWS_REGION,
    )
    s3 = boto3.client(
        "s3",
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
        region_name=AWS_REGION,
    )

    def _s3_prefix_exists(bucket: str, prefix: str) -> bool:
        """Return True if at least one object exists under the given S3 prefix."""
        time.sleep(0.08)  # throttle to stay under S3 rate limits
        resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=1)
        return resp.get("KeyCount", 0) > 0

    for tbl in ("harsh_braking", "harsh_acceleration", "harsh_cornering"):
        try:
            # Collect all registered partitions
            paginator = glue.get_paginator("get_partitions")
            stale_values: list[list[str]] = []

            for page in paginator.paginate(DatabaseName=ATHENA_DB, TableName=tbl):
                for part in page["Partitions"]:
                    loc: str = part["StorageDescriptor"]["Location"]
                    # loc looks like s3://bucket/path/year=2026/month=3/day=5/hour=5/
                    loc = loc.rstrip("/") + "/"
                    if not loc.startswith("s3://"):
                        continue
                    without_scheme = loc[5:]
                    bucket_name, _, prefix = without_scheme.partition("/")
                    if not _s3_prefix_exists(bucket_name, prefix):
                        stale_values.append(part["Values"])

            if not stale_values:
                log.info("Partition repair %s: no stale partitions found.", tbl)
                continue

            log.info("Partition repair %s: deleting %d stale partitions …", tbl, len(stale_values))
            # Glue batch_delete_partition accepts up to 25 entries per call
            CHUNK = 25
            for i in range(0, len(stale_values), CHUNK):
                chunk = stale_values[i : i + CHUNK]
                glue.batch_delete_partition(
                    DatabaseName=ATHENA_DB,
                    TableName=tbl,
                    PartitionsToDelete=[{"Values": v} for v in chunk],
                )
            log.info("Partition repair %s: done.", tbl)

        except Exception as exc:
            log.warning("Partition repair failed for %s (non-fatal): %s", tbl, exc)


def prewarm_fleet_caches() -> None:
    """Run all heavy fleet queries in parallel threads to populate caches on startup.
    Partition repair is intentionally NOT run here — it does expensive S3 scanning
    and blocks the reload.  Use POST /api/cache/repair to run it on-demand.
    """
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
