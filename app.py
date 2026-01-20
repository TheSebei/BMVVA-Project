import streamlit as st
import pandas as pd
import altair as alt

st.set_page_config(page_title="UK Road Accidents Map", layout="wide")
st.title("UK Road Accidents")

DATA_PATH = "Road_Accident_Data.parquet"

# canvas instead of SVG for much faster point rendering
alt.renderers.set_embed_options(renderer="canvas")


@st.cache_data
def load_data(path: str) -> pd.DataFrame:
    df = pd.read_parquet(path)
    df["latitude"] = pd.to_numeric(df["latitude"], errors="coerce")
    df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")
    return df.dropna(subset=["latitude", "longitude"])


df = load_data(DATA_PATH)

# --- Sampling slider (for performance) ---
max_available = len(df)

default_n = min(1_000, max_available)
step_n = 1_000

n_points = st.slider(
    "Number of accidents to plot (random sample)",
    min_value=1_000,
    max_value=max_available,
    value=default_n,
    step=step_n,
    help="Smaller samples render much faster. The sample is re-drawn when you move the slider.",
)

if n_points < max_available:
    df_plot = df.sample(n_points, random_state=42)
    st.caption(
        f"Loaded {max_available:,} accidents. Showing a random sample of {n_points:,} for performance."
    )
else:
    df_plot = df
    st.caption(f"Loaded all {max_available:,} accidents with valid coordinates.")

# --- Opacity slider for map points ---
st.sidebar.header("Map Appearance")
point_opacity = st.sidebar.slider(
    "Point opacity",
    min_value=0.01,
    max_value=1.0,
    value=0.50,
    step=0.01,
    help="Controls the opacity of accident points on the map.",
)

# --- UK administrative boundaries (TopoJSON) ---
UK_TOPO_URL = "https://raw.githubusercontent.com/ONSdigital/uk-topojson/refs/heads/main/output/topo.json"

layer_label_to_key = {
    "Counties (England)": "cty",
    "Upper-tier / unitary authorities (UK)": "utla",
    "Lower-tier / unitary authorities (UK)": "ltla",
}
selected_label = st.selectbox(
    "Boundary layer",
    list(layer_label_to_key.keys()),
    index=1,
)
GEOG_KEY = layer_label_to_key[selected_label]

uk_outline = alt.Chart(alt.topo_feature(UK_TOPO_URL, "uk")).mark_geoshape(
    fill="lightgray",
    stroke="white",
    strokeWidth=1.0,
)

admin_boundaries = alt.Chart(alt.topo_feature(UK_TOPO_URL, GEOG_KEY)).mark_geoshape(
    fill=None,
    stroke="white",
    strokeWidth=0.7,
)

# --- Sidebar controls (encoding) ---
st.sidebar.header("Encodings")

all_cols = list(df_plot.columns)

_default_map_color = (
    "accident_severity"
    if "accident_severity" in all_cols
    else ("day_of_week" if "day_of_week" in all_cols else all_cols[0])
)

# Map + Bar colors
map_color_field = st.sidebar.selectbox(
    "Map & Bar: Color",
    options=all_cols,
    index=all_cols.index(_default_map_color) if _default_map_color in all_cols else 0,
)

# Pie colors (separate)
_default_pie_color = (
    "day_of_week"
    if "day_of_week" in all_cols
    else ("weather_conditions" if "weather_conditions" in all_cols else all_cols[0])
)

pie_color_field = st.sidebar.selectbox(
    "Pie: Color",
    options=all_cols,
    index=all_cols.index(_default_pie_color) if _default_pie_color in all_cols else 0,
)

shape_options = ["(none)"] + all_cols
shape_field = st.sidebar.selectbox(
    "Shape",
    options=shape_options,
    index=0,
)
shape_field = None if shape_field == "(none)" else shape_field

size_options = ["(none)"] + all_cols
size_field = st.sidebar.selectbox(
    "Size",
    options=size_options,
    index=0,
)
size_field = None if size_field == "(none)" else size_field


def _vega_type(col: str) -> str:
    return "Q" if pd.api.types.is_numeric_dtype(df_plot[col]) else "N"


def _size_type(col: str) -> str:
    return "Q" if pd.api.types.is_numeric_dtype(df_plot[col]) else "O"


def _ordered_categories(field: str, series: pd.Series) -> list:
    cats = series.dropna().unique().tolist()
    if field == "accident_severity":
        severity_order = ["slight", "serious", "fatal"]
        return [c for c in severity_order if c in cats]
    if field == "day_of_week":
        dow_order = [
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
            "Sunday",
        ]
        # Keep only those present; append any extras at the end.
        ordered = [d for d in dow_order if d in cats]
        extras = [d for d in cats if d not in ordered]
        return ordered + extras
    return cats


def _color_scale_for(field: str, categories: list) -> alt.Scale:
    # Keep severity palette; otherwise default Vega behavior.
    if field == "accident_severity" and categories:
        return alt.Scale(
            domain=categories,
            range=[
                "#84c3ff",  # slight
                "#627cf3",  # serious
                "#E44848",  # fatal
            ],
        )
    if len(categories) == 2:
        return alt.Scale(domain=categories, range=["#e63946", "#0694d6"])
    return alt.Scale(domain=categories)


# --- Linked selections ---
# Brush = spatial selection (select points on the map)
brush = alt.selection_interval(name="brush")

# Selection on Map/Bar color field (click legend/category)
sel_mapbar = alt.selection_point(
    fields=[map_color_field],
    on="click",
    clear="dblclick",
    name="sel_mapbar",
)

# Selection on Pie color field (click slice)
sel_pie = alt.selection_point(
    fields=[pie_color_field],
    on="click",
    clear="dblclick",
    name="sel_pie",
)


# --- Accident points (filtered by BOTH categorical selections, and brushed for opacity) ---
enc_points = {
    "longitude": alt.Longitude("longitude:Q"),
    "latitude": alt.Latitude("latitude:Q"),
    "color": alt.Color(
        f"{map_color_field}:{_vega_type(map_color_field)}",
        legend=alt.Legend(title=map_color_field),
    ),
}

if shape_field is not None:
    enc_points["shape"] = alt.Shape(
        f"{shape_field}:N", legend=alt.Legend(title=shape_field)
    )

if size_field is not None:
    enc_points["size"] = alt.Size(
        f"{size_field}:{_size_type(size_field)}",
        legend=alt.Legend(title=size_field),
    )

# Build tooltip dynamically
tooltip_fields = [
    alt.Tooltip(f"{map_color_field}:{_vega_type(map_color_field)}", title=str(map_color_field)),
    alt.Tooltip(f"{pie_color_field}:{_vega_type(pie_color_field)}", title=str(pie_color_field)),
]
if shape_field is not None:
    tooltip_fields.append(alt.Tooltip(f"{shape_field}:N", title=str(shape_field)))
if size_field is not None:
    tooltip_fields.append(
        alt.Tooltip(f"{size_field}:{_size_type(size_field)}", title=str(size_field))
    )

tooltip_fields += [
    alt.Tooltip("latitude:Q", format=".4f"),
    alt.Tooltip("longitude:Q", format=".4f"),
]

points = (
    alt.Chart(df_plot)
    .transform_filter(sel_mapbar)
    .transform_filter(sel_pie)
    .mark_point(filled=True)
    .encode(
        **enc_points,
        tooltip=tooltip_fields,
        opacity=alt.condition(brush, alt.value(point_opacity), alt.value(0.05)),
    )
    .add_params(brush)
)

chart = (
    alt.layer(uk_outline, admin_boundaries, points)
    .project(type="mercator")
    .properties(height=650)
)

# --- Bar chart (grouped by Map/Bar color field; filtered by brush + pie selection) ---
bar_categories = _ordered_categories(map_color_field, df_plot[map_color_field])
bar_color_scale = _color_scale_for(map_color_field, bar_categories)

bar = (
    alt.Chart(df_plot)
    .transform_filter(brush)
    .transform_filter(sel_pie)
    .mark_bar(size=12)
    .encode(
        x=alt.X("count():Q", title="Count"),
        y=alt.Y(
            f"{map_color_field}:N",
            title=str(map_color_field),
            scale=alt.Scale(domain=bar_categories, paddingInner=0.6),
        ),
        color=alt.Color(
            f"{map_color_field}:N",
            scale=bar_color_scale,
            legend=alt.Legend(title=str(map_color_field)),
        ),
        tooltip=[
            alt.Tooltip(f"{map_color_field}:N", title=str(map_color_field)),
            alt.Tooltip("count():Q", title="Count", format=","),
        ],
        opacity=alt.condition(sel_mapbar, alt.value(1.0), alt.value(0.35)),
    )
    .add_params(sel_mapbar)
    .properties(height=220)
)

# --- Pie chart (grouped by Pie color field; filtered by brush + map/bar selection) ---
pie_categories = _ordered_categories(pie_color_field, df_plot[pie_color_field])
pie_color_scale = _color_scale_for(pie_color_field, pie_categories)

pie = (
    alt.Chart(df_plot)
    .transform_filter(brush)
    .transform_filter(sel_mapbar)
    .transform_aggregate(count="count()", groupby=[pie_color_field])
    .transform_joinaggregate(total="sum(count)")
    .transform_calculate(percent="datum.count / datum.total")
    .mark_arc()
    .encode(
        theta=alt.Theta("count:Q", stack=True),
        color=alt.Color(
            f"{pie_color_field}:N",
            scale=pie_color_scale,
            legend=alt.Legend(title=str(pie_color_field)),
        ),
        tooltip=[
            alt.Tooltip(f"{pie_color_field}:N", title=str(pie_color_field)),
            alt.Tooltip("count:Q", title="Count", format=","),
            alt.Tooltip("percent:Q", title="Percent", format=".1%"),
        ],
        opacity=alt.condition(sel_pie, alt.value(1.0), alt.value(0.4)),
    )
    .add_params(sel_pie)
    .properties(height=420, width=300)
)

# --- Temporal exploration heatmap (Day of week vs time of day) ---
# Try to find a usable time-of-day column; if none is found, we derive hour from a typical "time" string.
_time_candidates = [
    "hour",
    "accident_hour",
    "time_of_day",
    "time",
    "accident_time",
]
TIME_COL = next((c for c in _time_candidates if c in all_cols), None)

have_day_of_week = "day_of_week" in all_cols

if TIME_COL is not None and have_day_of_week:
    # If TIME_COL is already numeric (e.g., hour 0..23) use it directly; otherwise attempt to extract HH.
    is_time_numeric = pd.api.types.is_numeric_dtype(df_plot[TIME_COL])

    calc_hour = (
        f"datum['{TIME_COL}']"
        if is_time_numeric
        else f"toNumber(slice(datum['{TIME_COL}'], 0, 2))"
    )

    dow_domain = _ordered_categories("day_of_week", df_plot["day_of_week"])

    temporal_heatmap = (
        alt.Chart(df_plot, title="Day of week × time of day")
        .transform_filter(brush)
        .transform_filter(sel_mapbar)
        .transform_filter(sel_pie)
        .transform_calculate(hour=calc_hour)
        .mark_rect()
        .encode(
            x=alt.X(
                "hour:O",
                title="Hour of day",
                sort=list(range(24)),
            ),
            y=alt.Y(
                "day_of_week:N",
                title="Day of week",
                scale=alt.Scale(domain=dow_domain),
            ),
            color=alt.Color("count():Q", title="Count"),
            tooltip=[
                alt.Tooltip("day_of_week:N", title="Day"),
                alt.Tooltip("hour:O", title="Hour"),
                alt.Tooltip("count():Q", title="Count", format=","),
            ],
        )
        .properties(height=220, width=300)
    )
else:
    temporal_heatmap = (
        alt.Chart(pd.DataFrame({"note": ["Missing day_of_week or time column to build heatmap."]}))
        .mark_text(align="left")
        .encode(text="note:N")
        .properties(height=220, width=300)
    )

# --- Layout ---
left_panel = alt.vconcat(
chart,
bar,
spacing=10,
)


right_panel = alt.vconcat(
pie,
temporal_heatmap,
spacing=15,
)


combined = (
alt.hconcat(
left_panel,
right_panel,
spacing=15,
)
# Keep separate legends/scales since the pie can use a different field.
.resolve_scale(color="independent")
)


# Render charts full-width (no centering columns)
st.altair_chart(combined, use_container_width=True)