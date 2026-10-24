# Flight ELT — Database Schema

An ELT pipeline that pulls aircraft position data from the OpenSky Network API
into Postgres and transforms it through dbt staging/marts layers. This file
summarises the full table/column structure in one place — useful when writing
queries in Metabase (or when giving another tool context about the model).

Layer flow: `raw_flight_states` (raw, written by `elt.py`) + `airlines` seed →
`stg_flight_states` / `stg_airlines` (staging, typing + cleaning) →
`dim_airline` / `fct_flight_positions` / `fct_daily_traffic` (marts, the final
tables used for BI). Materialization: staging = view, marts = table
(see `flight_dbt/dbt_project.yml`).

---

## 1. `raw_flight_states` (raw table, Postgres `public` schema)

Populated by `elt.py` from the OpenSky `/api/states/all` response. Managed
outside dbt (referenced as `source('raw','raw_flight_states')` in
`_sources.yml`).

| Column | Type | Description |
|---|---|---|
| id | SERIAL PK | |
| query_time | BIGINT | Epoch time of the API call (becomes snapshot_at) |
| icao24 | TEXT | Aircraft transponder address |
| callsign | TEXT | Raw callsign (may be padded/empty) |
| origin_country | TEXT | Registered country |
| time_position | BIGINT | Position epoch time |
| last_contact | BIGINT | Last-contact epoch time |
| longitude, latitude | DOUBLE PRECISION | |
| baro_altitude, geo_altitude | DOUBLE PRECISION | metres |
| on_ground | BOOLEAN | |
| velocity | DOUBLE PRECISION | m/s |
| true_track | DOUBLE PRECISION | degrees |
| vertical_rate | DOUBLE PRECISION | m/s |
| sensors | TEXT | |
| squawk | TEXT | |
| spi | BOOLEAN | |
| position_source | INTEGER | |
| category | INTEGER | |
| ingested_at | TIMESTAMP | DEFAULT now() |

## 2. `airlines` (dbt seed — `flight_dbt/seeds/airlines.csv`)

OpenFlights airline list. Columns: `airline_id, name, alias, iata, icao,
callsign, country, active`. `\N` and `""` values mean null (stg_airlines
cleans these up).

---

## Staging layer (`flight_dbt/models/staging/`)

### `stg_flight_states`
Source: `raw_flight_states`. Applies typing + physically-implausible value
cleaning (the ~0.05% of rows with bad ADS-B readings are **nulled out, not
dropped**): values above the thresholds `velocity > 400 m/s`,
`altitude > 16000 m`, and `|vertical_rate| > 100 m/s` become null.

| Column | Source/Note |
|---|---|
| id | not_null, unique |
| icao24 | not_null |
| callsign | trimmed, null if empty |
| origin_country | |
| snapshot_at | `to_timestamp(query_time)`, not_null |
| position_time | `to_timestamp(time_position)` |
| last_contact_time | `to_timestamp(last_contact)` |
| longitude, latitude | null if outside -180..180 / -90..90 |
| baro_altitude, geo_altitude | null if above threshold |
| on_ground | |
| velocity | null if outside 0..400 |
| true_track | null if outside 0..360 |
| vertical_rate | null if \|x\| > 100 |
| squawk, spi, position_source, category, ingested_at | as-is |

### `stg_airlines`
Source: `airlines` seed. Cleans `\N`/empty values, casts `is_active` to bool,
and drops rows whose `icao_code` is null or `'N/A'`.

Columns: `airline_id, airline_name, iata_code, icao_code, country, is_active`

---

## Marts layer (`flight_dbt/models/marts/`) — the tables Metabase sees

### `dim_airline`
Grain: **1 row / icao_code**. Deduplicates when the same icao_code arrives with
multiple records in `stg_airlines` (prioritising `is_active`, then
`airline_id`).

| Column | Test |
|---|---|
| icao_code | not_null, unique — join key |
| airline_name | |
| iata_code | |
| country | |
| is_active | |

### `fct_flight_positions`
Grain: **1 row / raw flight_state observation** (1:1 with
`stg_flight_states`). Left-joins `stg_flight_states` to `dim_airline` on
`left(callsign,3) = icao_code` — so `airline_icao` and `airline_name` are
often **null** (no callsign / no match).

| Column | Description |
|---|---|
| flight_state_id | not_null, unique — `stg_flight_states.id` |
| snapshot_at | API call time |
| icao24 | |
| callsign | |
| callsign_prefix | `left(callsign,3)` — airline guess |
| airline_icao | FK → `dim_airline.icao_code` (nullable, has relationships test) |
| airline_name | |
| airline_country | dim_airline.country |
| registration_country | stg_flight_states.origin_country |
| latitude, longitude | |
| baro_altitude | |
| velocity | |
| true_track | |
| vertical_rate | |
| on_ground | |
| position_time | |

### `fct_daily_traffic`
Grain: **1 row / UTC day**. The pipeline runs every 30 minutes → 48 snapshots
expected per day (`snapshots_per_day = 48`). Index: `snapshot_at`.

Important design note: "distinct aircraft/airline/country counts per day" are
deliberately **omitted** — that metric is confounded with the number of
snapshots (more sampling = artificially higher count). Snapshot-based max/mean
values are used instead.

| Column | Description |
|---|---|
| traffic_date | UTC day (can be thought of as the PK) |
| snapshots | How many snapshots were taken that day |
| coverage_pct | `least(100*snapshots/48, 100)` — how much of the day was covered |
| peak_airborne / peak_at | Most aircraft airborne & the snapshot time of it |
| trough_airborne / trough_at | Fewest aircraft & its time |
| mean_airborne | Average airborne aircraft per snapshot |
| busiest_airline / busiest_airline_aircraft | Busiest airline on average during the day |
| median_altitude | Average of per-snapshot medians |
| median_velocity | Same approach |
| climb_ratio | `sum(climbing)/sum(descending)` — >1 the sky is filling, <1 emptying |

---

## Relationships / join map for Metabase

```
dim_airline.icao_code  <──  fct_flight_positions.airline_icao   (left join, nullable)
fct_flight_positions   ──►  fct_daily_traffic  (fct_daily_traffic is derived from
                                                 fct_flight_positions by aggregating on
                                                 snapshot_at, not a direct FK)
stg_flight_states.id   ==   fct_flight_positions.flight_state_id
```

- For daily/summary reporting, use **`fct_daily_traffic`** directly.
- For aircraft/airline-level detail or map views, use **`fct_flight_positions`**
  (joining `dim_airline` if needed).
- Since `airline_icao`/`airline_name` are frequently null, account for that in
  Metabase filters/aggregations (e.g. you may want an "unknown airline"
  category).
