# VehicleAnalysisAI

Local, read-only drive analytics exploration against PostgreSQL.

## Safety

This workspace is configured for **read-only analysis**.
The scripts here only run `SELECT` statements and generate local files.
No script performs `INSERT`, `UPDATE`, `DELETE`, `CREATE`, `ALTER`, or `DROP` on the database.

## Environment

Virtual environment:
- `.venv`

Install dependencies:
- [requirements.txt](requirements.txt)

## Available scripts

- [db_overview.py](db_overview.py): schema inventory and sample rows
- [db_kpis.py](db_kpis.py): KPI snapshot on main driving tables
- [db_quality.py](db_quality.py): data quality checks
- [drive_analytics_report.py](drive_analytics_report.py): consolidated read-only analytics report
- [run_dashboard.py](run_dashboard.py): FastAPI app entrypoint for dashboard and API

## Application

Built components:

- Read-only FastAPI backend
- Dashboard UI served from the same app
- Device search and device-level drill-down
- Daily trend, risk distribution, and telemetry quality views
- Event mix, vehicle mix, and telemetry hotspot leaderboards
- Trip-candidate extraction based on GPS time gaps

## Run

Set `PG_DSN` and run:

- `.\.venv\Scripts\python.exe .\drive_analytics_report.py`
- `.\.venv\Scripts\python.exe -m uvicorn run_dashboard:app --reload`

Then open:

- `http://127.0.0.1:8000/`
- `http://127.0.0.1:8000/docs`

## API endpoints

- `GET /api/health`
- `GET /api/overview`
- `GET /api/analytics/advanced`
- `GET /api/trends/daily?days=14`
- `GET /api/devices/search?q=860560`
- `GET /api/devices/{device_id}`
- `GET /api/devices/{device_id}/scores?days=30`
- `GET /api/devices/{device_id}/positions?limit=100`
- `GET /api/devices/{device_id}/trip-candidates?limit=50`

## Outputs

Generated locally under [artifacts](artifacts):
- [artifacts/drive_analytics_report.json](artifacts/drive_analytics_report.json)
- [artifacts/drive_analytics_report.md](artifacts/drive_analytics_report.md)

## Current findings

- Main analytics-ready sources are `driver_daily_scores`, `device_behaviour_profile`, and `device_latest_position`.
- `driver_daily_scores` already contains event counts and daily score outputs.
- `device_latest_position` contains data quality issues, especially invalid dates/timestamps and impossible speed values.
- Trip analytics should be built only after filtering those rows.

## Notes

- The app does not write to PostgreSQL.
- Each database session is marked read-only before queries run.
