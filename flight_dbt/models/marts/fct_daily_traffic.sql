{#
    One row per UTC day, for end-of-day reporting.

    Every figure here is built from per-snapshot aggregates first and only then
    reduced to a day, because the obvious daily metrics are not what they look
    like. "Distinct aircraft seen today" counts how many times we looked, not how
    much flew: 23 July has a single snapshot and reports 11,795 aircraft, while
    25 July has 45 snapshots and reports 53,858 — the same sky, sampled more
    often. Distinct airlines and countries are confounded the same way, so none
    of the three is exposed.

    Maxima and means over snapshots survive the problem, since each snapshot is
    a full census of the sky rather than a sample of it. Peak traffic held at
    13,032 / 12,485 / 12,500 across days whose snapshot counts ran 8, 45 and 40.

    Partial days are kept rather than filtered, with coverage_pct alongside so a
    thin day is visible as thin instead of quietly ranking beside a full one.
#}

{% set snapshots_per_day = 48 %}  {# the pipeline runs every 30 minutes #}

with per_snapshot as (

    select
        snapshot_at,
        (snapshot_at at time zone 'UTC')::date as traffic_date,

        count(*) filter (where not on_ground)                       as airborne,
        count(*) filter (where not on_ground and vertical_rate > 2.5)  as climbing,
        count(*) filter (where not on_ground and vertical_rate < -2.5) as descending,

        percentile_cont(0.5) within group (order by baro_altitude)
            filter (where not on_ground)                            as median_altitude,
        percentile_cont(0.5) within group (order by velocity)
            filter (where not on_ground)                            as median_velocity

    from {{ ref('fct_flight_positions') }}
    group by snapshot_at

),

daily as (

    select
        traffic_date,
        count(*)                as snapshots,

        max(airborne)           as peak_airborne,
        min(airborne)           as trough_airborne,
        round(avg(airborne))    as mean_airborne,

        -- array_agg over an ordered set rather than a self-join: one pass, and
        -- it reads as "the snapshot_at belonging to the largest airborne".
        (array_agg(snapshot_at order by airborne desc))[1] as peak_at,
        (array_agg(snapshot_at order by airborne))[1]      as trough_at,

        round(avg(median_altitude))                as median_altitude,
        round(avg(median_velocity)::numeric, 1)    as median_velocity,

        -- Above 1 the sky filled over the day, below 1 it drained.
        round(sum(climbing)::numeric / nullif(sum(descending), 0), 2) as climb_ratio

    from per_snapshot
    group by traffic_date

),

airline_per_snapshot as (

    select
        (snapshot_at at time zone 'UTC')::date as traffic_date,
        snapshot_at,
        airline_name,
        count(distinct icao24) as aircraft

    from {{ ref('fct_flight_positions') }}
    where not on_ground
      and airline_name is not null
    group by 1, 2, 3

),

busiest_airline as (

    -- Ranked on the mean across the day's snapshots, not the daily total. A
    -- total would again reward the days we sampled most.
    select distinct on (traffic_date)
        traffic_date,
        airline_name        as busiest_airline,
        round(avg(aircraft)) as busiest_airline_aircraft

    from airline_per_snapshot
    group by traffic_date, airline_name
    order by traffic_date, avg(aircraft) desc

)

select
    d.traffic_date,
    d.snapshots,
    least(round(100.0 * d.snapshots / {{ snapshots_per_day }}), 100) as coverage_pct,

    d.peak_airborne,
    d.peak_at,
    d.trough_airborne,
    d.trough_at,
    d.mean_airborne,

    b.busiest_airline,
    b.busiest_airline_aircraft,

    d.median_altitude,
    d.median_velocity,
    d.climb_ratio

from daily d
left join busiest_airline b using (traffic_date)
order by d.traffic_date
