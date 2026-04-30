import json

with open("gps_fleet_command_center_grafana.json", "r", encoding="utf-8") as f:
    dashboard = json.load(f)

# Find max Y currently
max_y = 0
for p in dashboard.get("panels", []):
    y = p.get("gridPos", {}).get("y", 0)
    h = p.get("gridPos", {}).get("h", 0)
    if y + h > max_y:
        max_y = y + h

def next_id():
    ids = [p.get("id", 0) for p in dashboard.get("panels", []) if p.get("id")]
    return max(ids) + 1 if ids else 100

new_panels = [
    {
      "id": next_id(),
      "title": "Fleet Safety Overview",
      "type": "row",
      "gridPos": {"h": 1, "w": 24, "x": 0, "y": max_y}
    },
    {
      "id": next_id() + 1,
      "title": "Average Fleet Score",
      "type": "gauge",
      "gridPos": {"h": 8, "w": 6, "x": 0, "y": max_y + 1},
      "targets": [{
        "datasource": {"type": "postgres"},
        "format": "table",
        "rawQuery": True,
        "rawSql": "SELECT score_30day_avg FROM device_behaviour_profile WHERE score_30day_avg IS NOT NULL;",
        "refId": "A"
      }],
      "fieldConfig": {
        "defaults": {
          "min": 0, "max": 100,
          "color": {"mode": "thresholds"},
          "thresholds": {"mode": "absolute", "steps": [
            {"color": "#dc2626", "value": None},
            {"color": "#ea580c", "value": 50},
            {"color": "#eab308", "value": 70},
            {"color": "#16a34a", "value": 85}
          ]}
        }
      },
      "options": {"reduceOptions": {"values": False, "calcs": ["mean"]}}
    },
    {
      "id": next_id() + 2,
      "title": "Driver Risk Distribution",
      "type": "barchart",
      "gridPos": {"h": 8, "w": 6, "x": 6, "y": max_y + 1},
      "targets": [{
        "datasource": {"type": "postgres"},
        "format": "table",
        "rawQuery": True,
        "rawSql": "SELECT risk_category, COUNT(*) AS count FROM device_behaviour_profile GROUP BY risk_category ORDER BY count DESC;",
        "refId": "A"
      }],
      "options": {"orientation": "horizontal", "legend": {"displayMode": "hidden"}}
    },
    {
      "id": next_id() + 3,
      "title": "Daily Alerts Trend",
      "type": "timeseries",
      "gridPos": {"h": 8, "w": 12, "x": 12, "y": max_y + 1},
      "targets": [{
        "datasource": {"type": "postgres"},
        "format": "time_series",
        "rawQuery": True,
        "rawSql": "SELECT score_date AS time, SUM(total_hb) AS \"Harsh Braking\", SUM(total_ha) AS \"Harsh Acceleration\", SUM(total_rt) AS \"Rash Turning\" FROM driver_daily_scores WHERE $__timeFilter(score_date) GROUP BY score_date ORDER BY score_date;",
        "refId": "A"
      }],
      "options": {
        "legend": {"displayMode": "list", "placement": "bottom"},
        "tooltip": {"mode": "single"}
      },
      "fieldConfig": {
        "defaults": {
           "custom": {
             "drawStyle": "bars",
             "lineInterpolation": "linear",
             "stacking": {"mode": "normal", "group": "A"}
           }
        }
      }
    },
    {
      "id": next_id() + 4,
      "title": "Data Infrastructure",
      "type": "row",
      "gridPos": {"h": 1, "w": 24, "x": 0, "y": max_y + 9}
    },
    {
      "id": next_id() + 5,
      "title": "GPS Points Analyzed over Time",
      "type": "timeseries",
      "gridPos": {"h": 8, "w": 12, "x": 0, "y": max_y + 10},
      "targets": [{
        "datasource": {"type": "postgres"},
        "format": "time_series",
        "rawQuery": True,
        "rawSql": "SELECT analytics_date AS time, SUM(points_count) AS \"Analyzed Points\" FROM gps_points_analytics WHERE $__timeFilter(analytics_date) GROUP BY analytics_date ORDER BY analytics_date;",
        "refId": "A"
      }],
      "fieldConfig": {
        "defaults": {
          "color": {"mode": "palette-classic"},
          "custom": {
            "drawStyle": "line",
            "fillOpacity": 20,
            "lineWidth": 2
          }
        }
      }
    },
    {
      "id": next_id() + 6,
      "title": "Data Received by Day",
      "type": "timeseries",
      "gridPos": {"h": 8, "w": 12, "x": 12, "y": max_y + 10},
      "targets": [{
        "datasource": {"type": "postgres"},
        "format": "time_series",
        "rawQuery": True,
        "rawSql": "SELECT date AS time, SUM(points_received) AS \"Total Points\" FROM daily_metrics WHERE $__timeFilter(date) GROUP BY date ORDER BY date;",
        "refId": "A"
      }]
    }
]

dashboard["panels"].extend(new_panels)

with open("gps_fleet_command_center_grafana.json", "w", encoding="utf-8") as f:
    json.dump(dashboard, f, indent=2)

print("Added Fleet Safety Overview and Data Infrastructure analytics to Grafana.")
