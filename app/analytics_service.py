from __future__ import annotations

from typing import Any

from app.config import DEFAULT_DAYS, MAX_POSITION_ROWS, MAX_TREND_DAYS
from app.db import get_cursor
from app.schemas import normalize_rows


class AnalyticsService:
    def get_latest_devices(self, limit: int = 12) -> list[dict[str, Any]]:
        safe_limit = max(1, min(limit, 50))
        with get_cursor() as cur:
            cur.execute(
                """
                with latest_scores as (
                    select
                        d.device_id,
                        max(d.score_date) as latest_score_date,
                        max(d.updated_at) as latest_score_update
                    from public.driver_daily_scores d
                    group by d.device_id
                )
                select
                    l.device_id,
                    l.latest_score_date,
                    l.latest_score_update,
                    p.risk_category,
                    p.score_today,
                    p.score_7day_avg,
                    p.last_updated
                from latest_scores l
                left join public.device_behaviour_profile p on p.device_id = l.device_id
                order by l.latest_score_date desc, l.latest_score_update desc nulls last
                limit %s
                """,
                (safe_limit,),
            )
            return normalize_rows(cur.fetchall())

    def get_dashboard_overview(self) -> dict[str, Any]:
        queries = {
            "overview": """
                select
                    (select count(*) from public.driver_daily_scores) as score_rows,
                    (select count(distinct device_id) from public.driver_daily_scores) as scored_devices,
                    (select round(avg(current_score)::numeric, 2) from public.driver_daily_scores) as avg_score,
                    (select count(*) from public.device_behaviour_profile where risk_category = 'Critical') as critical_devices,
                    (select count(*) from public.device_latest_position) as position_rows,
                    (select count(*) from public.device_latest_position where device_speed > 180) as high_speed_rows
            """,
            "score_bands": """
                select
                    case
                        when current_score >= 90 then '90-100'
                        when current_score >= 75 then '75-89'
                        when current_score >= 50 then '50-74'
                        when current_score >= 25 then '25-49'
                        else '0-24'
                    end as band,
                    count(*) as rows
                from public.driver_daily_scores
                group by 1
                order by 1 desc
            """,
            "risk_distribution": """
                select
                    risk_category,
                    count(*) as devices,
                    round(avg(score_today)::numeric, 2) as avg_score_today,
                    round(avg(score_7day_avg)::numeric, 2) as avg_score_7d,
                    round(avg(score_30day_avg)::numeric, 2) as avg_score_30d
                from public.device_behaviour_profile
                group by risk_category
                order by devices desc
            """,
            "quality": """
                select
                    count(*) as rows,
                    count(*) filter (where date < date '2025-01-01' or date > current_date) as invalid_date_rows,
                    count(*) filter (where gps_time < timestamp '2025-01-01' or gps_time > now() + interval '1 day') as invalid_gps_time_rows,
                    count(*) filter (where device_speed > 180) as speed_gt_180_rows,
                    round(avg(device_speed)::numeric, 2) as avg_speed,
                    max(device_speed) as max_speed
                from public.device_latest_position
            """,
        }

        result: dict[str, Any] = {}
        with get_cursor() as cur:
            for name, sql in queries.items():
                cur.execute(sql)
                result[name] = normalize_rows(cur.fetchall())
        return result

    def get_daily_trend(self, days: int = DEFAULT_DAYS) -> list[dict[str, Any]]:
        safe_days = max(1, min(days, MAX_TREND_DAYS))
        with get_cursor() as cur:
            cur.execute(
                """
                select
                    score_date,
                    count(*) as rows,
                    count(distinct device_id) as devices,
                    round(avg(current_score)::numeric, 2) as avg_score,
                    sum(total_hb) as harsh_braking,
                    sum(total_ha) as harsh_acceleration,
                    sum(total_rt) as rash_turning,
                    sum(total_deductions) as total_deductions
                from public.driver_daily_scores
                group by score_date
                order by score_date desc
                limit %s
                """,
                (safe_days,),
            )
            return normalize_rows(cur.fetchall())

    def get_advanced_analytics(self) -> dict[str, Any]:
        queries = {
            "event_mix": """
                select 'Harsh Braking' as event_type, sum(total_hb) as event_count from public.driver_daily_scores
                union all
                select 'Harsh Acceleration' as event_type, sum(total_ha) as event_count from public.driver_daily_scores
                union all
                select 'Rash Turning' as event_type, sum(total_rt) as event_count from public.driver_daily_scores
                order by event_count desc
            """,
            "vehicle_mix": """
                select
                    coalesce(nullif(vehicle_type, ''), 'unknown') as vehicle_type,
                    count(*) as rows,
                    count(distinct device_id) as devices,
                    round(avg(device_speed)::numeric, 2) as avg_speed
                from public.device_latest_position
                where latitude between -90 and 90
                  and longitude between -180 and 180
                  and gps_time between timestamp '2025-01-01' and now() + interval '1 day'
                group by 1
                order by rows desc
                limit 10
            """,
            "top_risky_devices": """
                with latest_scores as (
                    select
                        d.*, 
                        row_number() over (partition by d.device_id order by d.score_date desc, d.updated_at desc) as rn
                    from public.driver_daily_scores d
                )
                select
                    device_id,
                    score_date,
                    current_score,
                    total_hb,
                    total_ha,
                    total_rt,
                    total_deductions
                from latest_scores
                where rn = 1
                order by current_score asc, total_deductions desc
                limit 10
            """,
            "top_safe_devices": """
                with latest_scores as (
                    select
                        d.*, 
                        row_number() over (partition by d.device_id order by d.score_date desc, d.updated_at desc) as rn
                    from public.driver_daily_scores d
                )
                select
                    device_id,
                    score_date,
                    current_score,
                    total_hb,
                    total_ha,
                    total_rt,
                    total_deductions
                from latest_scores
                where rn = 1
                order by current_score desc, total_deductions asc
                limit 10
            """,
            "quality_hotspots": """
                select
                    device_id,
                    count(*) filter (where device_speed > 180) as bad_speed_rows,
                    max(device_speed) as max_speed,
                    max(gps_time) as latest_bad_gps_time
                from public.device_latest_position
                where device_speed > 180
                group by device_id
                order by bad_speed_rows desc, max_speed desc
                limit 15
            """,
            "data_points_trend": """
                select
                    analytics_date,
                    count(distinct device_id) as active_devices,
                    sum(points_count) as total_points
                from public.gps_points_analytics
                group by analytics_date
                order by analytics_date desc
                limit 14
            """,
        }

        result: dict[str, Any] = {}
        with get_cursor() as cur:
            for name, sql in queries.items():
                cur.execute(sql)
                result[name] = normalize_rows(cur.fetchall())
        return result

    def search_devices(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        if not query.strip():
            return []
        normalized_query = query.strip()
        safe_query = f"%{normalized_query}%"
        safe_limit = max(1, min(limit, 100))
        with get_cursor() as cur:
            cur.execute(
                """
                with candidate_devices as (
                    select
                        device_id
                    from public.driver_daily_scores
                    where device_id ilike %s
                    union
                    select device_id
                    from public.device_behaviour_profile
                    where device_id ilike %s
                    union
                    select device_id
                    from public.device_latest_position
                    where device_id ilike %s
                ),
                candidate_scores as (
                    select
                        c.device_id,
                        max(d.score_date) as latest_score_date,
                        max(d.created_at) as last_score_update,
                        (c.device_id = %s) as exact_match
                    from candidate_devices c
                    left join public.driver_daily_scores d on d.device_id = c.device_id
                    group by c.device_id
                )
                select
                    c.device_id,
                    c.exact_match,
                    c.latest_score_date,
                    p.risk_category,
                    p.score_today,
                    p.score_7day_avg,
                    p.score_30day_avg,
                    p.last_updated
                from candidate_scores c
                left join public.device_behaviour_profile p on p.device_id = c.device_id
                order by c.exact_match desc, c.latest_score_date desc nulls last, p.last_updated desc nulls last
                limit %s
                """,
                (safe_query, safe_query, safe_query, normalized_query, safe_limit),
            )
            return normalize_rows(cur.fetchall())

    def get_device_summary(self, device_id: str) -> dict[str, Any]:
        with get_cursor() as cur:
            cur.execute(
                """
                select
                    p.device_id,
                    p.risk_category,
                    p.score_today,
                    p.score_7day_avg,
                    p.score_30day_avg,
                    p.total_alerts_7d,
                    p.total_alerts_30d,
                    p.braking_rate_per_100km,
                    p.accel_rate_per_100km,
                    p.cornering_rate_per_100km,
                    p.last_updated
                from public.device_behaviour_profile p
                where p.device_id = %s
                """,
                (device_id,),
            )
            profile = normalize_rows(cur.fetchall())

            cur.execute(
                """
                select
                    min(score_date) as min_date,
                    max(score_date) as max_date,
                    count(*) as score_days,
                    round(avg(current_score)::numeric, 2) as avg_score,
                    min(current_score) as min_score,
                    max(current_score) as max_score,
                    sum(total_hb) as total_hb,
                    sum(total_ha) as total_ha,
                    sum(total_rt) as total_rt,
                    sum(total_deductions) as total_deductions
                from public.driver_daily_scores
                where device_id = %s
                """,
                (device_id,),
            )
            score_summary = normalize_rows(cur.fetchall())

            cur.execute(
                """
                select
                    max(gps_time) as latest_gps_time,
                    max(date) as latest_date,
                    round(avg(device_speed)::numeric, 2) as avg_speed,
                    max(device_speed) as max_speed,
                    count(*) as rows,
                    count(*) filter (where device_speed > 180) as bad_speed_rows
                from public.device_latest_position
                where device_id = %s
                """,
                (device_id,),
            )
            position_summary = normalize_rows(cur.fetchall())

            return {
                "profile": profile[0] if profile else None,
                "score_summary": score_summary[0] if score_summary else None,
                "position_summary": position_summary[0] if position_summary else None,
            }

    def get_device_full_analysis(self, device_id: str, days: int = 30) -> dict[str, Any]:
        """Comprehensive per-device analysis: daily breakdown, fleet comparison, alert severity, activity."""
        safe_days = max(7, min(days, 90))
        result: dict[str, Any] = {}

        with get_cursor() as cur:
            # ── 1. Daily alert breakdown (all severity columns) ──────────────
            cur.execute(
                """
                select
                    score_date,
                    current_score,
                    total_hb, total_ha, total_rt,
                    hb_critical, hb_high, hb_medium, hb_low,
                    ha_high,    ha_medium,  ha_low,
                    rt_high,    rt_medium,  rt_low,
                    total_deductions
                from public.driver_daily_scores
                where device_id = %s
                order by score_date desc
                limit %s
                """,
                (device_id, safe_days),
            )
            result["daily_history"] = normalize_rows(cur.fetchall())

            # ── 2. Alert severity totals ──────────────────────────────────────
            cur.execute(
                """
                select
                    sum(hb_critical)  as hb_critical,
                    sum(hb_high)      as hb_high,
                    sum(hb_medium)    as hb_medium,
                    sum(hb_low)       as hb_low,
                    sum(ha_high)      as ha_high,
                    sum(ha_medium)    as ha_medium,
                    sum(ha_low)       as ha_low,
                    sum(rt_high)      as rt_high,
                    sum(rt_medium)    as rt_medium,
                    sum(rt_low)       as rt_low,
                    sum(total_hb)     as total_hb,
                    sum(total_ha)     as total_ha,
                    sum(total_rt)     as total_rt,
                    sum(total_deductions) as total_deductions,
                    count(*)          as active_days
                from public.driver_daily_scores
                where device_id = %s
                  and score_date >= current_date - (%s - 1)
                """,
                (device_id, safe_days),
            )
            row = cur.fetchone()
            result["alert_totals"] = normalize_rows([row])[0] if row else {}

            # ── 3. Fleet comparison ─────────────────────────────────────────
            cur.execute(
                """
                with fleet_latest as (
                    select device_id,
                           current_score,
                           total_hb, total_ha, total_rt, total_deductions
                    from public.driver_daily_scores
                    where score_date = (
                        select max(score_date) from public.driver_daily_scores
                    )
                ),
                fleet_stats as (
                    select
                        round(avg(current_score)::numeric,2)       as fleet_avg_score,
                        round(avg(total_deductions)::numeric,2)    as fleet_avg_deductions,
                        percentile_cont(0.5) within group (order by current_score) as fleet_median_score,
                        percentile_cont(0.75) within group (order by current_score) as fleet_p75_score,
                        count(*)                                   as fleet_total_devices
                    from fleet_latest
                ),
                device_stats as (
                    select
                        current_score   as device_score,
                        total_deductions as device_deductions,
                        (select count(*) from fleet_latest f2
                         where f2.current_score < d.current_score)::float
                        / nullif((select count(*) from fleet_latest),0) * 100 as percentile
                    from fleet_latest d
                    where device_id = %s
                )
                select
                    f.fleet_avg_score,
                    f.fleet_avg_deductions,
                    f.fleet_median_score,
                    f.fleet_p75_score,
                    f.fleet_total_devices,
                    d.device_score,
                    d.device_deductions,
                    round(d.percentile::numeric,1) as device_percentile_rank
                from fleet_stats f
                cross join device_stats d
                """,
                (device_id,),
            )
            fleet_row = cur.fetchone()
            result["fleet_comparison"] = normalize_rows([fleet_row])[0] if fleet_row else {}

            # ── 4. Score trend stats ────────────────────────────────────────
            cur.execute(
                """
                select
                    max(current_score) as best_score,
                    min(current_score) as worst_score,
                    round(avg(current_score)::numeric,2) as avg_score,
                    (array_agg(current_score order by score_date asc))[1] as oldest_score,
                    (array_agg(current_score order by score_date desc))[1] as latest_score,
                    count(*) as total_days,
                    sum(case when current_score = 0 then 1 else 0 end) as zero_score_days
                from public.driver_daily_scores
                where device_id = %s
                  and score_date >= current_date - (%s - 1)
                """,
                (device_id, safe_days),
            )
            trend_row = cur.fetchone()
            result["score_stats"] = normalize_rows([trend_row])[0] if trend_row else {}

            # ── 5. Activity calendar (last 30 days, one row per day) ─────────
            cur.execute(
                """
                select
                    score_date,
                    current_score,
                    total_hb + total_ha + total_rt as total_alerts
                from public.driver_daily_scores
                where device_id = %s
                  and score_date >= current_date - 29
                order by score_date asc
                """,
                (device_id,),
            )
            result["activity_calendar"] = normalize_rows(cur.fetchall())

            # ── 6. GPS quality summary for this device ────────────────────
            cur.execute(
                """
                select
                    count(*)                                                     as total_position_rows,
                    count(*) filter (where device_speed > 180)                  as high_speed_rows,
                    count(*) filter (where device_speed > 0 and device_speed <= 180) as valid_speed_rows,
                    round(avg(case when device_speed <= 180 then device_speed end)::numeric,2) as clean_avg_speed,
                    max(case when device_speed <= 180 then device_speed end)     as clean_max_speed,
                    count(*) filter (
                        where gps_time < timestamp '2025-01-01'
                           or gps_time > now() + interval '1 day'
                    ) as invalid_timestamp_rows,
                    min(case when gps_time between timestamp '2025-01-01'
                                              and now() + interval '1 day'
                             then gps_time end) as earliest_valid_gps,
                    max(case when gps_time between timestamp '2025-01-01'
                                              and now() + interval '1 day'
                             then gps_time end) as latest_valid_gps
                from public.device_latest_position
                where device_id = %s
                """,
                (device_id,),
            )
            gps_row = cur.fetchone()
            result["gps_quality"] = normalize_rows([gps_row])[0] if gps_row else {}

        return result

    def get_device_daily_scores(self, device_id: str, days: int = 30) -> list[dict[str, Any]]:
        safe_days = max(1, min(days, 120))
        with get_cursor() as cur:
            cur.execute(
                """
                select
                    score_date,
                    current_score,
                    total_hb,
                    total_ha,
                    total_rt,
                    total_deductions,
                    hb_critical,
                    hb_high,
                    hb_medium,
                    hb_low,
                    ha_high,
                    ha_medium,
                    ha_low,
                    rt_high,
                    rt_medium,
                    rt_low
                from public.driver_daily_scores
                where device_id = %s
                order by score_date desc
                limit %s
                """,
                (device_id, safe_days),
            )
            return normalize_rows(cur.fetchall())

    def get_device_positions(self, device_id: str, limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = max(1, min(limit, MAX_POSITION_ROWS))
        with get_cursor() as cur:
            cur.execute(
                """
                select
                    date,
                    gps_time,
                    latitude,
                    longitude,
                    device_speed,
                    orientation,
                    vehicle_type,
                    updated_at
                from public.device_latest_position
                where device_id = %s
                  and latitude between -90 and 90
                  and longitude between -180 and 180
                order by gps_time desc nulls last
                limit %s
                """,
                (device_id, safe_limit),
            )
            return normalize_rows(cur.fetchall())

    def get_trip_candidates(self, device_id: str, limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = max(1, min(limit, 500))
        with get_cursor() as cur:
            cur.execute(
                """
                with ordered as (
                    select
                        device_id,
                        gps_time,
                        date,
                        latitude,
                        longitude,
                        device_speed,
                        lag(gps_time) over (partition by device_id order by gps_time) as prev_gps_time,
                        lag(latitude) over (partition by device_id order by gps_time) as prev_latitude,
                        lag(longitude) over (partition by device_id order by gps_time) as prev_longitude
                    from public.device_latest_position
                    where device_id = %s
                      and gps_time between timestamp '2025-01-01' and now() + interval '1 day'
                      and device_speed between 0 and 180
                ),
                segmented as (
                    select
                        device_id,
                        date,
                        gps_time,
                        latitude,
                        longitude,
                        device_speed,
                        case
                            when prev_gps_time is null then 1
                            when gps_time - prev_gps_time > interval '30 minutes' then 1
                            else 0
                        end as new_trip_flag
                    from ordered
                ),
                tagged as (
                    select
                        *,
                        sum(new_trip_flag) over (partition by device_id order by gps_time rows unbounded preceding) as trip_id
                    from segmented
                )
                select
                    trip_id,
                    min(gps_time) as trip_start,
                    max(gps_time) as trip_end,
                    count(*) as point_count,
                    round(avg(device_speed)::numeric, 2) as avg_speed,
                    max(device_speed) as max_speed,
                    min(latitude) as min_latitude,
                    max(latitude) as max_latitude,
                    min(longitude) as min_longitude,
                    max(longitude) as max_longitude
                from tagged
                group by trip_id
                order by trip_start desc
                limit %s
                """,
                (device_id, safe_limit),
            )
            return normalize_rows(cur.fetchall())

    def get_health(self) -> dict[str, Any]:
        with get_cursor() as cur:
            cur.execute(
                "select current_database() as database_name, current_user as username, now() as checked_at"
            )
            return normalize_rows(cur.fetchall())[0]
