from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.analytics_service import AnalyticsService
from app.config import STATIC_DIR, TEMPLATES_DIR


app = FastAPI(title="VehicleAnalysisAI", version="0.1.0")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
service = AnalyticsService()


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"title": "VehicleAnalysisAI Dashboard"},
    )

@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    return Response(status_code=204)


@app.get("/api/health")
def health() -> dict:
    return service.get_health()


@app.get("/api/overview")
def overview() -> dict:
    return service.get_dashboard_overview()


@app.get("/api/devices/latest")
def latest_devices(limit: int = Query(default=12, ge=1, le=50)) -> list[dict]:
    return service.get_latest_devices(limit)


@app.get("/api/trends/daily")
def daily_trend(days: int = Query(default=14, ge=1, le=60)) -> list[dict]:
    return service.get_daily_trend(days)


@app.get("/api/analytics/advanced")
def advanced_analytics() -> dict:
    return service.get_advanced_analytics()


@app.get("/api/devices/search")
def search_devices(q: str = Query(min_length=3), limit: int = Query(default=20, ge=1, le=100)) -> list[dict]:
    return service.search_devices(q, limit)


@app.get("/api/devices/{device_id}")
def device_summary(device_id: str) -> dict:
    result = service.get_device_summary(device_id)
    if not result["profile"] and not result["score_summary"]:
        raise HTTPException(status_code=404, detail="Device not found")
    return result


@app.get("/api/devices/{device_id}/full-analysis")
def device_full_analysis(device_id: str, days: int = Query(default=30, ge=7, le=90)) -> dict:
    result = service.get_device_full_analysis(device_id, days)
    return result


@app.get("/api/devices/{device_id}/scores")
def device_scores(device_id: str, days: int = Query(default=30, ge=1, le=120)) -> list[dict]:
    return service.get_device_daily_scores(device_id, days)


@app.get("/api/devices/{device_id}/positions")
def device_positions(device_id: str, limit: int = Query(default=100, ge=1, le=500)) -> list[dict]:
    return service.get_device_positions(device_id, limit)


@app.get("/api/devices/{device_id}/trip-candidates")
def trip_candidates(device_id: str, limit: int = Query(default=50, ge=1, le=500)) -> list[dict]:
    return service.get_trip_candidates(device_id, limit)
