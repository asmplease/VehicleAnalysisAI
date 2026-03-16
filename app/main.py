from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import STATIC_DIR, TEMPLATES_DIR
import app.athena_service as athena
import app.pg_service as pg

log = logging.getLogger(__name__)

app = FastAPI(title="VehicleAnalysisAI", version="1.0.0")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _safe_list(fn, *args, **kwargs) -> JSONResponse:
    """Call fn and return its result as JSON; on error return empty list with 200."""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        log.error("Athena error in %s: %s", fn.__name__, exc)
        return []


def _safe_dict(fn, *args, **kwargs) -> JSONResponse:
    """Call fn and return its result as JSON; on error return empty dict with 200."""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        log.error("Athena error in %s: %s", fn.__name__, exc)
        return {}


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


@app.on_event("startup")
async def _prewarm():
    """Kick off cache pre-warm in background so first real request is instant."""
    import threading
    log.info("Server starting — launching cache pre-warm thread")
    threading.Thread(target=athena.prewarm_fleet_caches, daemon=True).start()


# ── Cache management ─────────────────────────────────────────────────────

@app.get("/api/cache/status")
def cache_status():
    """Report current cache sizes and TTLs."""
    return athena.cache_info()


@app.post("/api/cache/clear")
def cache_clear_endpoint():
    """Evict all cached Athena results — next request re-fetches from Athena."""
    import threading
    athena.cache_clear()
    threading.Thread(target=athena.prewarm_fleet_caches, daemon=True).start()
    return {"cleared": True, "message": "Cache cleared and pre-warm re-triggered"}


@app.post("/api/cache/repair")
def cache_repair_endpoint():
    """Trigger stale Glue partition cleanup in the background (on-demand only)."""
    import threading
    threading.Thread(target=athena.repair_alert_table_partitions, daemon=True).start()
    return {"started": True, "message": "Partition repair running in background — check server logs"}


@app.get("/api/debug/alerttypes")
def debug_alerttypes():
    """Diagnostic: show distinct alerttype values in the raw table (March 2026).
    Use this to verify actual alertType naming in the S3/Athena raw data."""
    try:
        df = athena.query_df(
            f"SELECT alerttype, COUNT(*) AS n FROM raw"
            f" WHERE {athena._MARCH} AND alerttype IS NOT NULL"
            f" GROUP BY alerttype ORDER BY n DESC LIMIT 30"
        )
        return df.to_dict(orient="records")
    except Exception as exc:
        return {"error": str(exc)}


# ── Fleet analytics ────────────────────────────────────────────────────────────

@app.get("/api/fleet/summary")
def fleet_summary():
    """KPIs: total alerts, devices, per-type counts, avg/max speed, total GPS points."""
    return _safe_dict(athena.get_fleet_summary)


@app.get("/api/fleet/daily-trend")
def fleet_daily_trend():
    """Per-day alert counts by type for the full March dataset."""
    return _safe_list(athena.get_daily_alert_trend)


@app.get("/api/fleet/hourly-distribution")
def fleet_hourly():
    """Alert counts by hour-of-day across all 3 alert types."""
    return _safe_list(athena.get_hourly_alert_distribution)


@app.get("/api/fleet/speed-distribution")
def fleet_speed_dist(
    alert_type: str = Query(default="harsh_braking",
                            pattern="^(harsh_braking|harsh_acceleration|harsh_cornering)$")
):
    """Speed bucket histogram at moment of alert."""
    return _safe_list(athena.get_speed_distribution, alert_type)


@app.get("/api/fleet/hotspots")
def fleet_hotspots(
    alert_type: str = Query(default="harsh_braking",
                            pattern="^(harsh_braking|harsh_acceleration|harsh_cornering)$"),
    limit: int = Query(default=500, ge=50, le=2000),
):
    """Lat/lon grid hotspots for heatmap overlay."""
    return _safe_list(athena.get_alert_hotspots, alert_type, limit)


@app.get("/api/fleet/top-devices")
def fleet_top_devices(limit: int = Query(default=50, ge=5, le=200)):
    """Top devices ranked by total alert count."""
    return _safe_list(athena.get_top_risky_devices, limit)


@app.get("/api/fleet/safe-drivers")
def fleet_safe_drivers(limit: int = Query(default=25, ge=5, le=100)):
    """Devices with most GPS activity but zero alerts — good drivers."""
    return _safe_list(athena.get_top_safe_drivers, limit)


# ── Device analytics ───────────────────────────────────────────────────────────

@app.get("/api/devices/search")
def device_search(
    q: str = Query(min_length=3),
    limit: int = Query(default=20, ge=1, le=100),
):
    return _safe_list(athena.search_devices, q, limit)


@app.get("/api/devices/{device_id}/summary")
def device_summary(device_id: str):
    """Returns alert summary for a device. Safe drivers return is_safe_driver=true with zero counts."""
    return _safe_dict(athena.get_device_summary, device_id)


@app.get("/api/devices/{device_id}/timeline")
def device_timeline(device_id: str):
    """All alert events newest-first (for table + mini timeline chart)."""
    return _safe_list(athena.get_device_alert_timeline, device_id)


@app.get("/api/devices/{device_id}/daily-alerts")
def device_daily_alerts(device_id: str):
    """Day-by-day alert breakdown for this device."""
    return _safe_list(athena.get_device_daily_alerts, device_id)


@app.get("/api/devices/{device_id}/map")
def device_map(device_id: str, day: int = None):
    """Alert locations for this device, optionally filtered to a single day."""
    return _safe_list(athena.get_device_alert_map, device_id, day)


@app.get("/api/devices/{device_id}/route")
def device_route(device_id: str, day: int = None):
    """Ordered GPS track for a device from the raw table (latest day by default)."""
    return _safe_list(athena.get_device_gps_route, device_id, day)


@app.get("/api/devices/{device_id}/days")
def device_gps_days(device_id: str):
    """Days in March with GPS track data — for the route date picker."""
    return _safe_list(athena.get_device_gps_days, device_id)


@app.get("/api/devices/{device_id}/driver-score")
def driver_score(device_id: str):
    """Driver score profile + per-day severity breakdown from PostgreSQL."""
    profile = pg.get_driver_profile(device_id)
    daily   = pg.get_driver_daily_scores(device_id)
    return {"profile": profile, "daily": daily}


# ── Fleet PostgreSQL analytics ─────────────────────────────────────────────────

@app.get("/api/fleet/risk-distribution")
def fleet_risk_distribution():
    """Count of devices per risk category (Low/Medium/High/Critical) from PostgreSQL."""
    return pg.get_fleet_risk_distribution()
