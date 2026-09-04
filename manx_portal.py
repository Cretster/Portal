import streamlit as st
import requests
import re
import json
from pathlib import Path
import plotly.graph_objects as go
import pydeck as pdk
from datetime import datetime, timedelta
import folium
from streamlit_folium import st_folium
from folium.raster_layers import ImageOverlay

# ---------------------------------------------------------------------------
# Soil pH grid (local JSON produced from SoilGrids 0-5 cm mean)
# Place iom_ph_grid.json and iom_ph_overlay.png next to this script.
# ---------------------------------------------------------------------------
@st.cache_data
def load_ph_grid():
    candidates = [
        Path(__file__).parent / "iom_ph_grid.json",
        Path("iom_ph_grid.json"),
        Path("/mount/src/portal/iom_ph_grid.json"),
    ]
    for p in candidates:
        if p.exists():
            with p.open() as f:
                data = json.load(f)
            # support either key name from different exports
            if "ph_grid" not in data and "grid" in data:
                data["ph_grid"] = data["grid"]
            return data
    return None


def sample_ph(lat, lon, grid_meta):
    """Return predicted pH (float) or None if outside raster / nodata."""
    if grid_meta is None:
        return None
    west, south = grid_meta["west"], grid_meta["south"]
    east, north = grid_meta["east"], grid_meta["north"]
    width, height = grid_meta["width"], grid_meta["height"]
    ph_grid = grid_meta.get("ph_grid") or grid_meta.get("grid")
    if ph_grid is None:
        return None
    if not (south <= lat <= north and west <= lon <= east):
        return None
    x = int((lon - west) / (east - west) * width)
    y = int((north - lat) / (north - south) * height)
    if x < 0 or x >= width or y < 0 or y >= height:
        return None
    return ph_grid[y][x]


def ph_legend_html(ph_value=None):
    """Prominent legend + large pH readout for the selected point."""
    rows = [
        ("#d73027", "< 5.0", "ACIEEEEED!!!"),
        ("#fc8d59", "5.0 – 5.5", "Moderately acid (near optimal)"),
        ("#fee08b", "5.5 – 6.0", "Slightly acid (GREAT!)"),
        ("#d9ef8b", "6.0 – 6.5", "Near-neutral (near optimal)"),
        ("#91cf60", "6.5 – 7.0", "Slightly alkaline (like Duracell)"),
        ("#1a9850", "> 7.0", "Alkaline (like bleach)"),
    ]
    parts = [
        '<div style="font-size:14px;line-height:1.55;'
        'padding:12px 14px;background:#f7f9fb;border:1px solid #d0d7de;'
        'border-radius:8px;margin:8px 0 16px 0">'
    ]
    parts.append("<b style='font-size:15px'>Soil pH (0–5 cm) — SoilGrids prediction</b><br><br>")
    for colour, rng, desc in rows:
        parts.append(
            f'<span style="display:inline-block;width:20px;height:14px;'
            f'background:{colour};border:1px solid #999;margin-right:8px;'
            f'vertical-align:middle"></span>'
            f'<b>{rng}</b> &nbsp; <span style="color:#444">{desc}</span><br>'
        )
    if ph_value is not None:
        parts.append(
            '<div style="margin-top:14px;padding:12px 14px;background:#e8f4fc;'
            'border-left:5px solid #1a5276;border-radius:4px">'
            '<div style="font-size:28px;font-weight:700;color:#555;margin-bottom:2px">'
            "Chosen Location Soil pH:</div>"
            f'<div style="font-size:28px;font-weight:700;color:#1a5276;'
            f'letter-spacing:0.02em">pH ≈ {ph_value:.1f}</div>'
            "</div>"
        )
    else:
        parts.append(
            '<div style="margin-top:12px;color:#666;font-size:13px">'
            "Click the map to read pH at that point.</div>"
        )
    parts.append(
        '<div style="margin-top:10px;font-size:11px;color:#666">'
        "Model estimates at ~250 m resolution (ISRIC SoilGrids), smoothly interpolated for display. "
        "Most of the Isle of Man falls in the acid to near-neutral range.</div>"
    )
    parts.append("</div>")
    return "".join(parts)


# 1. Species Parameter Matrix
SPECIES_MATRIX = {
    "🍄 Field Mushroom (Agaricus campestris)": {
        "day_min": 8, "day_max": 14,
        "night_min": 5, "night_max": 9,
        "rain_trigger": 12, "frost_kill": True,
    },
}


# 2. Isle of Man Geographic Database
IOM_POSTCODE_DB = {
    "IM1": {"name": "Douglas / Onchan", "lat": 54.15, "lon": -4.48, "upland_offset": 0},
    "IM2": {"name": "Douglas Outskirts", "lat": 54.16, "lon": -4.51, "upland_offset": 1},
    "IM3": {"name": "Onchan Rural", "lat": 54.18, "lon": -4.45, "upland_offset": 1},
    "IM4": {"name": "Middle / Baldwin / Marown (High Uplands)", "lat": 54.20, "lon": -4.55, "upland_offset": 4},
    "IM5": {"name": "Peel / German / Patrick", "lat": 54.22, "lon": -4.67, "upland_offset": 2},
    "IM6": {"name": "Kirk Michael", "lat": 54.28, "lon": -4.59, "upland_offset": 2},
    "IM7": {"name": "Ramsey / Andreas / Jurby", "lat": 54.32, "lon": -4.40, "upland_offset": 0},
    "IM8": {"name": "Ramsey Town", "lat": 54.32, "lon": -4.38, "upland_offset": 0},
    "IM9": {"name": "Castletown / Ballasalla / Port Erin", "lat": 54.08, "lon": -4.63, "upland_offset": 1},
}


@st.cache_data(ttl=1800)
def fetch_live_weather(lat, lon):
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "temperature_2m,precipitation",
        "past_days": 2,
        "forecast_days": 1,
        "timezone": "auto",
    }
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    temps = data["hourly"]["temperature_2m"]
    precip = data["hourly"]["precipitation"]
    times = data["hourly"]["time"]

    now = datetime.fromisoformat(times[-1])
    cutoff_24h = now - timedelta(hours=24)
    cutoff_48h = now - timedelta(hours=48)

    last_24h = [(t, temp) for t, temp in zip(times, temps) if datetime.fromisoformat(t) >= cutoff_24h]
    last_48h_precip = [p for t, p in zip(times, precip) if datetime.fromisoformat(t) >= cutoff_48h]

    day_temp_max = max(v for _, v in last_24h)
    night_temp_min = min(v for _, v in last_24h)
    rain_48h = sum(last_48h_precip)
    had_frost = night_temp_min <= 0

    return {
        "day_temp": round(day_temp_max, 1),
        "night_temp": round(night_temp_min, 1),
        "rain_48h": round(rain_48h, 1),
        "had_frost": had_frost,
    }


@st.cache_data(ttl=1800)
def fetch_historical_daily(lat, lon, days_back=21, days_forward=5):
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
        "past_days": days_back,
        "forecast_days": days_forward,
        "timezone": "auto",
    }
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()["daily"]
    return data["time"], data["temperature_2m_max"], data["temperature_2m_min"], data["precipitation_sum"]


def score_color(score):
    if score >= 80:
        return "#2ecc71"
    elif score >= 50:
        return "#f1c40f"
    else:
        return "#e74c3c"


def build_trend_chart(dates, day_max, night_min, rain, scores, species_name):
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=dates, y=day_max, mode="lines+markers", name="Day Temp Max (°C)",
        line=dict(color="#e67e22", width=2),
    ))
    fig.add_trace(go.Scatter(
        x=dates, y=night_min, mode="lines+markers", name="Night Temp Min (°C)",
        line=dict(color="#3498db", width=2),
    ))
    fig.add_trace(go.Scatter(
        x=dates, y=rain, mode="lines+markers", name="Daily Rainfall (mm)",
        line=dict(color="#9b59b6", width=2), yaxis="y2",
    ))

    first_seg = True
    for i in range(len(dates) - 1):
        seg_color = score_color((scores[i] + scores[i + 1]) / 2)
        fig.add_trace(go.Scatter(
            x=[dates[i], dates[i + 1]], y=[scores[i], scores[i + 1]],
            mode="lines", line=dict(color=seg_color, width=6),
            yaxis="y2", showlegend=first_seg,
            name="FRUITING PROBABILITY % (thick line - red/yellow/green)",
            legendgroup="probability", hoverinfo="skip",
        ))
        first_seg = False
    fig.add_trace(go.Scatter(
        x=dates, y=scores, mode="markers", yaxis="y2",
        marker=dict(
            color=[score_color(s) for s in scores],
            size=9, line=dict(color="white", width=1),
        ),
        name="Probability Score", showlegend=False,
        hovertemplate="%{x}<br>Probability: %{y}%<extra></extra>",
    ))

    fig.update_layout(
        title=f"Weather & Fruiting Probability Trend — {species_name}",
        xaxis=dict(title="Date"),
        yaxis=dict(title="Temperature (°C)"),
        yaxis2=dict(title="Rainfall (mm) / Probability (%)", overlaying="y", side="right"),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        height=480,
    )
    return fig


def google_maps_link_to_latlon(url):
    raw_coords = re.match(r"^\s*(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)\s*$", url)
    if raw_coords:
        return float(raw_coords.group(1)), float(raw_coords.group(2))

    patterns = [
        r"@(-?\d+\.\d+),(-?\d+\.\d+)",
        r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)",
        r"[?&]q=(-?\d+\.\d+),(-?\d+\.\d+)",
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            return float(m.group(1)), float(m.group(2))

    if "goo.gl" in url:
        raise ValueError(
            "Short goo.gl links can't be read directly — open it in a browser once "
            "and paste the full address-bar URL instead, or paste coordinates "
            "(e.g. 54.150, -4.480)."
        )
    raise ValueError(
        "Couldn't find coordinates in that link. Try pasting raw coordinates "
        "instead (e.g. 54.150, -4.480)."
    )


@st.cache_data(ttl=86400)
def get_elevation_bonus(lat, lon):
    resp = requests.get(
        "https://api.open-meteo.com/v1/elevation",
        params={"latitude": lat, "longitude": lon},
        timeout=10,
    )
    resp.raise_for_status()
    elevation = resp.json()["elevation"][0]
    bonus = min(4, int(elevation // 50))
    return bonus, elevation


def calculate_precise_index(day_temp, night_temp, rain_48h, has_had_frost, bonus, s_rules):
    if s_rules["frost_kill"] and has_had_frost:
        return 0, "🔴 Season Terminated: Recent ground frost detected.", 0, 0, 0

    day_score = (
        35 if s_rules["day_min"] <= day_temp <= s_rules["day_max"]
        else (15 if (s_rules["day_min"] - 4) <= day_temp <= (s_rules["day_max"] + 3) else 0)
    )
    night_score = (
        25 if s_rules["night_min"] <= night_temp <= s_rules["night_max"]
        else (10 if (s_rules["night_min"] - 4) <= night_temp <= (s_rules["night_max"] + 3) else 0)
    )
    rain_score = (
        40 if rain_48h >= s_rules["rain_trigger"]
        else (20 if rain_48h >= (s_rules["rain_trigger"] / 2) else 0)
    )

    total_score = min(day_score + night_score + rain_score + (bonus * 5), 100)

    if total_score >= 80:
        verdict = "🟩 EXCELLENT: Strong probability of active seasonal growth flushes."
    elif total_score >= 50:
        verdict = "🟨 MODERATE: Sporadic growth possible. Check damp, unfertilized slopes."
    else:
        verdict = "🟥 POOR: Unviable conditions. Highly unlikely to observe growth right now."

    return total_score, verdict, day_score, night_score, rain_score


def render_location_map(lat, lon, label):
    highlight = pdk.Layer(
        "ScatterplotLayer",
        data=[{"lat": lat, "lon": lon, "label": label}],
        get_position="[lon, lat]",
        get_fill_color="[46, 204, 113, 90]",
        get_radius=900,
        pickable=False,
    )
    marker = pdk.Layer(
        "ScatterplotLayer",
        data=[{"lat": lat, "lon": lon, "label": label}],
        get_position="[lon, lat]",
        get_fill_color="[231, 76, 60, 255]",
        get_radius=70,
        pickable=True,
    )
    view_state = pdk.ViewState(latitude=lat, longitude=lon, zoom=11.5, pitch=0)
    st.pydeck_chart(pdk.Deck(
        layers=[highlight, marker],
        initial_view_state=view_state,
        tooltip={"text": "{label}"},
        map_style=None,
    ))


def find_overlay_png():
    candidates = [
        Path(__file__).parent / "iom_ph_overlay.png",
        Path("iom_ph_overlay.png"),
        Path("/mount/src/portal/iom_ph_overlay.png"),
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    return None


def build_clickable_ph_map(center_lat=54.23, center_lon=-4.55, zoom=10,
                           clicked=None, grid_meta=None, opacity=0.7,
                           fit_island=False):
    """
    Folium map with smoothly upsampled SoilGrids pH image overlay.
    Falls back to ISRIC WMS if the PNG is missing.
    The red pin is always drawn from session state so it survives reruns.
    When fit_island is True (first load only), the view is fitted to IoM bounds;
    otherwise centre/zoom from the caller are respected so user zoom is kept.
    """
    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=int(zoom),
        tiles="OpenStreetMap",
    )
    if fit_island:
        m.fit_bounds([[54.04, -4.85], [54.43, -4.30]])

    png = find_overlay_png()
    if png and grid_meta:
        ImageOverlay(
            name="Soil pH 0–5 cm",
            image=png,
            bounds=[
                [grid_meta["south"], grid_meta["west"]],
                [grid_meta["north"], grid_meta["east"]],
            ],
            opacity=float(opacity),
            interactive=False,
            cross_origin=False,
        ).add_to(m)
    else:
        folium.raster_layers.WmsTileLayer(
            url="https://maps.isric.org/mapserv?map=/map/phh2o.map",
            layers="phh2o_0-5cm_mean",
            name="Soil pH 0–5 cm (SoilGrids WMS)",
            fmt="image/png",
            transparent=True,
            opacity=float(opacity),
            version="1.3.0",
            attr="SoilGrids / ISRIC (CC-BY 4.0)",
        ).add_to(m)

    # Always draw the pin from session state so it stays until the next click
    if clicked and clicked.get("lat") is not None:
        popup_html = f"{clicked['lat']:.5f}, {clicked['lon']:.5f}"
        if clicked.get("ph") is not None:
            popup_html += f"<br><b>pH ≈ {clicked['ph']:.1f}</b>"
        folium.Marker(
            [clicked["lat"], clicked["lon"]],
            popup=popup_html,
            tooltip="Selected location",
            icon=folium.Icon(color="red", icon="map-marker", prefix="glyphicon"),
        ).add_to(m)

    return m


# --- FRONTEND ---
st.set_page_config(page_title="Dr Pablo's Mushroom Magic!", page_icon="🍄", layout="wide")
st.title("🍄 Dr Pablo's Mushroom Magic!")
st.header("🍄 Zoom/Click on the map where you want to check growth conditions")
st.subheader("Then scroll down for probability info below")
st.caption("🍄 Use 🔍+/🔍− for a zoom that stays in place. Mouse-wheel or pinch to zoom is fine for browsing; click once to drop the pin.")
st.caption("🍄 Colour shading on the map indicates typical soil acidity over the island as per colour key lower down")

if "map_click" not in st.session_state:
    st.session_state.map_click = None  # {"lat", "lon", "ph"}
if "map_view" not in st.session_state:
    # centre + zoom remembered across clicks so the map does not jump
    st.session_state.map_view = {"lat": 54.23, "lon": -4.55, "zoom": 10}
if "ph_opacity" not in st.session_state:
    st.session_state.ph_opacity = 0.70
if "map_initialized" not in st.session_state:
    st.session_state.map_initialized = False

ph_grid = load_ph_grid()

st.sidebar.header("Target Organism Matrix")
selected_species = st.sidebar.selectbox("Select Target Variety:", list(SPECIES_MATRIX.keys()))
rules = SPECIES_MATRIX[selected_species]

st.sidebar.markdown("---")
st.sidebar.header("Navigation Panel")
app_mode = st.sidebar.radio(
    "Go to view:",
    ["📍 Hyperlocal Focused Zone", "⚖️ 3-Zone Head-to-Head Compare"],
)

# ==========================================
# VIEW A: HYPERLOCAL FOCUSED ZONE VIEW
# ==========================================
if app_mode == "📍 Hyperlocal Focused Zone":
    st.sidebar.markdown("---")
    st.sidebar.header("Location Source")
    location_mode = st.sidebar.radio(
        "Specify location by:",
        ["🗺️ Click on soil-pH map", "📮 Postcode Zone", "🔗 Google Maps Link / coords"],
    )

    zone_info = None
    location_error = None
    display_label = "Selected location"
    current_ph = None

    if location_mode == "📮 Postcode Zone":
        selected_outcode = st.sidebar.selectbox(
            "Select Target Postcode:", list(IOM_POSTCODE_DB.keys())
        )
        zone_info = IOM_POSTCODE_DB[selected_outcode]
        display_label = selected_outcode
        st.session_state.map_click = None

    elif location_mode == "🔗 Google Maps Link / coords":
        gmaps_input = st.sidebar.text_input(
            "Paste a full Maps URL or coordinates:",
            placeholder="54.150, -4.480  or  https://www.google.com/maps/@54.15,-4.48,15z",
        )
        st.sidebar.caption(
            "Short maps.app.goo.gl links won't work directly — open once in a browser "
            "and paste the full URL, or paste coordinates from a long-press pin."
        )
        display_label = "Pinned location"
        st.session_state.map_click = None
        if gmaps_input:
            try:
                lat, lon = google_maps_link_to_latlon(gmaps_input)
                bonus, elevation = get_elevation_bonus(lat, lon)
                zone_info = {
                    "name": "Pinned Google Maps location",
                    "lat": lat,
                    "lon": lon,
                    "upland_offset": bonus,
                }
                st.sidebar.caption(f"📐 Elevation: ~{elevation}m (terrain bonus: +{bonus})")
            except Exception as e:
                location_error = str(e)

    else:  # 🗺️ Click on soil-pH map
        st.sidebar.info(
            "Click anywhere on the map to set the location. "
            "Weather, scores and pH update automatically."
        )
        display_label = "Map pin"

        # Opacity in sidebar (deliberate; avoids fighting map clicks)
        st.session_state.ph_opacity = st.sidebar.slider(
            "Soil pH overlay opacity",
            min_value=0.0,
            max_value=1.0,
            value=float(st.session_state.ph_opacity),
            step=0.05,
            key="ph_opacity_slider",
            help="Drag to fade the pH colour layer in or out.",
        )

        # Explicit zoom controls — these survive pin drops reliably.
        # (Mouse-wheel zoom is client-side only and is lost on pin remount;
        #  use these buttons when you want a zoom level that sticks.)
        zc1, zc2, zc3, zc4 = st.columns(4)
        with zc1:
            if st.button("🔍−", help="Zoom out", use_container_width=True):
                st.session_state.map_view["zoom"] = max(8, int(st.session_state.map_view["zoom"]) - 1)
                st.session_state.map_initialized = True
        with zc2:
            if st.button("Island", help="Whole Isle of Man", use_container_width=True):
                st.session_state.map_view = {"lat": 54.23, "lon": -4.55, "zoom": 10}
                st.session_state.map_initialized = False
        with zc3:
            if st.button("🔍+", help="Zoom in", use_container_width=True):
                st.session_state.map_view["zoom"] = min(16, int(st.session_state.map_view["zoom"]) + 1)
                st.session_state.map_initialized = True
        with zc4:
            st.caption(f"Zoom **{int(st.session_state.map_view['zoom'])}**")

        view = st.session_state.map_view
        fit_island = not st.session_state.map_initialized

        fmap = build_clickable_ph_map(
            view["lat"], view["lon"], int(view["zoom"]),
            st.session_state.map_click, ph_grid,
            opacity=float(st.session_state.ph_opacity),
            fit_island=fit_island,
        )

        # ONLY last_clicked — do not return zoom/center.
        # Returning zoom/center forces a Streamlit rerun on every pan/zoom,
        # which remounts the map and causes the "2–3 attempts" behaviour.
        map_data = st_folium(
            fmap,
            width=None,
            height=520,
            returned_objects=["last_clicked"],
            key="iom_ph_map_stable",
        )

        # Single-click pin drop
        if map_data and map_data.get("last_clicked"):
            lat = float(map_data["last_clicked"]["lat"])
            lon = float(map_data["last_clicked"]["lng"])
            ph = sample_ph(lat, lon, ph_grid)
            new_click = {"lat": lat, "lon": lon, "ph": ph}
            prev = st.session_state.map_click
            if (
                prev is None
                or abs(prev["lat"] - lat) > 1e-6
                or abs(prev["lon"] - lon) > 1e-6
            ):
                st.session_state.map_click = new_click
                # Keep current zoom; only nudge centre toward the pin slightly
                # by storing pin as view centre so remount stays nearby
                st.session_state.map_view["lat"] = lat
                st.session_state.map_view["lon"] = lon
                st.session_state.map_initialized = True

        # Prominent legend + large pH block under the map
        st.markdown(ph_legend_html(current_ph), unsafe_allow_html=True)
        if ph_grid is None:
            st.warning(
                "Soil pH grid file (`iom_ph_grid.json`) not found next to the app "
                "— pH values cannot be sampled. Overlay may fall back to WMS."
            )

    if location_error:
        st.sidebar.error(f"⚠️ {location_error}")

    if zone_info is None:
        if location_mode == "🗺️ Click on soil-pH map":
            st.info("👆 Click on the map above to choose a location.")
        else:
            st.info("👈 Enter a location in the sidebar to see live data.")
        st.stop()

    if location_mode != "🗺️ Click on soil-pH map":
        st.subheader(f"🗺️ {display_label}")
        render_location_map(
            zone_info["lat"], zone_info["lon"], zone_info.get("name", display_label)
        )
        st.markdown("---")
    else:
        st.subheader("🗺️ Map pin selected")
        st.caption(f"**Location:** {zone_info['name']}")
        st.markdown("---")

    # ------------------------------------------------------------------
    # Live weather + scorecard FIRST (directly under map / pH)
    # ------------------------------------------------------------------
    left_panel, right_panel = st.columns(2)

    with left_panel:
        st.subheader(f"🎛️ Live Weather Snapshot: {display_label}")
        try:
            with st.spinner("Fetching live weather data..."):
                weather = fetch_live_weather(zone_info["lat"], zone_info["lon"])
            st.success("Live data loaded from Open-Meteo.")
            d_temp = weather["day_temp"]
            n_temp = weather["night_temp"]
            rain = weather["rain_48h"]
            frost_input = weather["had_frost"]

            st.metric("Day Temp Max (24h)", f"{d_temp}°C")
            st.metric("Night Temp Min (24h)", f"{n_temp}°C")
            st.metric("Rolling 48h Rain", f"{rain}mm")
            st.write(f"Ground frost detected: {'Yes ❄️' if frost_input else 'No'}")

            st.markdown("---")
            st.caption("Override values manually if you want to test hypothetical conditions:")
            d_temp = st.slider("Day Temp Max (°C)", 0.0, 25.0, float(d_temp), 0.5, key="ov_day")
            n_temp = st.slider("Night Temp Min (°C)", -5.0, 15.0, float(n_temp), 0.5, key="ov_night")
            rain = st.slider("Rolling 48h Rain (mm)", 0.0, 50.0, float(rain), 0.5, key="ov_rain")
            frost_input = st.toggle("Active Ground Frost Event?", value=frost_input, key="ov_frost")

        except Exception as e:
            st.error(f"⚠️ Could not fetch live weather data ({e}). Falling back to manual input.")
            d_temp = st.slider("Day Temp Max (°C)", 0.0, 25.0, 12.0, 0.5, key="fb_day")
            n_temp = st.slider("Night Temp Min (°C)", -5.0, 15.0, 7.0, 0.5, key="fb_night")
            rain = st.slider("Rolling 48h Rain (mm)", 0.0, 50.0, 15.0, 0.5, key="fb_rain")
            frost_input = st.toggle("Active Ground Frost Event?", value=False, key="fb_frost")

    with right_panel:
        st.subheader("WEATHER CONDITIONS SCORE %")

        prob, verdict, d_s, n_s, r_s = calculate_precise_index(
            d_temp, n_temp, rain, frost_input, zone_info["upland_offset"], rules
        )

        st.metric(label=f"NEW GROWTH* Fruiting Probability Score for {display_label}", value=f"{prob}%", border=True)

        st.progress(prob / 100)
        st.markdown(f"### NEW Growth Status*: {verdict}")
        st.subheader("*Check Graph below for recent conditions & likeliness of existing growth")
        st.markdown("---")

        st.markdown("### Score Breakdown")
        c1, c2, c3 = st.columns(3)
        c1.markdown(f"**Day Temp:** {d_temp}°C")
        c2.markdown(f"⭐ {int((d_s / 35) * 10)}/10")
        c3.caption(f"Target: {rules['day_min']}-{rules['day_max']}°C")
        c1, c2, c3 = st.columns(3)
        c1.markdown(f"**Night Temp:** {n_temp}°C")
        c2.markdown(f"⭐ {int((n_s / 25) * 10)}/10")
        c3.caption(f"Target: {rules['night_min']}-{rules['night_max']}°C")
        c1, c2, c3 = st.columns(3)
        c1.markdown(f"**Recent Rain:** {rain}mm")
        c2.markdown(f"⭐ {int((r_s / 40) * 10)}/10")
        c3.caption(f"Target: >={rules['rain_trigger']}mm")

        if zone_info["upland_offset"] > 0:
            st.info(
                f"⛰️ **Upland Grazing Bonus:** +{zone_info['upland_offset'] * 5}% "
                f"terrain advantage mapping applied to {zone_info['name']}."
            )

    # ------------------------------------------------------------------
    # Historical trend graph LAST (below scoring)
    # ------------------------------------------------------------------
    st.markdown("---")
    st.subheader(f"📈 Recent Weather & Forecast Trend: {display_label}")
    st.caption(
        "Use this to line up your own field observations against what the model expected at the time."
    )

    history_days = st.slider("Days of Weather History to include", 7, 30, 21, key="hist_days")

    try:
        with st.spinner("Loading trend data..."):
            dates, day_max, night_min, trend_rain = fetch_historical_daily(
                zone_info["lat"], zone_info["lon"], days_back=history_days
            )

        daily_scores = []
        for i in range(len(dates)):
            rain_48h = trend_rain[i] + (trend_rain[i - 1] if i > 0 else 0)
            had_frost = night_min[i] <= 0
            score, _, _, _, _ = calculate_precise_index(
                day_max[i], night_min[i], rain_48h, had_frost,
                zone_info["upland_offset"], rules,
            )
            daily_scores.append(score)

        fig = build_trend_chart(
            dates, day_max, night_min, trend_rain, daily_scores, selected_species
        )
        st.plotly_chart(fig, use_container_width=True)
        st.caption(
            "🟢 ≥80% · 🟡 50–79% · 🔴 <50% — the thick line's colour follows the same "
            "probability model as the scorecard above."
        )

    except Exception as e:
        st.error(f"⚠️ Could not load historical trend data ({e}).")

# ==========================================
# VIEW B: 3-ZONE HEAD-TO-HEAD COMPARISON
# ==========================================
else:
    st.header("⚖️ 3-Zone Head-to-Head Comparison Matrix")
    st.write(
        "Compares live current weather across three distinct Manx districts simultaneously. "
        "Each zone can be a postcode or a custom pinned location:"
    )

    def render_zone_picker(column, zone_num, default_postcode_index):
        column.markdown(f"**Zone {zone_num}**")
        mode = column.radio(
            "Source:",
            ["📮 Postcode", "🔗 Maps/Coords"],
            key=f"zone{zone_num}_mode",
            label_visibility="collapsed",
        )
        if mode == "📮 Postcode":
            outcode = column.selectbox(
                "Postcode:",
                list(IOM_POSTCODE_DB.keys()),
                index=default_postcode_index,
                key=f"zone{zone_num}_postcode",
                label_visibility="collapsed",
            )
            return {"label": outcode, **IOM_POSTCODE_DB[outcode]}
        else:
            raw = column.text_input(
                "Location:",
                placeholder="54.150, -4.480 or Maps URL",
                key=f"zone{zone_num}_custom",
                label_visibility="collapsed",
            )
            if not raw:
                return None
            try:
                lat, lon = google_maps_link_to_latlon(raw)
                bonus, elevation = get_elevation_bonus(lat, lon)
                column.caption(f"📐 ~{elevation}m (bonus +{bonus})")
                return {
                    "label": "Custom pin",
                    "name": "Custom pinned location",
                    "lat": lat,
                    "lon": lon,
                    "upland_offset": bonus,
                }
            except Exception as e:
                column.error(f"⚠️ {e}")
                return None

    sc1, sc2, sc3 = st.columns(3)
    comparison_configs = [
        render_zone_picker(sc1, 1, 0),
        render_zone_picker(sc2, 2, 3),
        render_zone_picker(sc3, 3, 8),
    ]

    panel_columns = st.columns(3)

    for idx, config in enumerate(comparison_configs):
        with panel_columns[idx]:
            if config is None:
                st.info("👈 Enter a location above to see data.")
                continue

            st.markdown(f"## {config['label']}")
            st.caption(f"**Region Location:** {config['name']}")

            try:
                weather = fetch_live_weather(config["lat"], config["lon"])
                prob, verdict, d_s, n_s, r_s = calculate_precise_index(
                    weather["day_temp"], weather["night_temp"],
                    weather["rain_48h"], weather["had_frost"],
                    config["upland_offset"], rules,
                )
                st.metric(label="Calculated Probability Index", value=f"{prob}%")
                st.progress(prob / 100)
                st.subheader(verdict.split(":")[0])
                st.markdown("---")
                st.markdown("**Live Metrics:**")
                st.markdown(f"• Day Temp Max: {weather['day_temp']}°C")
                st.markdown(f"• Night Temp Min: {weather['night_temp']}°C")
                st.markdown(f"• Rolling 48h Rain: {weather['rain_48h']}mm")
                st.markdown(f"• Terrain Weighting Bonus: +{config['upland_offset'] * 5}%")
            except Exception as e:
                st.error(f"⚠️ Live data unavailable ({e})")
