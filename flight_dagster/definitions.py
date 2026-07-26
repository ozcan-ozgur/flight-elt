from pathlib import Path

from dagster import (
    AssetExecutionContext,
    AssetKey,
    AssetSelection,
    DefaultScheduleStatus,
    Definitions,
    ScheduleDefinition,
    asset,
    define_asset_job,
)
from dagster_dbt import (
    DbtCliResource,
    DbtProject,
    dbt_assets,
    get_asset_key_for_source,
)

import elt

DBT_PROJECT_DIR = Path(__file__).joinpath("..", "..", "flight_dbt").resolve()

dbt_project = DbtProject(
    project_dir=DBT_PROJECT_DIR,
    profiles_dir=DBT_PROJECT_DIR,
)
dbt_project.prepare_if_dev()


@dbt_assets(manifest=dbt_project.manifest_path)
def flight_dbt_assets(context: AssetExecutionContext, dbt: DbtCliResource):
    yield from dbt.cli(["build"], context=context).stream()


@asset(
    key=get_asset_key_for_source([flight_dbt_assets], "raw"),
    compute_kind="python",
)
def raw_flight_states(context: AssetExecutionContext) -> None:
    query_time, states = elt.get_states()
    elt.load(query_time, states)
    context.add_output_metadata({"rows": len(states), "query_time": query_time})


# The airline seed is static reference data — reloading it every run costs 25s
# for no benefit. Refresh it manually with `dbt seed` when the CSV changes.
AIRLINE_SEED = AssetKey("airlines")

flight_job = define_asset_job(
    "flight_job",
    selection=AssetSelection.all() - AssetSelection.assets(AIRLINE_SEED),
)

# Every 30 minutes: 48 runs/day x 4 credits = 192 of OpenSky's 400 anonymous
# daily credits, leaving headroom for manual runs.
flight_schedule = ScheduleDefinition(
    job=flight_job,
    cron_schedule="*/30 * * * *",
    default_status=DefaultScheduleStatus.RUNNING,
)

defs = Definitions(
    assets=[raw_flight_states, flight_dbt_assets],
    schedules=[flight_schedule],
    resources={"dbt": DbtCliResource(project_dir=dbt_project)},
)