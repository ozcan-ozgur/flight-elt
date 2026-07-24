with flights as (

    select * from {{ ref('stg_flight_states') }}

),

airlines as (

    select * from {{ ref('dim_airline') }}

),

joined as (

    select
        f.id                        as flight_state_id,
        f.snapshot_at,
        f.icao24,
        f.callsign,
        left(f.callsign, 3)         as callsign_prefix,

        a.icao_code                 as airline_icao,
        a.airline_name,
        a.country                   as airline_country,
        f.origin_country            as registration_country,

        f.latitude,
        f.longitude,
        f.baro_altitude,
        f.velocity,
        f.true_track,
        f.vertical_rate,
        f.on_ground,
        f.position_time

    from flights f
    left join airlines a
        on left(f.callsign, 3) = a.icao_code

)

select * from joined
