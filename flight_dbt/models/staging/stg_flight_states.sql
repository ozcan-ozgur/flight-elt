with source as (

    select * from {{ source('raw', 'raw_flight_states') }}

),

cleaned as (

    select
        id,
        icao24,
        nullif(trim(callsign), '')            as callsign,
        origin_country,

        to_timestamp(query_time)              as snapshot_at,
        to_timestamp(time_position)           as position_time,
        to_timestamp(last_contact)            as last_contact_time,

        longitude,
        latitude,
        baro_altitude,
        geo_altitude,

        on_ground,
        velocity,
        true_track,
        vertical_rate,

        squawk,
        spi,
        position_source,
        category,

        ingested_at

    from source

)

select * from cleaned

