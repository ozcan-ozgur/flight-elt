with airlines as (

    select * from {{ ref('stg_airlines') }}

),

ranked as (

    select
        *,
        row_number() over (
            partition by icao_code
            order by is_active desc, airline_id
        ) as rn

    from airlines

)

select
    icao_code,
    airline_name,
    iata_code,
    country,
    is_active

from ranked
where rn = 1
