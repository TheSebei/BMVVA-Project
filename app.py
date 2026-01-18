# app.py
import streamlit as st
import pandas as pd
import altair as alt

st.set_page_config(page_title="UK Road Accidents Map", layout="wide")
st.title("UK Road Accidents")

DATA_PATH = "Road_Accident_Data.parquet"

# Use canvas instead of SVG for much faster point rendering
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
step_n = 1_000 if max_available <= 200_000 else 5_000

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

_default_color = (
    "accident_severity"
    if "accident_severity" in all_cols
    else ("day_of_week" if "day_of_week" in all_cols else all_cols[0])
)

color_field = st.sidebar.selectbox(
    "Color",
    options=all_cols,
    index=all_cols.index(_default_color) if _default_color in all_cols else 0,
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


# --- Accident points (with brushing) ---
brush = alt.selection_interval(name="brush")

color_click = alt.selection_point(
    fields=[color_field],
    on="click",
    clear="dblclick",
    name="color_click",
)

enc = {
    "longitude": alt.Longitude("longitude:Q"),
    "latitude": alt.Latitude("latitude:Q"),
    "color": alt.Color(
        f"{color_field}:{_vega_type(color_field)}",
        legend=alt.Legend(title=color_field),
    ),
}

if shape_field is not None:
    enc["shape"] = alt.Shape(f"{shape_field}:N", legend=alt.Legend(title=shape_field))

if size_field is not None:
    enc["size"] = alt.Size(
        f"{size_field}:{_size_type(size_field)}",
        legend=alt.Legend(title=size_field),
    )

# Build tooltip dynamically
tooltip_fields = [
    alt.Tooltip(f"{color_field}:{_vega_type(color_field)}", title=str(color_field)),
]
if shape_field is not None:
    tooltip_fields.append(alt.Tooltip(f"{shape_field}:N", title=str(shape_field)))
if size_field is not None:
    tooltip_fields.append(alt.Tooltip(f"{size_field}:{_size_type(size_field)}", title=str(size_field)))
tooltip_fields += [
    alt.Tooltip("latitude:Q", format=".4f"),
    alt.Tooltip("longitude:Q", format=".4f"),
]

points = (
    alt.Chart(df_plot)
    .transform_filter(color_click)
    .mark_point(filled=True)
    .encode(
        **enc,
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

# --- Summary charts (grouped ONLY by color, filtered by brush) ---
color_enc_field = f"{color_field}:{_vega_type(color_field)}"

bar_categories = df_plot[color_field].dropna().unique().tolist()

if color_field == "accident_severity":
    severity_order = ["slight", "serious", "fatal"]
    bar_categories = [c for c in severity_order if c in bar_categories]
    bar_color_scale = alt.Scale(
        domain=bar_categories,
        range=[
            "#84c3ff",
            "#627cf3",
            "#E44848",
        ],
    )
elif len(bar_categories) == 2:
    bar_color_scale = alt.Scale(domain=bar_categories, range=["#e63946", "#0694d6"])
else:
    bar_color_scale = alt.Scale(domain=bar_categories)

bar = (
    alt.Chart(df_plot)
    .transform_filter(brush)
    .mark_bar(size=12)
    .encode(
        x=alt.X("count():Q", title="Count"),
        y=alt.Y(
            f"{color_field}:N",
            title=str(color_field),
            scale=alt.Scale(domain=bar_categories, paddingInner=0.6),
        ),
        color=alt.Color(
            f"{color_field}:N",
            scale=bar_color_scale,
            legend=alt.Legend(title=str(color_field)),
        ),
        tooltip=[
            alt.Tooltip(f"{color_field}:N", title=str(color_field)),
            alt.Tooltip("count():Q", title="Count", format=","),
        ],
        opacity=alt.condition(color_click, alt.value(1.0), alt.value(0.35)),
    )
    .add_params(color_click)
    .properties(height=220)
)

pie = (
    alt.Chart(df_plot)
    .transform_filter(brush)
    .transform_aggregate(count="count()", groupby=[color_field])
    .transform_joinaggregate(total="sum(count)")
    .transform_calculate(percent="datum.count / datum.total")
    .mark_arc()
    .encode(
        theta=alt.Theta("count:Q", stack=True),
        color=alt.Color(
            f"{color_field}:N",
            scale=bar_color_scale,
            legend=alt.Legend(title=str(color_field)),
        ),
        tooltip=[
            alt.Tooltip(f"{color_field}:N", title=str(color_field)),
            alt.Tooltip("count:Q", title="Count", format=","),
            alt.Tooltip("percent:Q", title="Percent", format=".1%"),
        ],
        opacity=alt.condition(color_click, alt.value(1.0), alt.value(0.4)),
    )
    .add_params(color_click)
    .properties(height=420, width=300)
)

# --- Layout ---
left_panel = alt.vconcat(
    chart,
    bar,
    spacing=10,
)

combined = alt.hconcat(
    left_panel,
    pie,
    spacing=15,
).resolve_scale(
    color="independent"
)

# Center the charts in the page using Streamlit columns
col_left, col_center, col_right = st.columns([1, 3, 1])
with col_center:
    st.altair_chart(combined, use_container_width=True)
