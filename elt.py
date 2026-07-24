import requests
from sqlalchemy import create_engine, text
from config import DB_URL
import json

URL = "https://opensky-network.org/api/states/all?extended=1"
from sqlalchemy import create_engine, text
from config import DB_URL

def get_states():
    response = requests.get(URL, timeout=30)
    response.raise_for_status()
    data = response.json()
    return data['time'], data['states']


FIELDS = [
    "icao24", "callsign", "origin_country", "time_position",
    "last_contact", "longitude", "latitude", "baro_altitude",
    "on_ground", "velocity", "true_track", "vertical_rate",
    "sensors", "geo_altitude", "squawk", "spi",
    "position_source", "category"
]

CREATE_SQL = text("""
CREATE TABLE IF NOT EXISTS raw_flight_states (
    id SERIAL PRIMARY KEY,
    query_time BIGINT,
    icao24 TEXT,
    callsign TEXT,
    origin_country TEXT,
    time_position BIGINT,
    last_contact BIGINT,
    longitude DOUBLE PRECISION,
    latitude DOUBLE PRECISION,
    baro_altitude DOUBLE PRECISION,
    on_ground BOOLEAN,
    velocity DOUBLE PRECISION,
    true_track DOUBLE PRECISION,
    vertical_rate DOUBLE PRECISION,
    sensors TEXT,
    geo_altitude DOUBLE PRECISION,
    squawk TEXT,
    spi BOOLEAN,
    position_source INTEGER,
    category INTEGER,
    ingested_at TIMESTAMP DEFAULT now()
)
""")

INSERT_SQL = text("""
INSERT INTO raw_flight_states (
    query_time, icao24, callsign, origin_country, time_position,
    last_contact, longitude, latitude, baro_altitude, on_ground,
    velocity, true_track, vertical_rate, sensors, geo_altitude,
    squawk, spi, position_source, category
) VALUES (
    :query_time, :icao24, :callsign, :origin_country, :time_position,
    :last_contact, :longitude, :latitude, :baro_altitude, :on_ground,
    :velocity, :true_track, :vertical_rate, :sensors, :geo_altitude,
    :squawk, :spi, :position_source, :category
)
""")


def load(query_time, states):
    rows = []
    for state in states:
        row = dict(zip(FIELDS, state))
        row["sensors"] = json.dumps(row["sensors"]) if row["sensors"] is not None else None
        row["query_time"] = query_time
        rows.append(row)

    engine = create_engine(DB_URL)
    with engine.begin() as conn:
        conn.execute(CREATE_SQL)
        conn.execute(INSERT_SQL, rows)

    print(f"{len(rows)} rows inserted.")


if __name__ == '__main__':
    query_time, states = get_states()
    load(query_time, states)

