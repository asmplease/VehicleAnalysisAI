import json
import uuid

with open("gps_fleet_command_center_grafana.json", "r", encoding="utf-8") as f:
    dashboard = json.load(f)

max_y = 0
for p in dashboard.get("panels", []):
    y = p.get("gridPos", {}).get("y", 0)
    h = p.get("gridPos", {}).get("h", 0)
    if y + h > max_y:
        max_y = y + h

def next_id():
    ids = [p.get("id", 0) for p in dashboard.get("panels", []) if p.get("id")]
    return max(ids) + 1 if ids else 200

new_panels = [
    {
      "id": next_id(),
      "title": "Advanced Geolocation Insights",
      "type": "row",
      "gridPos": {"h": 1, "w": 24, "x": 0, "y": max_y}
    },
    {
      "id": next_id() + 1,
      "title": "📍 Live Driver Risk Map (Colored by Behavior)",
      "type": "geomap",
      "gridPos": {"h": 14, "w": 12, "x": 0, "y": max_y + 1},
      "description": "Latest position of active vehicles colored by their 30-day average driver score (Red = Bad, Green = Good).",
      "targets": [{
        "datasource": {"type": "postgres"},
        "format": "table",
        "rawQuery": True,
        "rawSql": "SELECT p.device_id, p.latitude, p.longitude, p.device_speed, b.risk_category, COALESCE(b.score_30day_avg, 100) AS driver_score FROM (SELECT DISTINCT ON (device_id) device_id, latitude, longitude, device_speed FROM device_latest_position WHERE $__timeFilter(gps_time) ORDER BY device_id, gps_time DESC) p LEFT JOIN device_behaviour_profile b ON p.device_id = b.device_id;",
        "refId": "A"
      }],
      "fieldConfig": {
        "defaults": {
          "color": {
            "mode": "thresholds"
          },
          "thresholds": {
            "mode": "absolute",
            "steps": [
              {"color": "red", "value": None},
              {"color": "orange", "value": 50},
              {"color": "yellow", "value": 70},
              {"color": "green", "value": 85}
            ]
          }
        },
        "overrides": []
      },
      "options": {
        "basemap": {"config": {}, "name": "Layer 0", "type": "carto"},
        "controls": {"mouseWheelZoom": True, "showAttribution": True, "showScale": True},
        "layers": [
          {
            "config": {
              "color": {"field": "driver_score"},
              "size": {"field": "driver_score", "fixed": 5, "max": 15, "min": 2}
            },
            "location": {"lookup": "latitude", "mode": "coords"},
            "name": "Vehicles",
            "type": "markers"
          }
        ]
      }
    },
    {
      "id": next_id() + 2,
      "title": "🔥 Historical Accuracy Density Hotspots",
      "type": "geomap",
      "gridPos": {"h": 14, "w": 12, "x": 12, "y": max_y + 1},
      "description": "Plots a dense heat map using the aggregated 4-decimal place points analytics table.",
      "targets": [{
        "datasource": {"type": "postgres"},
        "format": "table",
        "rawQuery": True,
        "rawSql": "SELECT latitude_4dp AS latitude, longitude_4dp AS longitude, SUM(points_count) AS intensity FROM gps_points_analytics WHERE $__timeFilter(analytics_date) GROUP BY latitude_4dp, longitude_4dp ORDER BY intensity DESC LIMIT 20000;",
        "refId": "A"
      }],
      "options": {
        "basemap": {"config": {}, "name": "Layer 0", "type": "carto"},
        "controls": {"mouseWheelZoom": True, "showAttribution": True, "showScale": True},
        "layers": [
          {
            "config": {"blur": 15, "radius": 10, "weight": {"field": "intensity", "fixed": 1}},
            "location": {"lookup": "latitude", "mode": "coords"},
            "name": "Activity Density",
            "type": "heatmap"
          }
        ]
      }
    }
]

dashboard["panels"].extend(new_panels)

with open("gps_fleet_command_center_grafana.json", "w", encoding="utf-8") as f:
    json.dump(dashboard, f, indent=2)

print("Added advanced geomaps to Grafana JSON")
