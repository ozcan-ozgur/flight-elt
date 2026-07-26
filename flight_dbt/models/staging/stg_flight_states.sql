{#
    OpenSky publishes raw receiver output, so a small share of rows carry
    garbled ADS-B values: aircraft "cruising" at Mach 5, altitudes above the
    stratosphere, climb rates no airframe can sustain. Roughly 0.05% of rows.

    Implausible readings are nulled rather than dropped: the observation itself
    is real, only that one field is untrustworthy. Thresholds sit well above
    anything civil aviation reaches, so genuine outliers survive.
#}

{% set max_velocity_ms = 400 %}      {# 1,440 km/h — no airliner comes close #}
{% set max_altitude_m = 16000 %}     {# above every civil service ceiling #}
{% set max_vertical_rate_ms = 100 %} {# ~20,000 ft/min #}

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

        case when longitude between -180 and 180 then longitude end as longitude,
        case when latitude between -90 and 90 then latitude end     as latitude,

        case when baro_altitude <= {{ max_altitude_m }} then baro_altitude end as baro_altitude,
        case when geo_altitude <= {{ max_altitude_m }} then geo_altitude end   as geo_altitude,

        on_ground,

        case when velocity between 0 and {{ max_velocity_ms }} then velocity end as velocity,
        case when true_track between 0 and 360 then true_track end               as true_track,
        case
            when abs(vertical_rate) <= {{ max_vertical_rate_ms }} then vertical_rate
        end                                                                      as vertical_rate,

        squawk,
        spi,
        position_source,
        category,

        ingested_at

    from source

)

select * from cleaned
