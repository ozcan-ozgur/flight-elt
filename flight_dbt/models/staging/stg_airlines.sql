with source as (

    select * from {{ ref('airlines') }}

),

cleaned as (

    select
        airline_id,
        nullif(nullif(name, '\N'), '')      as airline_name,
        nullif(nullif(iata, '\N'), '')      as iata_code,
        nullif(nullif(icao, '\N'), '')      as icao_code,
        nullif(nullif(country, '\N'), '')   as country,
        active = 'Y'                        as is_active

    from source

)

select * from cleaned
where icao_code is not null
  and icao_code <> 'N/A'
