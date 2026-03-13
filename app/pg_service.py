"""
PostgreSQL service — reads from driver_daily_scores and device_behaviour_profile.
These tables are written by the C# ChannelService scoring pipeline.
Uses psycopg3 (sync) with a simple connection-per-call pattern (RDS is fast enough).
"""
from __future__ import annotations

import logging
from datetime import date

import psycopg
import psycopg.rows

from app.config import PG_DSN

log = logging.getLogger(__name__)


def _conn():
    """Open a new psycopg3 connection. Caller must use as context manager."""
    return psycopg.connect(PG_DSN, row_factory=psycopg.rows.dict_row, connect_timeout=15)


# ── Behaviour Profile ─────────────────────────────────────────────────────────

def get_driver_profile(device_id: str) -> dict:
    """
    Returns today's score, 7/30-day rolling averages and risk category
    from device_behaviour_profile.
    Returns {} if the device has no profile yet.
    """
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        score_today,
                        ROUND(score_7day_avg::numeric, 1)::float       AS score_7day_avg,
                        ROUND(score_30day_avg::numeric, 1)::float      AS score_30day_avg,
                        total_alerts_7d,
                        total_alerts_30d,
                        ROUND(braking_rate_per_100km::numeric, 2)::float    AS braking_rate_per_100km,
                        ROUND(accel_rate_per_100km::numeric, 2)::float      AS accel_rate_per_100km,
                        ROUND(cornering_rate_per_100km::numeric, 2)::float  AS cornering_rate_per_100km,
                        risk_category,
                        last_updated
                    FROM device_behaviour_profile
                    WHERE device_id = %s
                    """,
                    (device_id,),
                )
                row = cur.fetchone()
                if not row:
                    return {}
                d = dict(row)
                # Convert datetime → ISO string for JSON
                if hasattr(d.get("last_updated"), "isoformat"):
                    d["last_updated"] = d["last_updated"].isoformat()
                return d
    except Exception as exc:
        log.error("PG get_driver_profile(%s): %s", device_id, exc)
        return {}


# ── Daily Scores ──────────────────────────────────────────────────────────────

def get_driver_daily_scores(device_id: str) -> list[dict]:
    """
    Returns one row per day in March 2026 for the device including:
    - current_score, total_deductions
    - per-type counts (total_hb, total_ha, total_rt)
    - per-severity counts (hb_critical/high/medium/low, ha_high/medium/low, rt_high/medium/low)
    """
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        score_date,
                        current_score,
                        total_deductions,
                        total_hb,
                        total_ha,
                        total_rt,
                        COALESCE(hb_critical, 0) AS hb_critical,
                        COALESCE(hb_high,     0) AS hb_high,
                        COALESCE(hb_medium,   0) AS hb_medium,
                        COALESCE(hb_low,      0) AS hb_low,
                        COALESCE(ha_high,     0) AS ha_high,
                        COALESCE(ha_medium,   0) AS ha_medium,
                        COALESCE(ha_low,      0) AS ha_low,
                        COALESCE(rt_high,     0) AS rt_high,
                        COALESCE(rt_medium,   0) AS rt_medium,
                        COALESCE(rt_low,      0) AS rt_low
                    FROM driver_daily_scores
                    WHERE device_id = %s
                      AND score_date BETWEEN '2026-03-01' AND '2026-03-31'
                    ORDER BY score_date ASC
                    """,
                    (device_id,),
                )
                rows = cur.fetchall()
                # Convert date objects → ISO string for JSON serialisation
                result = []
                for r in rows:
                    d = dict(r)
                    if isinstance(d.get("score_date"), date):
                        d["score_date"] = d["score_date"].isoformat()
                    result.append(d)
                return result
    except Exception as exc:
        log.error("PG get_driver_daily_scores(%s): %s", device_id, exc)
        return []


# ── Fleet Risk Distribution ───────────────────────────────────────────────────

def get_fleet_risk_distribution() -> list[dict]:
    """
    Returns count of devices in each risk category (Low/Medium/High/Critical)
    from device_behaviour_profile.
    """
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT risk_category, COUNT(*) AS device_count
                    FROM device_behaviour_profile
                    WHERE risk_category IS NOT NULL
                    GROUP BY risk_category
                    ORDER BY
                        CASE risk_category
                            WHEN 'Critical' THEN 1
                            WHEN 'High'     THEN 2
                            WHEN 'Medium'   THEN 3
                            WHEN 'Low'      THEN 4
                            ELSE 5
                        END
                    """
                )
                return [dict(r) for r in cur.fetchall()]
    except Exception as exc:
        log.error("PG get_fleet_risk_distribution: %s", exc)
        return []
