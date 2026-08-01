import altair as alt
import numpy as np
import pandas as pd
import pydeck as pdk
import streamlit as st

from config import DB_URL

st.set_page_config(
    page_title="Flight tracker",
    page_icon=":material/flight:",
    layout="wide",
)

# The server reaps sessions that sit idle in a transaction, so the pool has to
# assume a checked-out connection may already be gone: pre_ping tests it and
# reconnects rather than handing out a dead one, and recycle retires them before
# they get old enough to be reaped mid-page.
conn = st.connection("sql", url=DB_URL, pool_pre_ping=True, pool_recycle=300)

# Instrument-panel palette, shared with the seismic-monitor project.
AMBER = "#f0883e"

# A day below this share of its 48 expected snapshots is reported, but flagged,
# and never used as the baseline for a day-on-day comparison.
FULL_DAY_COVERAGE = 70
MUTED = "#6e7681"

# Sequential low → high, taken from the seismic magnitude scale.
ALTITUDE_BANDS = (
    (3000, [242, 204, 96], "yellow", "Below 3,000 m"),
    (8000, [240, 136, 62], "orange", "3,000 – 8,000 m"),
    (float("inf"), [207, 62, 46], "red", "Above 8,000 m"),
)
UNKNOWN_COLOR = [110, 118, 129]

# Aircraft dots are sized in metres, so they scale with the map rather than the
# screen. In pixel units a dot stays the same size at every zoom, which means the
# world view spends the same ink on 13,000 aircraft that a city view spends on a
# dozen — at zoom 0.9 that came to more than twice the viewport's own area in
# overlapping marks, and the continents filled in solid.
#
# The two clamps pin the ends of the curve: the widest views sit on the floor and
# anything closer than a country sits on the ceiling. Only the ramp between them
# is left to the radius, which is why that number tunes the middle zooms without
# disturbing either end. At 15 km: ~1px worldwide, ~1.2px over Europe, 4px from
# zoom 4.4 in.
AIRCRAFT_RADIUS_M = 15_000
AIRCRAFT_MIN_PX = 1
AIRCRAFT_MAX_PX = 4

# How far ahead a leader line reaches, in seconds of flight. Being a geographic
# length it inherits the same zoom behaviour as the dots — a cruising jet draws
# about 1.4px worldwide and roughly 5px over Europe — so raising this thickens
# the widest view, which is the one most easily spoiled.
LEADER_SECONDS = 240

# Speed/altitude panel geometry. The width and height are fixed rather than
# stretched because the hexagons are placed in pixel space: tessellation only
# holds if the data→pixel mapping is known here, and a responsive width would
# slide the columns out of register. Domains are fixed for the same reason —
# derived ones would resize the grid whenever the snapshot changed. Values
# outside them are clamped into the edge bins rather than dropped; only 0.17% of
# aircraft sit above 14,000 m, and none has ever cleared the 400 m/s that
# staging caps velocity at.
ENVELOPE_W, ENVELOPE_H = 520, 380
ENVELOPE_SPEED = (0, 360)
ENVELOPE_ALTITUDE = (0, 14_000)

# Circumradius of the binning lattice. Marks are drawn a little smaller so a
# hairline of surface shows between neighbours — the gap is what separates them,
# not a stroke, which would add ink that carries no data.
HEX_RADIUS_PX = 8
HEX_DRAW_RADIUS_PX = 7.2

# One ramp for "how many aircraft", wherever that gets drawn — the map's density
# mode and the speed/altitude bins both read from it, so amber means the same
# thing in both places. deck.gl's stock heatmap range is a blue→green→yellow→red
# rainbow, which reads as four categories rather than one rising quantity; this
# is a single hue instead. It runs dim → bright rather than light → dark because
# both surfaces are dark, so "near zero" is what recedes into the background.
DENSITY_RAMP_HEX = ["#3d210f", "#7a3e18", "#b05a22", "#d8742d", "#f0883e", "#f7b77a"]
DENSITY_RAMP = [[int(step[i : i + 2], 16) for i in (1, 3, 5)] for step in DENSITY_RAMP_HEX]

# The map's three altitude bands, reused as the envelope's density ramp so the
# page carries one warm scale rather than two that nearly match. Taken from
# ALTITUDE_BANDS rather than restated, because a ramp copied by hand is a ramp
# that drifts.
#
# Yellow→amber→red is a multi-hue sequential, which is normally wrong; it is
# allowed here because the hues are analogous neighbours read as heat and the
# panel ships a scale legend. The check that matters for a sequential ramp is
# lightness monotonicity, and this clears it: OKLCH L 0.857 → 0.727 → 0.574, in
# near-even steps, with the darkest still at 3.6 contrast on the panel.
ENVELOPE_RAMP = ["#%02x%02x%02x" % tuple(rgb) for _, rgb, _, _ in ALTITUDE_BANDS]

# Rough bounding boxes: (min_lat, max_lat, min_lon, max_lon, zoom)
REGIONS = {
    "Worldwide": None,
    "Europe": (34.0, 72.0, -25.0, 45.0, 2.6),
    "Americas": (-56.0, 72.0, -170.0, -30.0, 1.8),
}

# Categorical palette (fixed order — a color always names the same airline,
# never a rank). The trend charts cap at 8 lines: past that, adjacent hues stop
# being colorblind-distinguishable.
TREND_COLORS = [
    "#3987e5",  # blue
    "#d95926",  # orange
    "#199e70",  # aqua
    "#c98500",  # yellow
    "#d55181",  # magenta
    "#008300",  # green
    "#9085e9",  # violet
    "#e66767",  # red
]

# The regional trend reuses the first two categorical slots. Sharing them with
# the airline chart is fine — the dimension differs, and each chart's legend
# names its own entities.
TREND_REGIONS = [name for name, box in REGIONS.items() if box]

# The two trend charts are fixed sets rather than filter-driven, so each always
# shows the same eight carriers and the sidebar cannot empty either one.
EU_AIRLINES = [
    "Ryanair",
    "Turkish Airlines",
    "easyJet",
    "Lufthansa",
    "Air France",
    "Vueling Airlines",
    "Pegasus Airlines",
    "Scandinavian Airlines System",
]

US_AIRLINES = [
    "American Airlines",
    "United Airlines",
    "Delta Air Lines",
    "Alaska Airlines",
    "Southwest Airlines",
    "Gol Transportes Aéreos",
    "Azul",
    "JetBlue Airways",
]

def trend_chart(
    data: pd.DataFrame,
    field: str,
    legend: str,
    domain: list[str],
    y_max: float | None = None,
    compact: bool = False,
) -> alt.Chart:
    """Aircraft-over-time line per category, colored from the fixed palette.

    `y_max` pins the axis so two of these can be read against each other: side by
    side on their own scales, a 399-aircraft peak would be drawn exactly as tall
    as a 484-aircraft one. `compact` moves the legend under the plot, where a
    half-width panel has room to spare, instead of beside it where it does not.
    """
    return (
        alt.Chart(data)
        .mark_line(strokeWidth=2)
        .encode(
            x=alt.X("snapshot_at:T", title="Snapshot"),
            y=alt.Y(
                "aircraft:Q",
                title="Aircraft airborne",
                scale=alt.Scale(domain=(0, y_max)) if y_max else alt.Undefined,
            ),
            color=alt.Color(
                f"{field}:N",
                title=legend,
                scale=alt.Scale(domain=domain, range=TREND_COLORS[: len(domain)]),
                legend=(
                    alt.Legend(orient="bottom", direction="vertical", columns=2, labelLimit=170)
                    if compact
                    else alt.Undefined
                ),
            ),
            tooltip=[
                alt.Tooltip("snapshot_at:T", title="Snapshot", format="%d %b, %H:%M"),
                alt.Tooltip(f"{field}:N", title=legend),
                alt.Tooltip("aircraft:Q", title="Aircraft"),
            ],
        )
        .properties(height=280)
    )


# Pointy-top unit hexagon, vertices every 60° starting at the top. Vega scales a
# custom shape path to whatever area `size` asks for, so the path only has to
# carry the proportions.
HEX_PATH = "M0,-1L0.866,-0.5L0.866,0.5L0,1L-0.866,0.5L-0.866,-0.5Z"


def hex_bin(speed: np.ndarray, altitude: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Snap points to a hexagonal lattice, returning bin centres in data units.

    Squares tile with every edge aligned to its neighbours, so the eye picks out
    the grid before the data. Hexagons have no such shared axis, and each one's
    centre is equidistant from all six neighbours rather than nearer the edges
    than the corners — the binning is less biased and it looks less like a
    spreadsheet.
    """
    x_span = ENVELOPE_SPEED[1] - ENVELOPE_SPEED[0]
    y_span = ENVELOPE_ALTITUDE[1] - ENVELOPE_ALTITUDE[0]

    px = np.clip((speed - ENVELOPE_SPEED[0]) / x_span, 0, 1) * ENVELOPE_W
    py = np.clip((altitude - ENVELOPE_ALTITUDE[0]) / y_span, 0, 1) * ENVELOPE_H

    # Row pitch is 1.5r and column pitch r√3; odd rows sit half a column across,
    # which is what interlocks them.
    dx, dy = HEX_RADIUS_PX * np.sqrt(3), HEX_RADIUS_PX * 1.5
    row = np.round(py / dy)
    col = np.round(px / dx - (row % 2) / 2)

    cx = (col + (row % 2) / 2) * dx
    cy = row * dy
    return (
        cx / ENVELOPE_W * x_span + ENVELOPE_SPEED[0],
        cy / ENVELOPE_H * y_span + ENVELOPE_ALTITUDE[0],
    )


def band_color(altitude: float) -> list[int]:
    if pd.isna(altitude):
        return UNKNOWN_COLOR
    for ceiling, rgb, _, _ in ALTITUDE_BANDS:
        if altitude < ceiling:
            return rgb
    return UNKNOWN_COLOR


snapshots = conn.query(
    "select distinct snapshot_at from fct_flight_positions order by snapshot_at desc",
    ttl="5m",
)

if snapshots.empty:
    st.warning("No data yet — run the pipeline first.", icon=":material/database_off:")
    st.stop()

with st.sidebar:
    st.subheader("Filters", divider=False)

    snapshot = st.selectbox(
        "Snapshot",
        snapshots["snapshot_at"],
        format_func=lambda t: t.strftime("%d %b %Y, %H:%M UTC"),
    )
    region = st.segmented_control(
        "Region",
        list(REGIONS),
        default="Europe",
        width="stretch",
    )
    airborne_only = st.toggle("Airborne only", value=True)
    min_altitude = st.slider("Minimum altitude", 0, 13000, 0, step=500, format="%d m")

positions = conn.query(
    """
    select
        icao24,
        callsign,
        airline_name,
        airline_country,
        registration_country,
        latitude,
        longitude,
        baro_altitude,
        velocity,
        true_track,
        on_ground
    from fct_flight_positions
    where snapshot_at = :snapshot
      and latitude is not null
      and longitude is not null
    """,
    params={"snapshot": snapshot},
    ttl="5m",
)

# count(*), not count(distinct icao24), throughout the per-snapshot aggregates.
# OpenSky answers each poll with one row per aircraft, so the two are equal —
# checked across all 86 snapshots, zero rows of difference — while the distinct
# costs roughly ten times as much, being a per-group sort rather than a tally.
# When a run fired twice it landed as two snapshots seconds apart, not as
# duplicates inside one, so the distinct was never the thing protecting against
# that anyway.
def load_airline_trend(airlines: list[str]) -> pd.DataFrame:
    return conn.query(
        """
        select
            snapshot_at,
            airline_name,
            count(*) as aircraft
        from fct_flight_positions
        where not on_ground
          and airline_name = any(:airlines)
        group by snapshot_at, airline_name
        order by snapshot_at
        """,
        params={"airlines": airlines},
        ttl="5m",
    )


eu_airline_trend = load_airline_trend(EU_AIRLINES)
us_airline_trend = load_airline_trend(US_AIRLINES)

# One subquery per region, generated from REGIONS so the chart can never drift
# from the map's idea of where "Europe" is. The bounds are floats from that
# constant, not user input; a bound parameter can't stand in for a repeated
# subquery, so they are formatted in.
region_trend = conn.query(
    "\nunion all\n".join(
        f"""
        select
            snapshot_at,
            '{name}' as region,
            count(*) as aircraft
        from fct_flight_positions
        where not on_ground
          and latitude between {box[0]:f} and {box[1]:f}
          and longitude between {box[2]:f} and {box[3]:f}
        group by snapshot_at
        """
        for name, box in REGIONS.items()
        if box
    )
    + "\norder by snapshot_at",
    ttl="5m",
)

# Feeds the status row. Vertical rate is noisy near zero, so ±2.5 m/s (roughly
# ±500 ft/min) is the cut between "level" and a real climb or descent.
snapshot_stats = conn.query(
    """
    select
        snapshot_at,
        count(*) filter (where not on_ground) as airborne,
        count(*) filter (where not on_ground and vertical_rate > 2.5) as climbing,
        count(*) filter (where not on_ground and vertical_rate < -2.5) as descending
    from fct_flight_positions
    group by snapshot_at
    order by snapshot_at
    """,
    ttl="5m",
)

# Already one row per day out of dbt, so this is a read rather than an
# aggregation — the reasoning about which daily figures survive the sampling
# lives in the model, not here.
daily_traffic = conn.query(
    "select * from fct_daily_traffic order by traffic_date desc",
    ttl="5m",
)

with st.sidebar:
    airline_options = sorted(positions["airline_name"].dropna().unique())
    selected_airlines = st.multiselect(
        "Airlines",
        airline_options,
        placeholder="All airlines",
    )
    st.caption(f"{len(snapshots):,} snapshots stored · source: OpenSky Network")

if airborne_only:
    positions = positions[~positions["on_ground"]]

positions = positions[positions["baro_altitude"].fillna(0) >= min_altitude]

if selected_airlines:
    positions = positions[positions["airline_name"].isin(selected_airlines)]

bounds = REGIONS[region] if region else None
if bounds:
    min_lat, max_lat, min_lon, max_lon, _ = bounds
    positions = positions[
        positions["latitude"].between(min_lat, max_lat)
        & positions["longitude"].between(min_lon, max_lon)
    ]

st.title("Flight tracker")
st.caption(
    f"Aircraft positions from the OpenSky Network, enriched with airline reference data · "
    f"snapshot of {snapshot.strftime('%d %b %Y, %H:%M UTC')}"
)

if positions.empty:
    st.info("No aircraft match these filters.", icon=":material/filter_alt_off:")
    st.stop()

positions["color"] = positions["baro_altitude"].map(band_color)
positions["altitude_label"] = positions["baro_altitude"].map(
    lambda a: "unknown" if pd.isna(a) else f"{a:,.0f} m"
)
positions["airline_label"] = positions["airline_name"].fillna("Unknown airline")
positions["heading_label"] = positions["true_track"].map(
    lambda t: "unknown" if pd.isna(t) else f"{t:.0f}°"
)

# Leader lines: where each aircraft reaches if it holds course and speed, the
# same convention an ATC display uses. Length carries speed, so a fast jet
# draws a long line and a holding turboprop a stub — direction and speed in one
# mark, and the dot stays the "now" so the line can only read as ahead.
#
# Equirectangular offset rather than a great-circle solution: over four minutes
# the error is metres, far under a pixel at any zoom this map reaches. The
# cosine is floored so a track near the poles cannot blow the longitude up.
reach_m = positions["velocity"] * LEADER_SECONDS
track_rad = np.radians(positions["true_track"])
lat_rad = np.radians(positions["latitude"])
positions["lead_latitude"] = positions["latitude"] + (reach_m * np.cos(track_rad)) / 111_320
positions["lead_longitude"] = positions["longitude"] + (reach_m * np.sin(track_rad)) / (
    111_320 * np.cos(lat_rad).clip(lower=0.15)
)
# Rows missing velocity or track would otherwise collapse to a zero-length line
# or, worse, a confident line due north.
leaders = positions.dropna(subset=["velocity", "true_track", "lead_latitude", "lead_longitude"])

# Status row. Deliberately unfiltered: it answers "what is the sky doing, and is
# the pipeline healthy", which the sidebar's exploration filters should not
# reshape. Everything below the row is filtered as before.
history = snapshot_stats[snapshot_stats["snapshot_at"] <= snapshot]
current = history.iloc[-1]

# Nearest reading to 24h before the selected snapshot — absent early in the
# dataset, in which case the tile simply shows no delta.
day_ago_at = snapshot - pd.Timedelta(hours=24)
day_ago = (
    history.iloc[(history["snapshot_at"] - day_ago_at).abs().argmin()]
    if history["snapshot_at"].min() <= day_ago_at
    else None
)

regions_now = (
    region_trend[region_trend["snapshot_at"] == current["snapshot_at"]]
    .set_index("region")["aircraft"]
    .sort_values(ascending=False)
)

latest_at = snapshot_stats["snapshot_at"].max()
age_min = (pd.Timestamp.now(tz="UTC") - latest_at).total_seconds() / 60
snaps_24h = int((snapshot_stats["snapshot_at"] > latest_at - pd.Timedelta(hours=24)).sum())
cadence = snapshot_stats["snapshot_at"].diff().dt.total_seconds().div(60).dropna()

with st.container(horizontal=True):
    st.metric(
        "Airborne",
        f"{current['airborne']:,}",
        delta=(
            f"{(current['airborne'] / day_ago['airborne'] - 1):+.0%} vs 24h ago"
            if day_ago is not None and day_ago["airborne"]
            else None
        ),
        border=True,
        chart_data=history["airborne"].tail(24),
        help="Aircraft in the air at the selected snapshot, whole dataset.",
    )

    if len(regions_now) >= 2 and regions_now.iloc[1]:
        lead, chaser = regions_now.index[0], regions_now.index[1]
        st.metric(
            "Busiest region",
            lead,
            delta=f"{regions_now.iloc[0] / regions_now.iloc[1]:.1f}× {chaser}",
            delta_arrow="off",
            delta_color="off",
            border=True,
            chart_data=(
                region_trend[region_trend["region"] == lead]
                .set_index("snapshot_at")
                .loc[:snapshot, "aircraft"]
                .tail(24)
            ),
            help="Which region holds the most aircraft right now, and by how much.",
        )

    # Climbs outnumbering descents means the sky is filling; the reverse means
    # it is winding down. Around parity neither reading is meaningful.
    climb_ratio = current["climbing"] / current["descending"] if current["descending"] else None
    st.metric(
        "Sky",
        "Filling" if climb_ratio and climb_ratio > 1.05 else "Emptying" if climb_ratio and climb_ratio < 0.95 else "Steady",
        delta=f"{climb_ratio:.2f}× climbing vs descending" if climb_ratio else None,
        delta_arrow="up" if climb_ratio and climb_ratio > 1.05 else "down" if climb_ratio and climb_ratio < 0.95 else "off",
        delta_color="off",
        border=True,
        chart_data=(history["climbing"] / history["descending"]).tail(24),
        help="Ratio of climbing to descending aircraft.",
    )

    st.metric(
        "Pipeline",
        f"{age_min:,.0f} min ago" if age_min < 120 else f"{age_min / 60:,.1f} h ago",
        delta=f"{snaps_24h} snapshots in 24h",
        delta_arrow="off",
        delta_color="off",
        border=True,
        chart_data=cadence.tail(24),
        help="Age of the newest snapshot; the sparkline is the gap in minutes between runs.",
    )

with st.container(border=True):
    st.subheader("Where they are")

    with st.container(horizontal=True, vertical_alignment="center"):
        map_style = st.segmented_control(
            "Map style",
            ["Aircraft", "Density"],
            default="Aircraft",
            label_visibility="collapsed",
        )
        # The legend has to follow the mode. Density colours by how many aircraft
        # share a patch of sky, not by how high they are, so showing the altitude
        # bands there would have the reader decode the heatmap against a scale it
        # was never drawn from.
        if map_style == "Density":
            st.badge("Sparse", color="grey")
            st.badge("Dense", color="orange")
        else:
            for _, _, badge_color, label in ALTITUDE_BANDS:
                st.badge(label, color=badge_color)

    st.caption(
        "Colour is aircraft per patch of sky, not altitude."
        if map_style == "Density"
        else "Each line runs four minutes ahead on the current course — longer means faster. "
        "Hover over an aircraft for flight details."
    )

    view_state = pdk.ViewState(
        latitude=float(positions["latitude"].median()),
        longitude=float(positions["longitude"].median()),
        zoom=bounds[4] if bounds else 0.9,
    )

    leader_layer = pdk.Layer(
        "LineLayer",
        data=leaders,
        get_source_position=["longitude", "latitude"],
        get_target_position=["lead_longitude", "lead_latitude"],
        get_color="color",
        get_width=1,
        width_units=pdk.types.String("pixels"),
        width_min_pixels=1,
        width_max_pixels=2,
        # Fainter than the dots on purpose: the line is the supporting mark, and
        # at world zoom thousands of them would otherwise undo the density work.
        opacity=0.45,
    )

    aircraft_layer = pdk.Layer(
        "ScatterplotLayer",
        data=positions,
        get_position=["longitude", "latitude"],
        get_fill_color="color",
        get_radius=AIRCRAFT_RADIUS_M,
        # pdk.types.String, not a bare "meters": pydeck serialises a plain string
        # into the accessor `@@=meters`, so deck.gl received a per-row lookup
        # where it wanted a unit name, and sized every dot against a fallback.
        # The bare form was here from the start, which is why radius_units never
        # actually took effect.
        radius_units=pdk.types.String("meters"),
        radius_min_pixels=AIRCRAFT_MIN_PX,
        radius_max_pixels=AIRCRAFT_MAX_PX,
        # Slightly translucent, so a crowded corridor reads as darker rather than
        # saturating into one flat shape.
        opacity=0.8,
        pickable=True,
    )
    flight_tooltip = {
        "html": "<b>{callsign}</b><br/>{airline_label}<br/>{altitude_label}<br/>heading {heading_label}",
        "style": {"fontSize": "12px"},
    }

    if map_style == "Density":
        layers = [
            pdk.Layer(
                "HeatmapLayer",
                data=positions[["latitude", "longitude"]],
                get_position=["longitude", "latitude"],
                color_range=DENSITY_RAMP,
                radius_pixels=45,
                opacity=0.75,
            )
        ]
        tooltip = None
    else:
        # Leader lines first so the dots draw over them: the dot has to stay the
        # unambiguous "here it is now", with the line trailing out ahead.
        layers = [leader_layer, aircraft_layer]
        tooltip = flight_tooltip

    st.pydeck_chart(
        pdk.Deck(
            layers=layers,
            initial_view_state=view_state,
            map_style=pdk.map_styles.CARTO_DARK,
            tooltip=tooltip,
        ),
        height=520,
    )

left, right = st.columns(2)

with left.container(border=True, height="stretch"):
    st.subheader("Busiest airlines")
    st.caption("Distinct aircraft currently tracked")
    top_airlines = (
        positions.dropna(subset=["airline_name"])
        .groupby("airline_name", as_index=False)["icao24"]
        .nunique()
        .rename(columns={"icao24": "aircraft", "airline_name": "airline"})
        .nlargest(12, "aircraft")
    )
    if top_airlines.empty:
        st.caption("No callsigns in this selection matched a known airline.")
    else:
        chart = (
            alt.Chart(top_airlines)
            .mark_bar(cornerRadiusEnd=3)
            .encode(
                x=alt.X("aircraft:Q", title="Aircraft"),
                y=alt.Y("airline:N", title=None, sort="-x"),
                # Shares the altitude/envelope ramp so the page reads as one warm
                # scale. It does restate what the bar length already says —
                # colour here carries no information of its own — so no legend:
                # a key would put the same number on screen a third time.
                # Linear, not log like the envelope; twelve bars are not the
                # skewed distribution that needed compressing there.
                color=alt.Color(
                    "aircraft:Q",
                    scale=alt.Scale(range=ENVELOPE_RAMP),
                    legend=None,
                ),
                tooltip=["airline", "aircraft"],
            )
            .properties(height=380)
        )
        st.altair_chart(chart)

with right.container(border=True, height="stretch"):
    st.subheader("Speed against altitude")
    st.caption("Brighter cells hold more aircraft · the tight patch is cruise")
    # Binned rather than one mark per aircraft. A worldwide snapshot puts 12,000
    # points into a 380px panel, 82% of them landing on top of another and 28
    # deep at the worst cell, so plain dots painted a flat slab that hid the very
    # thing worth seeing: the dense cruise cluster with climb and descent arcs
    # running out of it.
    #
    # Binning happens here rather than with alt.Bin because Vega-Lite's transform
    # runs in the browser, which would still ship every row — and trip Altair's
    # 5,000-row ceiling. Aggregating first sends a few hundred cells.
    flown = positions.dropna(subset=["velocity", "baro_altitude"])
    hex_speed, hex_altitude = hex_bin(
        flown["velocity"].to_numpy(), flown["baro_altitude"].to_numpy()
    )
    envelope_data = (
        pd.DataFrame({"speed": hex_speed, "altitude": hex_altitude})
        .groupby(["speed", "altitude"], as_index=False)
        .size()
        .rename(columns={"size": "aircraft"})
    )
    envelope = (
        alt.Chart(envelope_data)
        .mark_point(
            shape=HEX_PATH,
            filled=True,
            # `size` is not the mark's area for a custom path: Vega treats the
            # path as drawn in a 2-unit box and scales it by √size / 2. Measured
            # off the rendered SVG — the area reading came out 0.81× too small.
            # So the radius the lattice expects needs (2r)².
            size=(2 * HEX_DRAW_RADIUS_PX) ** 2,
        )
        .encode(
            x=alt.X(
                "speed:Q",
                title="Ground speed (m/s)",
                scale=alt.Scale(domain=list(ENVELOPE_SPEED), nice=False),
            ),
            y=alt.Y(
                "altitude:Q",
                title="Altitude (m)",
                scale=alt.Scale(domain=list(ENVELOPE_ALTITUDE), nice=False),
            ),
            color=alt.Color(
                "aircraft:Q",
                title="Aircraft",
                # Log, because cell counts are heavily right-skewed — median 10,
                # max 259. On a linear scale 84% of cells landed in the first of
                # the colour steps, so the panel read as one flat slab with two
                # bright specks. Log spreads them 141/107/122/102/29.
                scale=alt.Scale(type="log", range=ENVELOPE_RAMP),
                legend=alt.Legend(
                    orient="bottom",
                    direction="horizontal",
                    gradientLength=280,
                    gradientThickness=12,
                    labelFontSize=11,
                    titleFontSize=12,
                ),
            ),
            tooltip=[
                alt.Tooltip("speed:Q", title="Ground speed (m/s)", format=".0f"),
                alt.Tooltip("altitude:Q", title="Altitude (m)", format=",.0f"),
                alt.Tooltip("aircraft:Q", title="Aircraft"),
            ],
        )
        .properties(
            width=ENVELOPE_W,
            height=ENVELOPE_H,
            # Under the default "fit" autosize, width and height describe the
            # whole chart and the axes and legend are taken out of the middle,
            # leaving a plot area of about 441×218 — squashed 1.5× more
            # vertically than horizontally. Regular hexagons cannot tile an
            # anisotropically squashed lattice at any size. "pad" makes these
            # numbers the plot area itself and grows the chart around it, so the
            # lattice is the one hex_bin was computed against.
            autosize=alt.AutoSizeParams(type="pad", contains="padding"),
        )
    )
    # width="content" so the declared plot size survives instead of being
    # stretched to the column — the hexagons are placed against exactly these
    # dimensions, and a rescaled plot slides them out of register.
    st.altair_chart(envelope, width="content")

with st.container(border=True):
    st.subheader("Coverage over time")
    st.caption("Airborne aircraft per snapshot, across the whole dataset")
    # Reuses snapshot_stats rather than asking for the same figure again: the
    # airborne count per snapshot is already the first column of that result.
    #
    # Altair rather than st.line_chart, which draws a marker on every reading —
    # 94 of them here — and is the only line chart on the page not going through
    # trend_chart. One series, so the subheader names it and no legend is needed.
    st.altair_chart(
        alt.Chart(snapshot_stats)
        .mark_line(strokeWidth=2, color=AMBER)
        .encode(
            x=alt.X("snapshot_at:T", title="Snapshot"),
            y=alt.Y("airborne:Q", title="Aircraft airborne"),
            tooltip=[
                alt.Tooltip("snapshot_at:T", title="Snapshot", format="%d %b, %H:%M"),
                alt.Tooltip("airborne:Q", title="Aircraft airborne", format=",d"),
            ],
        )
        .properties(height=220),
        width="stretch",
    )

with st.container(border=True):
    st.subheader("Where the traffic moves")
    st.caption(
        "Airborne aircraft per region · deliberately ignores the region filter, "
        "since the point is to compare regions against each other"
    )
    st.altair_chart(
        trend_chart(region_trend, "region", "Region", TREND_REGIONS),
        width="stretch",
    )

# Side by side and on one shared axis, so the two continents can be read against
# each other — the whole point of showing them at all. The ceiling comes from
# both frames together; on separate scales Europe's 399-aircraft peak would be
# drawn as tall as America's 484 and the panels would quietly lie.
fleet_ceiling = max(
    eu_airline_trend["aircraft"].max() if not eu_airline_trend.empty else 0,
    us_airline_trend["aircraft"].max() if not us_airline_trend.empty else 0,
)

europe, americas = st.columns(2)

for column, title, data, carriers in (
    (europe, "European traffic over time", eu_airline_trend, EU_AIRLINES),
    (americas, "American traffic over time", us_airline_trend, US_AIRLINES),
):
    with column.container(border=True, height="stretch"):
        st.subheader(title)
        st.caption("Airborne aircraft per carrier · a fixed set of eight, shared scale")
        if data.empty:
            st.caption("No tracking data for these carriers yet.")
        else:
            st.altair_chart(
                trend_chart(
                    data,
                    "airline_name",
                    "Airline",
                    carriers,
                    y_max=fleet_ceiling,
                    compact=True,
                ),
                width="stretch",
            )

with st.container(border=True):
    st.subheader("Day in review")
    st.caption(
        "One row per UTC day · coverage is the share of the 48 expected snapshots "
        "that landed, and a thin day undercounts its own peak"
    )
    if daily_traffic.empty:
        st.caption("No completed days yet.")
    else:
        day_labels = {
            row.traffic_date: (
                f"{row.traffic_date:%d %b %Y}"
                + ("" if row.coverage_pct >= FULL_DAY_COVERAGE else f"  ·  {row.coverage_pct:.0f}% coverage")
            )
            for row in daily_traffic.itertuples()
        }
        chosen = st.selectbox(
            "Day",
            list(day_labels),
            format_func=day_labels.get,
            label_visibility="collapsed",
        )
        day = daily_traffic.set_index("traffic_date").loc[chosen]

        # Day-on-day only against a day complete enough to have found its own
        # peak; comparing against a 17%-coverage day would read as a crash in
        # traffic that is really just a gap in sampling.
        earlier = daily_traffic[daily_traffic["traffic_date"] < chosen]
        earlier = earlier[earlier["coverage_pct"] >= FULL_DAY_COVERAGE]
        previous = earlier.iloc[0] if not earlier.empty else None

        if day.coverage_pct < FULL_DAY_COVERAGE:
            st.warning(
                f"Only {day.snapshots} of 48 snapshots landed on this day "
                f"({day.coverage_pct:.0f}%). Peak and trough are floors, not the real extremes.",
                icon=":material/warning:",
            )

        with st.container(horizontal=True):
            st.metric(
                "Peak airborne",
                f"{day.peak_airborne:,}",
                delta=(
                    f"{day.peak_airborne / previous.peak_airborne - 1:+.0%} vs {previous.traffic_date:%d %b}"
                    if previous is not None
                    else None
                ),
                border=True,
                help=f"Reached at {day.peak_at:%H:%M} UTC.",
            )
            st.metric(
                "Quietest moment",
                f"{day.trough_airborne:,}",
                delta=f"at {day.trough_at:%H:%M} UTC",
                delta_arrow="off",
                delta_color="off",
                border=True,
            )
            st.metric(
                "Busiest airline",
                day.busiest_airline or "—",
                delta=(
                    f"{day.busiest_airline_aircraft:,.0f} aircraft on average"
                    if pd.notna(day.busiest_airline_aircraft)
                    else None
                ),
                delta_arrow="off",
                delta_color="off",
                border=True,
                help="Ranked on the mean across the day's snapshots, not the daily total.",
            )
            st.metric(
                "Typical cruise",
                f"{day.median_altitude:,.0f} m",
                delta=f"{day.median_velocity:.0f} m/s",
                delta_arrow="off",
                delta_color="off",
                border=True,
                help="Median across every airborne reading that day.",
            )

        st.dataframe(
            daily_traffic,
            hide_index=True,
            width="stretch",
            column_order=[
                "traffic_date",
                "coverage_pct",
                "peak_airborne",
                "trough_airborne",
                "mean_airborne",
                "busiest_airline",
                "median_altitude",
                "climb_ratio",
            ],
            column_config={
                "traffic_date": st.column_config.DateColumn("Day", format="DD MMM YYYY"),
                "coverage_pct": st.column_config.ProgressColumn(
                    "Coverage", min_value=0, max_value=100, format="%d%%"
                ),
                "peak_airborne": st.column_config.NumberColumn("Peak", format="localized"),
                "trough_airborne": st.column_config.NumberColumn("Quietest", format="localized"),
                "mean_airborne": st.column_config.NumberColumn("Average", format="localized"),
                "busiest_airline": st.column_config.TextColumn("Busiest airline"),
                "median_altitude": st.column_config.NumberColumn("Median altitude", format="%d m"),
                "climb_ratio": st.column_config.NumberColumn(
                    "Climb : descend",
                    format="%.2f",
                    help="Above 1 the sky filled over the day, below 1 it drained.",
                ),
            },
        )

with st.container(border=True):
    st.subheader("Flight log")
    st.dataframe(
        positions[
            [
                "callsign",
                "airline_name",
                "registration_country",
                "baro_altitude",
                "velocity",
                "latitude",
                "longitude",
            ]
        ].sort_values("baro_altitude", ascending=False),
        hide_index=True,
        height=360,
        column_config={
            "callsign": st.column_config.TextColumn("Callsign", pinned=True),
            "airline_name": "Airline",
            "registration_country": "Registered in",
            "baro_altitude": st.column_config.NumberColumn("Altitude", format="%.0f m"),
            "velocity": st.column_config.NumberColumn("Speed", format="%.0f m/s"),
            "latitude": st.column_config.NumberColumn("Lat", format="%.2f"),
            "longitude": st.column_config.NumberColumn("Lon", format="%.2f"),
        },
    )
