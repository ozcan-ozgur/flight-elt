# Flight ELT

An end-to-end ELT pipeline that pulls live aircraft positions from the
[OpenSky Network](https://opensky-network.org/) API into Postgres, transforms
them with dbt, orchestrates everything on a schedule with Dagster, and
visualises the result on an interactive Streamlit map.

```
OpenSky API ──(elt.py)──► raw_flight_states ─┐
                                             ├─(dbt)─► staging ─► marts ─► Streamlit dashboard
          airlines.csv (seed) ───────────────┘
                         ▲
                 Dagster schedules the whole graph every 30 min
```

## What's inside

| Layer | Tech | Purpose |
|---|---|---|
| **Extract / Load** | `elt.py`, SQLAlchemy | Calls `/api/states/all`, writes raw snapshots to `raw_flight_states` |
| **Transform** | dbt (`flight_dbt/`) | `staging` (typing + physical-plausibility cleaning, as views) → `marts` (`dim_airline`, `fct_flight_positions`, `fct_daily_traffic`, as tables) |
| **Orchestration** | Dagster (`flight_dagster/`) | Runs the extract + `dbt build` as assets every 30 minutes |
| **Visualisation** | Streamlit (`dashboard.py`) | Interactive live map (pydeck) + traffic analytics (Altair) |
| **Infra** | Docker Compose | Postgres + Dagster webserver + Dagster daemon |

The full table/column reference lives in [`SCHEMA.md`](SCHEMA.md).

## Prerequisites

- Python 3.12+
- Docker + Docker Compose (for the orchestrated/containerised setup)
- A running Postgres instance (the Compose file provides one)

## Setup

1. **Clone and create a virtualenv**

   ```bash
   python -m venv .venv
   # Windows
   .venv\Scripts\activate
   # macOS/Linux
   source .venv/bin/activate

   pip install -r requirements.txt
   ```

2. **Configure environment variables**

   Copy the template and fill in your own values:

   ```bash
   cp .env.example .env
   ```

   | Variable | Description |
   |---|---|
   | `DB_USER` / `DB_PASSWORD` / `DB_NAME` | Postgres credentials |
   | `DB_HOST` | `localhost` on the host, `postgres` inside Compose |
   | `DB_PORT` | `5434` on the host (mapped), `5432` inside Compose |
   | `DAGSTER_HOME` | Absolute path to `.dagster_home` (local runs only) |

   > `.env` is git-ignored — no secrets are committed. All credentials are read
   > from the environment.

## Running it

### Option A — full stack with Docker Compose (recommended)

Brings up Postgres, the Dagster webserver, and the daemon (which fires the
schedule):

```bash
docker compose up --build
```

- Dagster UI → <http://localhost:3000>
- Postgres → `localhost:5434`

The schedule runs the pipeline every 30 minutes automatically. You can also
trigger `flight_job` manually from the Dagster UI.

### Option B — run the pieces locally

```bash
# 1. One-off extract + load
python elt.py

# 2. Transform (seed once, then build)
cd flight_dbt
dbt seed          # loads airlines.csv (static reference data)
dbt build         # runs + tests all models
cd ..

# 3. Dagster dev server (optional, for scheduling/asset UI)
dagster dev -w workspace.yaml
```

### The dashboard

```bash
streamlit run dashboard.py
```

Opens at <http://localhost:8501> with a live aircraft map coloured by altitude
band, leader lines showing heading/speed, and daily traffic analytics derived
from `fct_daily_traffic`.

## How the data model works

- **Staging** types the raw API payload and nulls out physically implausible
  ADS-B readings (e.g. velocity > 400 m/s) rather than dropping rows.
- **`dim_airline`** is a 1-row-per-ICAO-code airline dimension built from the
  OpenFlights seed.
- **`fct_flight_positions`** is one row per raw observation, left-joined to the
  airline dimension on the callsign prefix (so the airline is often `NULL`).
- **`fct_daily_traffic`** aggregates to one row per UTC day using
  snapshot-based peak/mean metrics — deliberately avoiding distinct counts that
  would be confounded by sampling frequency.

See [`SCHEMA.md`](SCHEMA.md) for every column, test, and the join map.

## Scheduling & API budget

The pipeline runs every 30 minutes (48 runs/day). At ~4 OpenSky credits per
anonymous call that's 192 of the 400 daily credits, leaving headroom for manual
runs. The airline seed is treated as static reference data and is **not**
reloaded each run — refresh it with `dbt seed` when the CSV changes.

## Project layout

```
flight-elt/
├── elt.py                  # OpenSky extract + load into raw_flight_states
├── config.py               # Builds DB_URL from env vars
├── dashboard.py            # Streamlit live map + analytics
├── flight_dbt/             # dbt project (staging + marts models, seed, tests)
├── flight_dagster/         # Dagster assets, job, and schedule
├── deploy/dagster.yaml     # Dagster instance config (baked into the image)
├── Dockerfile              # App image (bakes the dbt manifest)
├── docker-compose.yml      # Postgres + Dagster webserver + daemon
├── workspace.yaml          # Dagster workspace entrypoint
├── requirements.txt
├── SCHEMA.md               # Full data-model reference
└── .env.example            # Environment variable template
```
