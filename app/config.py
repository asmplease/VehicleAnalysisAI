from __future__ import annotations

import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BASE_DIR / "app" / "templates"
STATIC_DIR = BASE_DIR / "app" / "static"
ARTIFACTS_DIR = BASE_DIR / "artifacts"

PG_DSN = os.environ.get("PG_DSN", "")
DEFAULT_DAYS = int(os.environ.get("DRIVE_ANALYTICS_DEFAULT_DAYS", "14"))
MAX_TREND_DAYS = int(os.environ.get("DRIVE_ANALYTICS_MAX_TREND_DAYS", "60"))
MAX_POSITION_ROWS = int(os.environ.get("DRIVE_ANALYTICS_MAX_POSITION_ROWS", "500"))
