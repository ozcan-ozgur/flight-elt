-- Guards the cleaning rules in stg_flight_states: if a garbled ADS-B reading
-- ever reaches the mart, this returns the offending rows and the test fails.

select
    flight_state_id,
    callsign,
    velocity,
    baro_altitude,
    vertical_rate

from {{ ref('fct_flight_positions') }}

where velocity > 400
   or velocity < 0
   or baro_altitude > 16000
   or abs(vertical_rate) > 100
   or latitude not between -90 and 90
   or longitude not between -180 and 180
