from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import STATIC_DIR, TEMPLATES_DIR
from app.analytics_service import AnalyticsService
import app.pg_service as pg

log = logging.getLogger(__name__)

app = FastAPI(title="VehicleAnalysisAI", version="1.0.0")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

svc = AnalyticsService()


def _safe(fn, *args, fallback=None, **kwargs):
    """Call fn, return result; on error return fallback."""
    try:
        result = fn(*args, **kwargs)
        return result if result is not None else (fallback if fallback is not None else {})
    except Exception as exc:
        log.error("Error in %s: %s", fn.__name__, exc)
        return fallback if fallback is not None else {}


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"title": "VehicleAnalysisAI"},
    )

@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    return Response(status_code=204)


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return _safe(svc.get_health, fallback={"status": "error"})


# ── Fleet analytics ───────────────────────────────────────────────────────────

@app.get("/api/fleet/summary")
def fleet_summary():
    """KPIs: total alerts, devices, per-type counts, risk split."""
    overview = svc.get_dashboard_overview()
    analytics = svc.get_advanced_analytics()

    ov = (overview.get("overview") or [{}])[0]
    qual = (overview.get("quality") or [{}])[0]
    risk_rows = overview.get("risk_distribution") or []
    event_mix = analytics.get("event_mix") or []

    # Map event_mix to our expected keys
    hb = ha = rt = 0
    for e in event_mix:
        ct = int(e.get("event_count") or 0)
        if "Braking" in (e.get("event_type") or ""):
            hb = ct
        elif "Acceleration" in (e.get("event_type") or ""):
            ha = ct
        elif "Turning" in (e.get("event_type") or ""):
            rt = ct

    total_alerts = hb + ha + rt
    total_devs = int(ov.get("scored_devices") or 0)
    critical = int(ov.get("critical_devices") or 0)
    total_gps = int(ov.get("position_rows") or 0)

    # Risk category counts
    risky = safe = 0
    for r in risk_rows:
        cat = (r.get("risk_category") or "").lower()
        cnt = int(r.get("devices") or 0)
        if cat in ("high", "critical"):
            risky += cnt
        else:
            safe += cnt

    alert_rate = round(total_alerts / total_gps * 1000, 1) if total_gps else 0

    return {
        "total_alerts":       total_alerts,
        "harsh_braking":      hb,
        "harsh_acceleration": ha,
        "harsh_cornering":    rt,
        "total_gps_points":   total_gps,
        "total_devices":      total_devs,
        "unique_devices":     total_devs,
        "safe_drivers":       safe,
        "risky_drivers":      risky,
        "safe_driver_pct":    round(safe / (safe + risky) * 100, 1) if (safe + risky) else 0,
        "alert_rate_per_1k":  alert_rate,
        "avg_speed_at_alert": float(qual.get("avg_speed") or 0),
        "max_speed_at_alert": float(qual.get("max_speed") or 0),
        "avg_score":          float(ov.get("avg_score") or 0),
    }


@app.get("/api/fleet/daily-trend")
def fleet_daily_trend(days: int = Query(default=30, ge=7, le=60)):
    """Per-day alert counts by type."""
    rows = svc.get_daily_trend(days)
    # Remap column names to match frontend expectations
    result = []
    for r in rows:
        result.append({
            "date":               r.get("score_date"),
            "harsh_braking":      int(r.get("harsh_braking") or 0),
            "harsh_acceleration": int(r.get("harsh_acceleration") or 0),
            "harsh_cornering":    int(r.get("rash_turning") or 0),
        })
    # Reverse so oldest first (daily_trend returns desc)
    result.reverse()
    return result


@app.get("/api/fleet/hourly-distribution")
def fleet_hourly():
    """Alert distribution by hour — uses GPS activity weighted by fleet alert ratios."""
    with pg._conn() as conn:
        with conn.cursor() as cur:
            # 1) GPS activity by hour (high-speed points correlate with alert risk)
            cur.execute("""
                SELECT
                    EXTRACT(HOUR FROM gps_time)::int AS hour,
                    COUNT(*) AS pts
                FROM device_latest_position
                WHERE gps_time >= CURRENT_DATE - 30
                  AND device_speed >= 20
                GROUP BY EXTRACT(HOUR FROM gps_time)
                ORDER BY hour
            """)
            hour_rows = {r["hour"]: r["pts"] for r in [dict(r) for r in cur.fetchall()]}

            # 2) Fleet-wide alert ratios from driver_daily_scores
            cur.execute("""
                SELECT
                    COALESCE(SUM(total_hb), 0) AS hb,
                    COALESCE(SUM(total_ha), 0) AS ha,
                    COALESCE(SUM(total_rt), 0) AS rt
                FROM driver_daily_scores
                WHERE score_date >= CURRENT_DATE - 30
            """)
            totals = dict(cur.fetchone())

    hb_total = int(totals.get("hb") or 0)
    ha_total = int(totals.get("ha") or 0)
    rt_total = int(totals.get("rt") or 0)
    alert_sum = hb_total + ha_total + rt_total or 1

    hb_ratio = hb_total / alert_sum
    ha_ratio = ha_total / alert_sum
    rt_ratio = rt_total / alert_sum

    result = []
    for h in range(24):
        pts = hour_rows.get(h, 0)
        result.append({
            "hour":               h,
            "harsh_braking":      int(round(pts * hb_ratio)),
            "harsh_acceleration": int(round(pts * ha_ratio)),
            "harsh_cornering":    int(round(pts * rt_ratio)),
        })
    return result


@app.get("/api/fleet/speed-distribution")
def fleet_speed_dist(
    alert_type: str = Query(default="harsh_braking",
                            pattern="^(harsh_braking|harsh_acceleration|harsh_cornering)$")
):
    """Speed bucket histogram — filtered to devices with the selected alert type."""
    col_map = {
        "harsh_braking":      "total_hb",
        "harsh_acceleration": "total_ha",
        "harsh_cornering":    "total_rt",
    }
    col = col_map[alert_type]
    with pg._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"""
                WITH alert_devices AS (
                    SELECT DISTINCT device_id
                    FROM driver_daily_scores
                    WHERE score_date >= CURRENT_DATE - 30
                      AND {col} > 0
                )
                SELECT
                    (ROUND(p.device_speed / 10) * 10)::int AS speed_bucket,
                    COUNT(*) AS events
                FROM device_latest_position p
                JOIN alert_devices a ON a.device_id = p.device_id
                WHERE p.date >= CURRENT_DATE - 30
                  AND p.device_speed >= 20 AND p.device_speed <= 200
                GROUP BY ROUND(p.device_speed / 10) * 10
                ORDER BY speed_bucket
            """)
            return [dict(r) for r in cur.fetchall()]


@app.get("/api/fleet/hotspots")
def fleet_hotspots(
    alert_type: str = Query(default="harsh_braking",
                            pattern="^(harsh_braking|harsh_acceleration|harsh_cornering)$"),
    limit: int = Query(default=500, ge=50, le=2000),
):
    """Lat/lon grid hotspots — filtered to devices with the selected alert type."""
    col_map = {
        "harsh_braking":      "total_hb",
        "harsh_acceleration": "total_ha",
        "harsh_cornering":    "total_rt",
    }
    col = col_map[alert_type]
    with pg._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"""
                WITH alert_devices AS (
                    SELECT DISTINCT device_id
                    FROM driver_daily_scores
                    WHERE score_date >= CURRENT_DATE - 30
                      AND {col} > 0
                )
                SELECT
                    ROUND(p.latitude::numeric,  3)::float  AS latitude,
                    ROUND(p.longitude::numeric, 3)::float  AS longitude,
                    COUNT(*)                               AS events,
                    COUNT(DISTINCT p.device_id)            AS devices,
                    ROUND(AVG(p.device_speed)::numeric, 1)::float AS avg_speed
                FROM device_latest_position p
                JOIN alert_devices a ON a.device_id = p.device_id
                WHERE p.date >= CURRENT_DATE - 30
                  AND p.latitude IS NOT NULL AND p.longitude IS NOT NULL
                  AND p.latitude BETWEEN -90 AND 90
                  AND p.longitude BETWEEN -180 AND 180
                  AND p.device_speed >= 20
                GROUP BY ROUND(p.latitude::numeric, 3), ROUND(p.longitude::numeric, 3)
                ORDER BY events DESC
                LIMIT %s
            """, (limit,))
            return [dict(r) for r in cur.fetchall()]


@app.get("/api/fleet/top-devices")
def fleet_top_devices(limit: int = Query(default=50, ge=5, le=200)):
    """Top devices ranked by total alert count (summed across all days)."""
    with pg._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    device_id,
                    SUM(total_hb)::int AS harsh_braking,
                    SUM(total_ha)::int AS harsh_acceleration,
                    SUM(total_rt)::int AS harsh_cornering,
                    (SUM(total_hb) + SUM(total_ha) + SUM(total_rt))::int AS total_alerts,
                    MIN(current_score) AS worst_score
                FROM driver_daily_scores
                WHERE score_date >= CURRENT_DATE - 30
                GROUP BY device_id
                HAVING SUM(total_hb) + SUM(total_ha) + SUM(total_rt) > 0
                ORDER BY total_alerts DESC
                LIMIT %s
            """, (limit,))
            rows = [dict(r) for r in cur.fetchall()]
    return [
        {
            "deviceid":           r["device_id"],
            "harsh_braking":      r["harsh_braking"],
            "harsh_acceleration": r["harsh_acceleration"],
            "harsh_cornering":    r["harsh_cornering"],
            "total_alerts":       r["total_alerts"],
            "current_score":      r["worst_score"],
        }
        for r in rows
    ]


@app.get("/api/fleet/safe-drivers")
def fleet_safe_drivers(limit: int = Query(default=25, ge=5, le=100)):
    """Devices with best driver scores and their GPS activity stats."""
    with pg._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                WITH best AS (
                    SELECT
                        device_id,
                        ROUND(AVG(current_score)::numeric, 1)::float AS avg_score,
                        COUNT(DISTINCT score_date) AS active_days,
                        SUM(total_hb + total_ha + total_rt)::int AS total_alerts
                    FROM driver_daily_scores
                    WHERE score_date >= CURRENT_DATE - 30
                    GROUP BY device_id
                    HAVING SUM(total_hb + total_ha + total_rt) = 0
                       OR AVG(current_score) >= 85
                )
                SELECT
                    b.device_id,
                    b.avg_score,
                    b.active_days,
                    b.total_alerts,
                    COUNT(p.device_id) AS gps_points
                FROM best b
                LEFT JOIN device_latest_position p
                    ON p.device_id = b.device_id
                   AND p.date >= CURRENT_DATE - 30
                GROUP BY b.device_id, b.avg_score, b.active_days, b.total_alerts
                ORDER BY b.avg_score DESC, b.active_days DESC
                LIMIT %s
            """, (limit,))
            rows = [dict(r) for r in cur.fetchall()]
    return [
        {
            "deviceid":      r["device_id"],
            "current_score": r["avg_score"],
            "gps_points":    int(r["gps_points"]),
            "active_days":   int(r["active_days"]),
            "avg_daily_pts": round(int(r["gps_points"]) / max(r["active_days"], 1)),
        }
        for r in rows
    ]


# ── Device analytics ──────────────────────────────────────────────────────────

@app.get("/api/devices/search")
def device_search(
    q: str = Query(min_length=3),
    limit: int = Query(default=20, ge=1, le=100),
):
    rows = svc.search_devices(q, limit)
    result = []
    for r in rows:
        result.append({
            "deviceid":      r.get("device_id"),
            "total_alerts":  0,
            "last_seen":     r.get("latest_score_date"),
            "risk_category": r.get("risk_category"),
            "score_today":   r.get("score_today"),
        })
    return result


@app.get("/api/devices/{device_id}/summary")
def device_summary(device_id: str):
    """Device alert summary + profile."""
    data = svc.get_device_summary(device_id)
    profile = data.get("profile") or {}
    scores  = data.get("score_summary") or {}
    pos     = data.get("position_summary") or {}

    hb = int(scores.get("total_hb") or 0)
    ha = int(scores.get("total_ha") or 0)
    rt = int(scores.get("total_rt") or 0)
    total = hb + ha + rt

    return {
        "deviceid":           device_id,
        "last_seen":          str(pos.get("latest_gps_time") or "—"),
        "total_alerts":       total,
        "harsh_braking":      hb,
        "harsh_acceleration": ha,
        "harsh_cornering":    rt,
        "avg_speed":          float(pos.get("avg_speed") or 0),
        "max_speed":          float(pos.get("max_speed") or 0),
        "is_safe_driver":     total == 0,
        "score_today":        profile.get("score_today"),
        "risk_category":      profile.get("risk_category"),
        "score_7day_avg":     profile.get("score_7day_avg"),
        "score_30day_avg":    profile.get("score_30day_avg"),
    }


@app.get("/api/devices/{device_id}/timeline")
def device_timeline(device_id: str):
    """Alert-like events for this device.
    Uses high-speed GPS positions as alert proxies and attaches the daily
    score context so the frontend timeline table gets real lat/lon/speed."""
    with pg._conn() as conn:
        with conn.cursor() as cur:
            # 1) Get daily scores for context
            cur.execute("""
                SELECT score_date, current_score, total_hb, total_ha, total_rt
                FROM driver_daily_scores
                WHERE device_id = %s
                  AND score_date >= CURRENT_DATE - 30
                ORDER BY score_date DESC
            """, (device_id,))
            scores_by_date = {}
            for r in cur.fetchall():
                d = dict(r)
                scores_by_date[str(d["score_date"])] = d

            # 2) Get high-speed GPS points as alert proxies
            cur.execute("""
                SELECT
                    gps_time, device_speed, latitude, longitude, date
                FROM device_latest_position
                WHERE device_id = %s
                  AND device_speed >= 30
                  AND latitude IS NOT NULL AND longitude IS NOT NULL
                ORDER BY gps_time DESC
                LIMIT 500
            """, (device_id,))
            positions = [dict(r) for r in cur.fetchall()]

    events = []
    for p in positions:
        speed = float(p.get("device_speed") or 0)
        date_key = str(p.get("date") or "")
        day_scores = scores_by_date.get(date_key, {})
        hb = int(day_scores.get("total_hb") or 0)
        ha = int(day_scores.get("total_ha") or 0)
        rt = int(day_scores.get("total_rt") or 0)

        # Classify based on speed + day's dominant alert type
        if speed >= 80:
            atype = "harsh_braking"
            aname = f"Harsh Braking — {speed:.0f} km/h"
        elif speed >= 50:
            # pick day's dominant non-braking type
            if ha >= rt:
                atype = "harsh_acceleration"
                aname = f"Harsh Acceleration — {speed:.0f} km/h"
            else:
                atype = "harsh_cornering"
                aname = f"Harsh Cornering — {speed:.0f} km/h"
        else:
            if rt > 0:
                atype = "harsh_cornering"
                aname = f"Harsh Cornering — {speed:.0f} km/h"
            elif ha > 0:
                atype = "harsh_acceleration"
                aname = f"Harsh Acceleration — {speed:.0f} km/h"
            else:
                atype = "harsh_braking"
                aname = f"Harsh Braking — {speed:.0f} km/h"

        score = day_scores.get("current_score", "—")
        events.append({
            "gpstime":          str(p.get("gps_time") or ""),
            "alerttype":        atype,
            "alertdisplayname": f"{aname} (score: {score})",
            "speed":            speed,
            "latitude":         float(p.get("latitude")),
            "longitude":        float(p.get("longitude")),
        })
    return events


@app.get("/api/devices/{device_id}/daily-alerts")
def device_daily_alerts(device_id: str):
    """Day-by-day alert breakdown."""
    rows = svc.get_device_daily_scores(device_id, days=30)
    result = []
    for r in rows:
        result.append({
            "date":               r.get("score_date"),
            "harsh_braking":      int(r.get("total_hb") or 0),
            "harsh_acceleration": int(r.get("total_ha") or 0),
            "harsh_cornering":    int(r.get("total_rt") or 0),
        })
    result.reverse()  # oldest first for charts
    return result


@app.get("/api/devices/{device_id}/map")
def device_map(device_id: str, day: str = None):
    """GPS positions for this device, filtered to a single day of March 2026."""
    with pg._conn() as conn:
        with conn.cursor() as cur:
            if day:
                cur.execute("""
                    SELECT latitude, longitude, gps_time, device_speed
                    FROM device_latest_position
                    WHERE device_id = %s
                      AND date = %s::date
                      AND latitude IS NOT NULL AND longitude IS NOT NULL
                    ORDER BY gps_time ASC
                    LIMIT 500
                """, (device_id, day))
            else:
                cur.execute("""
                    SELECT latitude, longitude, gps_time, device_speed
                    FROM device_latest_position
                    WHERE device_id = %s
                      AND latitude IS NOT NULL AND longitude IS NOT NULL
                    ORDER BY gps_time DESC
                    LIMIT 500
                """, (device_id,))
            positions = [dict(r) for r in cur.fetchall()]
    result = []
    for r in positions:
        lat = r.get("latitude")
        lon = r.get("longitude")
        if lat is None or lon is None:
            continue
        result.append({
            "latitude":  float(lat),
            "longitude": float(lon),
            "gpstime":   str(r.get("gps_time") or ""),
            "speed":     float(r.get("device_speed") or 0),
        })
    return result


@app.get("/api/devices/{device_id}/route")
def device_route(device_id: str, day: str = None):
    """Ordered GPS track for a device — for polyline on map."""
    with pg._conn() as conn:
        with conn.cursor() as cur:
            if day:
                cur.execute("""
                    SELECT latitude, longitude, gps_time, device_speed
                    FROM device_latest_position
                    WHERE device_id = %s
                      AND date = %s::date
                      AND latitude IS NOT NULL AND longitude IS NOT NULL
                    ORDER BY gps_time ASC
                    LIMIT 500
                """, (device_id, day))
            else:
                cur.execute("""
                    SELECT latitude, longitude, gps_time, device_speed
                    FROM device_latest_position
                    WHERE device_id = %s
                      AND latitude IS NOT NULL AND longitude IS NOT NULL
                    ORDER BY gps_time DESC
                    LIMIT 500
                """, (device_id,))
            positions = [dict(r) for r in cur.fetchall()]
    if not positions:
        return []

    DEDUP = 0.00015
    MAX_JUMP = 0.30
    out, prev = [], None
    for r in positions:
        lat = r.get("latitude")
        lon = r.get("longitude")
        if lat is None or lon is None:
            continue
        lat, lon = float(lat), float(lon)
        if prev:
            dlat = abs(lat - prev[0])
            dlon = abs(lon - prev[1])
            if dlat > MAX_JUMP or dlon > MAX_JUMP:
                continue
            if dlat < DEDUP and dlon < DEDUP:
                continue
        out.append({
            "latitude":  lat,
            "longitude": lon,
            "gpstime":   str(r.get("gps_time") or ""),
            "speed":     float(r.get("device_speed") or 0),
        })
        prev = (lat, lon)
    return out


@app.get("/api/devices/{device_id}/days")
def device_gps_days(device_id: str):
    """Days that have GPS data for this device — for the route date picker."""
    with pg._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    date::text AS date,
                    COUNT(*) AS gps_points
                FROM device_latest_position
                WHERE device_id = %s
                  AND latitude IS NOT NULL AND longitude IS NOT NULL
                GROUP BY date
                ORDER BY date DESC
            """, (device_id,))
            return [dict(r) for r in cur.fetchall()]


@app.get("/api/devices/{device_id}/driver-score")
def driver_score(device_id: str):
    """Driver score profile + per-day severity breakdown."""
    profile = pg.get_driver_profile(device_id)
    daily   = pg.get_driver_daily_scores(device_id)
    return {"profile": profile, "daily": daily}


# ── Additional analytics endpoints ────────────────────────────────────────────

@app.get("/api/fleet/risk-distribution")
def fleet_risk_distribution():
    """Count of devices per risk category."""
    return pg.get_fleet_risk_distribution()


@app.get("/api/fleet/overview")
def fleet_overview():
    """Dashboard overview with score bands, risk distribution, quality stats."""
    return _safe(svc.get_dashboard_overview, fallback={})


@app.get("/api/fleet/advanced-analytics")
def fleet_advanced():
    """Event mix, vehicle mix, top risky/safe, data points trend."""
    return _safe(svc.get_advanced_analytics, fallback={})


@app.get("/api/devices/{device_id}/full-analysis")
def device_full_analysis(device_id: str, days: int = Query(default=30, ge=7, le=90)):
    """Comprehensive device analysis: fleet comparison, severity, activity calendar."""
    return _safe(svc.get_device_full_analysis, device_id, days, fallback={})


@app.get("/api/devices/{device_id}/speed-profile")
def device_speed_profile(device_id: str, limit: int = Query(default=300, ge=50, le=600)):
    """Speed-over-time series with acceleration/deceleration events."""
    return _safe(svc.get_device_speed_profile, device_id, limit, fallback=[])


@app.get("/api/devices/{device_id}/deceleration-spots")
def device_deceleration_spots(device_id: str, limit: int = Query(default=60, ge=1, le=200)):
    """Candidate speed-bump / speed-breaker locations."""
    return _safe(svc.get_device_deceleration_spots, device_id, limit, fallback=[])


@app.get("/api/devices/{device_id}/fleet-hotspots")
def device_fleet_hotspots(device_id: str, days: int = 30, limit: int = 200):
    """Fleet-wide GPS density hotspots near this device's area."""
    return _safe(svc.get_fleet_hotspots_near_device, device_id, days, limit, fallback=[])


@app.get("/api/devices/{device_id}/trips")
def device_trips(device_id: str, limit: int = Query(default=100, ge=1, le=500)):
    """Trip detection — segments based on 30-min gaps."""
    return _safe(svc.get_trip_candidates, device_id, limit, fallback=[])