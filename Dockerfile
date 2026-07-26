FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DAGSTER_HOME=/opt/dagster_home

WORKDIR /opt/flight

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Bake the dbt manifest into the image. `dbt parse` never opens a connection,
# so placeholder credentials are enough to resolve the profile.
RUN DB_USER=parse DB_PASSWORD=parse DB_HOST=parse DB_PORT=5432 DB_NAME=parse \
    dbt parse --project-dir flight_dbt --profiles-dir flight_dbt

RUN mkdir -p "$DAGSTER_HOME"
COPY deploy/dagster.yaml "$DAGSTER_HOME/dagster.yaml"
