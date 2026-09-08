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
# 1. Target Species Parameter Matrix
# ---------------------------------------------------------------------------
SPECIES_MATRIX = {
    "😁 'Field Mushroom' (Agaricus campestris)": {
        "day_min": 12.0, "day_max": 18.0,
        "night_min": 7.0, "night_max": 12.0,
        "rain_trigger": 12.0,       
        "frost_kill": True,
        "preferred_ph_min": 6.5, "preferred_ph_max": 7.5,
        "wind_tolerance": 22.0,     
        "fruiting_months": (8, 9, 10), 
        "decay_days": 5,
        "ideal_day": 15.0,
        "ideal_night": 9.5,
        "max_diurnal": 8.0
    },
    "🍄 'Liberty Cap' (Psilocybe semilanceata)": {
        "day_min": 10.0, "day_max": 17.0,          # daily max; ideal centre ~14 °C
        "night_min": 6.5, "night_max": 11.5,        # daily min; ideal centre ~9 °C
        "rain_trigger": 15.0,       
        "frost_kill": True,
        "preferred_ph_min": 4.0, "preferred_ph_max": 6.0,  # aligns with data-driven 4.0–6.0
        "wind_tolerance": 13.0,     
        "fruiting_months": (9, 10, 11, 12), 
        "decay_days": 3,
        "ideal_day": 14.0,
        "ideal_night": 9.0,
        "max_diurnal": 6.5
    }
}

# ---------------------------------------------------------------------------
# 2. Soil pH Grid Handlers
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
            if "ph_grid" not in data and "grid" in data:
                data["ph_grid"] = data["grid"]
            return data
    return None

def sample_ph(lat, lon, grid_meta):
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
    rows = [
        ("#d73027", "< 5.0", "Highly Acidic"),
        ("#fc8d59", "5.0 – 5.5", "Moderately Acidic (Optimal for Liberty Caps)"),
        ("#fee08b", "5.5 – 6.0", "Slightly Acidic (Excellent for Liberty Caps)"),
        ("#d9ef8b", "6.0 – 6.5", "Near-Neutral (Good crossover zone)"),
        ("#91cf60", "6.5 – 7.0", "Slightly Alkaline (Good for Field Mushrooms)"),
        ("#1a9850", "> 7.0", "Alkaline (Optimal for Field Mushrooms)"),
    ]
    parts = [
        '<div style="font-size:14px;line-height:1.55;'
        'padding:12px 14px;background:#f7f9fb;border:1px solid #d0d7de;'
        'border-radius:8px;margin:8px 0 16px 0">'
    ]
    parts.append("<b style='font-size:15px'>Soil pH (0–5 cm) — Ground Suitability</b><br><br>")
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
            '<div style="font-size:24px;font-weight:700;color:#555;margin-bottom:2px">'
            "Selected Soil pH:</div>"
            f'<div style="font-size:28px;font-weight:700;color:#1a5276;'
            f'letter-spacing:0.02em">pH ≈ {ph_value:.1f}</div>'
            "</div>"
        )
    else:
        parts.append('<div style="margin-top:12px;color:#666;font-size:13px">Click the map to check localized soil pH suitability.</div>')
    parts.append("</div>")
    return "".join(parts)
# ---------------------------------------------------------------------------
# 3. Weather API Processing Engine (Calibrated Unit Alignment)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=1800)
def fetch_live_weather(lat, lon):
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "temperature_2m,precipitation,relative_humidity_2m,wind_speed_10m",
        "past_days": 5, 
        "forecast_days": 1,
        "wind_speed_unit": "kn",
        "timezone": "auto",
    }
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    hourly = data["hourly"]
    temps = hourly["temperature_2m"]
    precip = hourly["precipitation"]
    humidity = hourly["relative_humidity_2m"]
    wind = hourly["wind_speed_10m"]
    times = hourly["time"]

    now = datetime.fromisoformat(times[-1])
    
    cutoff_24h = now - timedelta(hours=24)
    cutoff_48h = now - timedelta(hours=48)
    
    last_24h_temps = [t for tm, t in zip(times, temps) if datetime.fromisoformat(tm) >= cutoff_24h]
    last_48h_precip = [p for tm, p in zip(times, precip) if datetime.fromisoformat(tm) >= cutoff_48h]
    last_48h_rh = [h for tm, h in zip(times, humidity) if datetime.fromisoformat(tm) >= cutoff_48h]
    last_24h_wind = [w for tm, w in zip(times, wind) if datetime.fromisoformat(tm) >= cutoff_24h]

    lagged_rain = 0
    weights = [0.35, 0.25, 0.15, 0.15, 0.10]
    for idx, w in enumerate(weights):
        start = now - timedelta(days=idx+1)
        end = now - timedelta(days=idx)
        day_precip = sum(p for tm, p in zip(times, precip) if start <= datetime.fromisoformat(tm) < end)
        lagged_rain += day_precip * w

    if not last_24h_temps:
        last_24h_temps = temps[-24:] if len(temps) >= 24 else temps
    if not last_48h_rh:
        last_48h_rh = humidity[-48:] if len(humidity) >= 48 else humidity
    if not last_24h_wind:
        last_24h_wind = wind[-24:] if len(wind) >= 24 else wind

    return {
        "day_temp": round(max(last_24h_temps), 1) if last_24h_temps else 10.0,
        "night_temp": round(min(last_24h_temps), 1) if last_24h_temps else 5.0,
        "rain_48h": round(sum(last_48h_precip), 1),
        "lagged_rain_score": round(lagged_rain * 5, 1), 
        "avg_humidity_48h": round(sum(last_48h_rh) / len(last_48h_rh), 1) if last_48h_rh else 80.0,
        "max_wind_24h": round(max(last_24h_wind), 1) if last_24h_wind else 5.0,
        "had_frost": (min(last_24h_temps) <= 0) if last_24h_temps else False
    }
    
@st.cache_data(ttl=1800)
def fetch_historical_daily(lat, lon, days_back=7):
    url = "https://api.open-meteo.com/v1/forecast"
    
    clean_days = int(days_back)
    
    params = {
        "latitude": float(lat),
        "longitude": float(lon),
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
        "hourly": "relative_humidity_2m,wind_speed_10m",
        "past_days": clean_days,
        "forecast_days": 3,
        "wind_speed_unit": "kn",
        "timezone": "auto",
    }
    
    resp = requests.get(url, params=params, timeout=15)
    
    if resp.status_code != 200:
        raise ValueError(f"Open-Meteo rejected the historical request with Status {resp.status_code}: {resp.text[:200]}")
        
    raw = resp.json()
    if "daily" not in raw or "hourly" not in raw:
        raise ValueError(f"Unexpected Open-Meteo response structure: {list(raw.keys())}")
        
    daily = raw["daily"]
    
    h_time = raw["hourly"]["time"]
    h_rh = raw["hourly"]["relative_humidity_2m"]
    h_wind = raw["hourly"]["wind_speed_10m"]
    
    daily_rh_avg = []
    daily_wind_max = []
    
    for d_str in daily["time"]:
        day_start = datetime.fromisoformat(d_str)
        day_end = day_start + timedelta(days=1)
        
        rh_sub = [h for t, h in zip(h_time, h_rh) if day_start <= datetime.fromisoformat(t) < day_end]
        wind_sub = [w for t, w in zip(h_time, h_wind) if day_start <= datetime.fromisoformat(t) < day_end]
        
        daily_rh_avg.append(sum(rh_sub)/len(rh_sub) if rh_sub else 80.0)
        daily_wind_max.append(max(wind_sub) if wind_sub else 5.0)

    return daily["time"], daily["temperature_2m_max"], daily["temperature_2m_min"], daily["precipitation_sum"], daily_rh_avg, daily_wind_max

@st.cache_data(ttl=86400)
def get_elevation_bonus(lat, lon):
    try:
        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": lat,
            "longitude": lon,
            "daily": "temperature_2m_max",
            "forecast_days": 1,
            "timezone": "auto",
        }
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        elev = data.get("elevation", 0)
        return min(4, int(elev // 55)), elev
    except Exception:
        return 0, 0

# ---------------------------------------------------------------------------
# 4. Biological Algorithm Scoring Logic
# ---------------------------------------------------------------------------
def calculate_growth_and_presence_scores(day_temp, night_temp, rain_index, avg_rh, max_wind, has_frost, bonus, rules, selected_ph):
    current_month = datetime.now().month
    
    if current_month not in rules["fruiting_months"]:
        return 0, {}, "🔴 Suppressed: Outside seasonal fruiting calendar."

    if rules["frost_kill"] and has_frost:
        return 0, {}, "❄️ Season Terminated: Sub-zero frost destroyed surface structures."

    day_score = 30 if rules["day_min"] <= day_temp <= rules["day_max"] else (10 if (rules["day_min"] - 3) <= day_temp <= (rules["day_max"] + 3) else 0)
    night_score = 20 if rules["night_min"] <= night_temp <= rules["night_max"] else (5 if (rules["night_min"] - 2) <= night_temp <= (rules["night_max"] + 2) else 0)
    rain_score = 30 if rain_index >= rules["rain_trigger"] else (15 if rain_index >= (rules["rain_trigger"] / 2) else 0)
    
    if avg_rh >= 90:
        rh_modifier = 1.0
    elif avg_rh >= 83:
        rh_modifier = 0.7
    elif avg_rh >= 75:
        rh_modifier = 0.3
    else:
        rh_modifier = 0.05

    wind_penalty = 1.0
    if max_wind > rules["wind_tolerance"]:
        diff = max_wind - rules["wind_tolerance"]
        wind_penalty = max(0.4, 1.0 - (diff * 0.05))

    # Day–night temperature swing penalty (smaller swings preferred)
    diurnal = day_temp - night_temp
    max_diurnal = rules.get("max_diurnal", 7.0)
    if diurnal <= max_diurnal:
        diurnal_penalty = 1.0
    elif diurnal <= max_diurnal + 2:
        diurnal_penalty = 0.85
    else:
        diurnal_penalty = max(0.5, 1.0 - ((diurnal - max_diurnal) * 0.08))

    ph_modifier = 1.0
    if selected_ph is not None:
        if not (rules["preferred_ph_min"] <= selected_ph <= rules["preferred_ph_max"]):
            dist = min(abs(selected_ph - rules["preferred_ph_min"]), abs(selected_ph - rules["preferred_ph_max"]))
            ph_modifier = max(0.1, 1.0 - (dist * 0.5))

    base_gpi = day_score + night_score + rain_score + (bonus * 5)
    final_growth_score = int(min(base_gpi, 100) * rh_modifier * wind_penalty * diurnal_penalty * ph_modifier)

    if final_growth_score >= 75:
        verdict = "🟩 EXCELLENT: Ideal environmental alignment. Spontaneous fruiting likely."
    elif final_growth_score >= 45:
        verdict = "🟨 MODERATE: Conditional growth. Check unmanaged high-moisture valley points."
    else:
        verdict = "🟥 POOR: Unviable micro-climate. New surface eruption suppressed."

    breakdown = {
        "day": day_score, "night": night_score, "rain": rain_score,
        "rh_mod": rh_modifier, "wind_pen": wind_penalty,
        "diurnal_pen": diurnal_penalty, "ph_mod": ph_modifier
    }
    return final_growth_score, breakdown, verdict

def generate_decayed_presence_array(growth_scores, decay_span):
    """Models a time-lagged, decaying persistence index where field presence 

    peaks 1-2 days after an eruption event and decays gradually.
    """
    total_days = len(growth_scores)
    presence_scores = [0] * total_days
    
    # Loop through timeline to compute the lagged development index
    for i in range(total_days):
        # Scan backward over the last 2 days to check for an eruption trigger window
        possible_peaks = []
        
        # Check if an eruption happened 1 day ago (Mushrooms are half size / button stage)
        if i >= 1:
            possible_peaks.append(int(growth_scores[i-1] * 0.75))
            
        # Check if an eruption happened 2 days ago (Mushrooms are full size / peak abundance)
        if i >= 2:
            possible_peaks.append(int(growth_scores[i-2]))
            
        # Find the highest shifted value derived from recent weather triggers
        implied_peak = max(possible_peaks) if possible_peaks else 0
        
        # Let previous day's presence decay naturally according to species lifespan
        previous_presence = presence_scores[i-1] if i > 0 else 0
        decay_step = 100 / decay_span
        natural_decay = max(0, previous_presence - decay_step)
        
        # Current day score is either the new mature arrivals or the old decaying ones
        presence_scores[i] = int(max(implied_peak, natural_decay))
        
    return presence_scores

# ---------------------------------------------------------------------------
# 5. Mapping and Layout Setup
# ---------------------------------------------------------------------------
def build_dual_trend_chart(dates, day_max, night_min, rain, growth_array, presence_array, rh_avg, wind_max, species_name):
    fig = go.Figure()
    
    # 1. Primary Left Axis: Temperatures (°C)
    fig.add_trace(go.Scatter(x=dates, y=day_max, mode="lines+markers", name="<b>Day Temp Max (°C)</b>", line=dict(color="#e67e22", width=1.5)))
    fig.add_trace(go.Scatter(x=dates, y=night_min, mode="lines+markers", name="<b>Night Temp Min (°C)</b>", line=dict(color="#3498db", width=1.5)))
    
    # 2. Secondary Right Axis: Volume Index / Probabilities (%) & Rain (mm)
    fig.add_trace(go.Bar(x=dates, y=rain, name="<b>Daily Rain (mm)</b>", marker_color="rgba(155, 89, 182, 0.25)", yaxis="y2"))
    fig.add_trace(go.Scatter(x=dates, y=growth_array, mode="lines", name="<b>⚡ ACTIVE NEW GROWTH %</b>", line=dict(color="#e74c3c", width=9, dash="dot"), yaxis="y2"))
    fig.add_trace(go.Scatter(x=dates, y=presence_array, mode="lines", name="<b>🍄 EXISTING GROWTH FIND %</b>", line=dict(color="#2ecc71", width=9), yaxis="y2"))
    fig.add_trace(go.Scatter(x=dates, y=rh_avg, mode="lines", name="<b>💧 Avg Air Humidity (%)</b>", line=dict(color="#1abc9c", width=6, dash="dash"), yaxis="y2"))
    
    # 3. Tertiary Right Axis: Wind Speed (knots)
    fig.add_trace(go.Scatter(x=dates, y=wind_max, mode="lines", name="<b>💨 Peak Wind (knots)</b>", line=dict(color="#7f8c8d", width=1.5, dash="dashdot"), yaxis="y3"))

    fig.update_layout(
        title=f"Growth Mapping Trends.  The main green and dotted red lines are the ones to focus on for showing growth chances ~— {species_name}",
        xaxis=dict(title="Timeline Window", domain=[0, 0.85]),
        yaxis=dict(title="Temperature Range (°C)", side="left"),
        yaxis2=dict(
            title="Probability / Moisture Volume Index (%) / Rain (mm)",
            overlaying="y", side="right", range=[0, 105]
        ),
        yaxis3=dict(
            title="Wind Velocity (knots)",
            overlaying="y", side="right", anchor="free", position=0.93
        ),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        height=550,
    )
    return fig



def find_overlay_png():
    candidates = [Path(__file__).parent / "iom_ph_overlay.png", Path("iom_ph_overlay.png"), Path("/mount/src/portal/iom_ph_overlay.png")]
    for p in candidates:
        if p.exists(): return str(p)
    return None

def build_clickable_ph_map(center_lat=54.23, center_lon=-4.55, zoom=10, clicked=None, grid_meta=None, opacity=0.7, fit_island=False):
    m = folium.Map(location=[center_lat, center_lon], zoom_start=int(zoom), tiles="OpenStreetMap")
    if fit_island:
        m.fit_bounds([[54.04, -4.85], [54.43, -4.30]])

    png = find_overlay_png()
    if png and grid_meta:
        ImageOverlay(
            name="Soil pH 0–5 cm", image=png,
            bounds=[[grid_meta["south"], grid_meta["west"]], [grid_meta["north"], grid_meta["east"]]],
            opacity=float(opacity), interactive=False, cross_origin=False,
        ).add_to(m)
    
    if clicked and clicked.get("lat") is not None:
        popup_html = f"Lat: {clicked['lat']:.4f}, Lon: {clicked['lon']:.4f}"
        if clicked.get("ph") is not None:
            popup_html += f"<br><b>pH ≈ {clicked['ph']:.1f}</b>"
        folium.Marker([clicked["lat"], clicked["lon"]], popup=popup_html, icon=folium.Icon(color="red")).add_to(m)
    return m

def ph_focus_sample_points(grid_meta, ph_min=5.0, ph_max=7.5, stride=4):
    if grid_meta is None: return []
    ph_grid = grid_meta.get("ph_grid") or grid_meta.get("grid")
    if not ph_grid: return []
    west, south, east, north = grid_meta["west"], grid_meta["south"], grid_meta["east"], grid_meta["north"]
    width, height = grid_meta["width"], grid_meta["height"]
    points = []
    for y in range(0, height, stride):
        for x in range(0, width, stride):
            ph = ph_grid[y][x]
            if ph and ph_min <= ph <= ph_max:
                lat = north - (y + 0.5) / height * (north - south)
                lon = west + (x + 0.5) / width * (east - west)
                points.append({"lat": lat, "lon": lon, "ph": ph})
    return points

def build_growth_conditions_map(points_with_scores, zoom=10):
    m = folium.Map(location=[54.23, -4.55], zoom_start=zoom, tiles="OpenStreetMap")
    m.fit_bounds([[54.04, -4.85], [54.43, -4.30]])
    for pt in points_with_scores:
        score = pt.get("score")
        colour = "#2ecc71" if score >= 75 else "#f1c40f"
        popup = f"<b>Lingering Presence: {score}%</b><br>Soil pH: {pt['ph']:.1f}<br>Elev: {int(pt.get('elevation',0))}m"
        folium.CircleMarker(
            location=[pt["lat"], pt["lon"]], radius=8, color=colour, weight=1,
            fill=True, fill_color=colour, fill_opacity=0.75, popup=folium.Popup(popup, max_width=200)
        ).add_to(m)
    return m

# ---------------------------------------------------------------------------
# 6. Streamlit Frontend Mounting
# ---------------------------------------------------------------------------
st.set_page_config(page_title="Dr Pablo's Mushroom Logic Model", page_icon="🍄", layout="wide")
st.title("🍄 Dr Pablo's Mushroom Magic")
st.caption("Advanced Time-Lagged Predictive Biological Growth Algorithm — Isle of Man Exclusive Spatial Grid.")

if "map_click" not in st.session_state: st.session_state.map_click = None
if "map_view" not in st.session_state: st.session_state.map_view = {"lat": 54.23, "lon": -4.55, "zoom": 10}
if "ph_opacity" not in st.session_state: st.session_state.ph_opacity = 0.65
if "map_initialized" not in st.session_state: st.session_state.map_initialized = False
if "map_version" not in st.session_state: st.session_state.map_version = 0
if "handled_click_id" not in st.session_state: st.session_state.handled_click_id = None

ph_grid = load_ph_grid()

st.sidebar.header("Target Biological Profile")
selected_species = st.sidebar.selectbox("Select Fungal Variety to Map:", list(SPECIES_MATRIX.keys()))
rules = SPECIES_MATRIX[selected_species]

st.sidebar.markdown("---")
st.sidebar.info("📌 **Target Matrix Info:**\nField Mushrooms require neutral-alkaline fields. Liberty Caps require undisturbed acidic pasture zones and collapse under fast drying wind profiles.")

st.sidebar.header("Map Adjustments")
st.session_state.ph_opacity = st.sidebar.slider(
    "Soil pH overlay opacity", min_value=0.0, max_value=1.0,
    value=float(st.session_state.ph_opacity), step=0.05
)

# Render Map Canvas Focus Tweaks Controls
zc1, zc2, zc3, zc4 = st.columns(4)
with zc1:
    if st.button("🔍−", use_container_width=True):
        st.session_state.map_view["zoom"] = max(8, int(st.session_state.map_view["zoom"]) - 1)
        st.session_state.map_initialized = True
with zc2:
    if st.button("Reset View (Island Focus)", use_container_width=True):
        st.session_state.map_view = {"lat": 54.23, "lon": -4.55, "zoom": 10}
        st.session_state.map_initialized = False
with zc3:
    if st.button("🔍+", use_container_width=True):
        st.session_state.map_view["zoom"] = min(16, int(st.session_state.map_view["zoom"]) + 1)
        st.session_state.map_initialized = True
with zc4:
    st.caption(f"Map Canvas Zoom Context Level: **{int(st.session_state.map_view['zoom'])}**")

view = st.session_state.map_view
fit_island = not st.session_state.map_initialized

fmap = build_clickable_ph_map(
    view["lat"], view["lon"], int(view["zoom"]),
    st.session_state.map_click, ph_grid,
    opacity=float(st.session_state.ph_opacity), fit_island=fit_island
)

map_data = st_folium(
    fmap, width=None, height=480,
    returned_objects=["last_clicked"],
    key=f"iom_ph_map_v{st.session_state.map_version}"
)

if map_data and map_data.get("last_clicked"):
    lat = float(map_data["last_clicked"]["lat"])
    lon = float(map_data["last_clicked"]["lng"])
    click_id = (round(lat, 4), round(lon, 4))
    if st.session_state.handled_click_id != click_id:
        ph = sample_ph(lat, lon, ph_grid)
        st.session_state.map_click = {"lat": lat, "lon": lon, "ph": ph}
        st.session_state.map_view["lat"] = lat
        st.session_state.map_view["lon"] = lon
        st.session_state.map_initialized = True
        st.session_state.handled_click_id = click_id
        st.session_state.map_version += 1
        st.rerun()

current_ph = st.session_state.map_click.get("ph") if st.session_state.map_click else None
st.markdown(ph_legend_html(current_ph), unsafe_allow_html=True)

if not st.session_state.map_click:
    st.info("👆 Click anywhere inside the Isle of Man boundaries on the map canvas above to load live data.")
    st.stop()

lat = st.session_state.map_click["lat"]
lon = st.session_state.map_click["lon"]
bonus, elevation = get_elevation_bonus(lat, lon)

score_placeholder = st.container()
# ---------------------------------------------------------------------------
# 7. Trend Visualization Timeline Generation
# ---------------------------------------------------------------------------
st.markdown("---")
st.subheader("📈 Growth Probability and Condition Trends")
st.caption("Plots the immediate eruption switch threshold versus lingering field presence over a rolling window.")

history_days = st.slider("Days of Weather History to include", 7, 30, 9, key="hist_days_slider")

try:
    dates, h_day, h_night, h_rain, h_rh, h_wind = fetch_historical_daily(lat, lon, days_back=history_days)
    
    historical_growth_stream = []
    for i in range(len(dates)):
        h_rain_index = sum(h_rain[max(0, i-j)] * w for j, w in enumerate([0.35, 0.25, 0.15, 0.15, 0.10])) * 5
        h_frost = h_night[i] <= 0
        
        g_sc, _, _ = calculate_growth_and_presence_scores(
            h_day[i], h_night[i], h_rain_index, h_rh[i], h_wind[i], h_frost, bonus, rules, current_ph
        )
        historical_growth_stream.append(g_sc)
        
    historical_presence_stream = generate_decayed_presence_array(historical_growth_stream, rules["decay_days"])
    
    # Clean injection passing humidity and wind variables to the chart trace
    trend_chart = build_dual_trend_chart(
        dates, h_day, h_night, h_rain, historical_growth_stream, 
        historical_presence_stream, h_rh, h_wind, selected_species
    )
    st.plotly_chart(trend_chart, use_container_width=True)
    st.info("💡 **How to interpret the trend chart:** The dotted Red line shows spikes when conditions were perfect for *new* growth. The solid Green line indicates field presence; notice how it lingers and drops slowly over a few days even after the weather shifts.  Click on any line name in the Key to hide/unhide the line.  Double click them to hide/unhide all other lines.")
except Exception as e:
    st.error(f"Could not build integrated visual model timelines: {e}")


# ---------------------------------------------------------------------------
# 8. Regional Discovery Macro Scanning Engine
# ---------------------------------------------------------------------------
st.markdown("---")
st.subheader("🤔 Island-wide High-Probability Macro Spatial Samples")
st.caption("Scans coordinates around the Isle of Man matching target geochemical profiles to filter regions showing high presence.")

sample_pts = ph_focus_sample_points(ph_grid, ph_min=rules["preferred_ph_min"], ph_max=rules["preferred_ph_max"], stride=4)
if len(sample_pts) > 40:
    step = max(1, len(sample_pts) // 40)
    sample_pts = sample_pts[::step]

viable_spots = []
if sample_pts:
    with st.spinner("Computing regional presence algorithms across spatial vectors..."):
        for pt in sample_pts:
            try:
                w = fetch_live_weather(pt["lat"], pt["lon"])
                b, _ = get_elevation_bonus(pt["lat"], pt["lon"])
                sc, _, _ = calculate_growth_and_presence_scores(w["day_temp"], w["night_temp"], w["lagged_rain_score"], w["avg_humidity_48h"], w["max_wind_24h"], w["had_frost"], b, rules, pt["ph"])
                if sc >= 45:
                    viable_spots.append({**pt, "score": sc, "elevation": b * 55})
            except:
                continue
                
if viable_spots:
    gmap = build_growth_conditions_map(viable_spots, zoom=10)
    st_folium(gmap, width=None, height=450, returned_objects=[], key="iom_regional_discovery_canvas")
    st.success(f"Discovered **{len(viable_spots)}** highly prospective search corridors within target parameters across the island.")
else:
    st.warning("No high-probability zones currently verified island-wide within target chemical profiles.")

# ---------------------------------------------------------------------------
# 9. Manual Sandbox Adjustments Controls (Placed at Bottom)
# ---------------------------------------------------------------------------
st.markdown("---")
st.subheader("🧪 Sandbox Area: Manual Condition Testing Overrides")
st.caption("Adjust these sliders to simulate custom weather fronts and observe how the score metrics fluctuate.")

try:
    with st.spinner("Streaming metrics from Open-Meteo..."):
        weather = fetch_live_weather(lat, lon)
    d_temp = weather["day_temp"]
    n_temp = weather["night_temp"]
    rain_index = weather["lagged_rain_score"]
    avg_rh = weather["avg_humidity_48h"]
    max_wind = weather["max_wind_24h"]
    frost_input = weather["had_frost"]
except:
    d_temp, n_temp, rain_index, avg_rh, max_wind, frost_input = 11.0, 6.0, 20.0, 92.0, 8.0, False

ov_col1, ov_col2 = st.columns(2)
with ov_col1:
    d_temp_sim = st.slider("Simulated Day Temp Max (°C)", 0.0, 25.0, float(d_temp), 0.5)
    n_temp_sim = st.slider("Simulated Night Temp Min (°C)", -5.0, 15.0, float(n_temp), 0.5)
    rain_sim = st.slider("Simulated 5-Day Soil Water Charge", 0.0, 50.0, float(rain_index), 0.5)
with ov_col2:
    avg_rh_sim = st.slider("Simulated 48h Mean Humidity (%)", 40.0, 100.0, float(avg_rh), 1.0)
    max_wind_sim = st.slider("Simulated Wind Velocities (knots)", 0.0, 40.0, float(max_wind), 0.5)
    frost_sim = st.toggle("Override: Hard Ground Frost State Active", value=frost_input)

growth_score, breakdown, verdict = calculate_growth_and_presence_scores(
    d_temp_sim, n_temp_sim, rain_sim, avg_rh_sim, max_wind_sim, frost_sim, bonus, rules, current_ph
)

# --- helpers for traffic-light colour + short advice ---
def _status_colour(level):
    return {"good": "#1a7f37", "moderate": "#b78100", "poor": "#cf222e"}.get(level, "#57606a")

def _metric_html(label, value_str, level, advice):
    col = _status_colour(level)
    return (
        f'<div style="margin:6px 0 10px 0;padding:8px 10px;border-left:4px solid {col};'
        f'background:#f6f8fa;border-radius:4px">'
        f'<span style="font-weight:600;color:{col}">{label}: {value_str}</span><br>'
        f'<span style="font-size:13px;color:#444">{advice}</span></div>'
    )

diurnal = round(d_temp_sim - n_temp_sim, 1)
ideal_day = rules.get("ideal_day", (rules["day_min"] + rules["day_max"]) / 2)
ideal_night = rules.get("ideal_night", (rules["night_min"] + rules["night_max"]) / 2)
max_diurnal = rules.get("max_diurnal", 7.0)

# Day temp status
if rules["day_min"] <= d_temp_sim <= rules["day_max"]:
    day_level, day_advice = "good", f"Within preferred daytime range ({rules['day_min']}–{rules['day_max']}°C). Ideal centre ≈ {ideal_day}°C."
elif (rules["day_min"] - 3) <= d_temp_sim <= (rules["day_max"] + 3):
    day_level, day_advice = "moderate", f"Marginal. Preferred daytime max is {rules['day_min']}–{rules['day_max']}°C (ideal ≈ {ideal_day}°C)."
else:
    day_level = "poor"
    if d_temp_sim > rules["day_max"]:
        day_advice = f"TOO WARM. Daytime max is above the preferred {rules['day_max']}°C (ideal ≈ {ideal_day}°C)."
    else:
        day_advice = f"TOO COOL. Daytime max is below the preferred {rules['day_min']}°C (ideal ≈ {ideal_day}°C)."

# Night temp status
if rules["night_min"] <= n_temp_sim <= rules["night_max"]:
    night_level, night_advice = "good", f"Within preferred night range ({rules['night_min']}–{rules['night_max']}°C). Ideal centre ≈ {ideal_night}°C."
elif (rules["night_min"] - 2) <= n_temp_sim <= (rules["night_max"] + 2):
    night_level, night_advice = "moderate", f"Marginal. Preferred night min is {rules['night_min']}–{rules['night_max']}°C (ideal ≈ {ideal_night}°C)."
else:
    night_level = "poor"
    if n_temp_sim > rules["night_max"]:
        night_advice = f"TOO WARM at night. Preferred night range is {rules['night_min']}–{rules['night_max']}°C (ideal ≈ {ideal_night}°C)."
    else:
        night_advice = f"TOO COLD at night. Preferred night range is {rules['night_min']}–{rules['night_max']}°C (ideal ≈ {ideal_night}°C)."

# Diurnal difference
if diurnal <= max_diurnal:
    diur_level, diur_advice = "good", f"Day–night difference ({diurnal}°C) is within preferred limit (≤ {max_diurnal}°C). Smaller swings favour fruiting."
elif diurnal <= max_diurnal + 2:
    diur_level, diur_advice = "moderate", f"Day–night difference ({diurnal}°C) is a little high. Preferred ≤ {max_diurnal}°C."
else:
    diur_level, diur_advice = "poor", f"Large day–night swing ({diurnal}°C). Preferred ≤ {max_diurnal}°C; big swings reduce fruiting likelihood."

# Rain / soil water
if rain_sim >= rules["rain_trigger"]:
    rain_level, rain_advice = "good", f"Soil water charge meets or exceeds trigger (≥ {rules['rain_trigger']} pts). Recent moisture is favourable."
elif rain_sim >= rules["rain_trigger"] / 2:
    rain_level, rain_advice = "moderate", f"Moderate moisture. Full trigger is ≥ {rules['rain_trigger']} pts."
else:
    rain_level, rain_advice = "poor", f"TOO DRY. Soil water charge is below half the trigger ({rules['rain_trigger']} pts)."

# Humidity
if avg_rh_sim >= 90:
    rh_level, rh_advice = "good", "High humidity (≥ 90 %) strongly supports pin formation and development."
elif avg_rh_sim >= 83:
    rh_level, rh_advice = "moderate", "Humidity is acceptable (83–90 %). ≥ 90 % is ideal."
elif avg_rh_sim >= 75:
    rh_level, rh_advice = "moderate", "Humidity is on the low side. ≥ 83 % preferred, ≥ 90 % ideal."
else:
    rh_level, rh_advice = "poor", "TOO DRY AIR. Humidity below 75 % strongly suppresses surface growth."

# Wind
if max_wind_sim <= rules["wind_tolerance"]:
    wind_level, wind_advice = "good", f"Wind within tolerance (≤ {rules['wind_tolerance']} kn). Low desiccation risk."
elif max_wind_sim <= rules["wind_tolerance"] + 5:
    wind_level, wind_advice = "moderate", f"Wind a little high. Preferred ≤ {rules['wind_tolerance']} kn."
else:
    wind_level, wind_advice = "poor", f"TOO WINDY. Peak wind above tolerance ({rules['wind_tolerance']} kn) increases drying and can abort pins."

# Frost
if frost_sim and rules["frost_kill"]:
    frost_level, frost_advice = "poor", "Hard frost is active — surface fruit bodies are likely killed or prevented."
else:
    frost_level, frost_advice = "good", "No hard frost detected."

# pH
if current_ph is None:
    ph_level, ph_advice = "moderate", "No local pH sample available for this point."
    ph_str = "n/a"
else:
    ph_str = f"{current_ph:.1f}"
    if rules["preferred_ph_min"] <= current_ph <= rules["preferred_ph_max"]:
        ph_level, ph_advice = "good", f"Soil pH inside preferred band ({rules['preferred_ph_min']}–{rules['preferred_ph_max']})."
    else:
        dist = min(abs(current_ph - rules["preferred_ph_min"]), abs(current_ph - rules["preferred_ph_max"]))
        if dist <= 0.5:
            ph_level, ph_advice = "moderate", f"pH close to preferred band ({rules['preferred_ph_min']}–{rules['preferred_ph_max']})."
        else:
            ph_level, ph_advice = "poor", f"pH outside preferred band ({rules['preferred_ph_min']}–{rules['preferred_ph_max']})."

with score_placeholder:
    left_panel, right_panel = st.columns(2)
    with left_panel:
        st.subheader("🎛️ Live Weather & Site Metrics")
        st.markdown(_metric_html("Day Temp Max", f"{d_temp_sim}°C", day_level, day_advice), unsafe_allow_html=True)
        st.markdown(_metric_html("Night Temp Min", f"{n_temp_sim}°C", night_level, night_advice), unsafe_allow_html=True)
        st.markdown(_metric_html("Day–Night Difference", f"{diurnal}°C", diur_level, diur_advice), unsafe_allow_html=True)
        st.markdown(_metric_html("Soil Hydration Score", f"{rain_sim} pts", rain_level, rain_advice), unsafe_allow_html=True)
        st.markdown(_metric_html("Mean Relative Humidity", f"{avg_rh_sim}%", rh_level, rh_advice), unsafe_allow_html=True)
        st.markdown(_metric_html("Peak Wind Speed", f"{max_wind_sim} kn", wind_level, wind_advice), unsafe_allow_html=True)
        st.markdown(_metric_html("Frost Active", "Yes ❄️" if frost_sim else "No", frost_level, frost_advice), unsafe_allow_html=True)
        st.markdown(_metric_html("Soil pH (0–5 cm)", ph_str, ph_level, ph_advice), unsafe_allow_html=True)
        if bonus > 0:
            st.info(f"⛰️ **Upland Altitude Edge Advantage applied:** +{bonus * 5}% probability bias (elevation ≈ {int(elevation)} m).")

    with right_panel:
        st.subheader("Calculated Probability Results")
        st.metric(label="IMMEDIATE NEW PIN ERUPTION PROBABILITY", value=f"{growth_score}%", border=True)
        st.progress(growth_score / 100)
        st.markdown(f"### Real-Time Verdict: \n*{verdict}*")
        
        st.caption(
            "Note: This live % is the **new-growth** potential for the current weather window. "
            "On the trend chart the dotted red line is the same concept (day-by-day new growth). "
            "The solid green line is **lingering field presence** — it stays high for a few days after a good growth day even if today’s new-growth score is lower. "
            "That is why the two numbers can differ."
        )
        
        if breakdown:
            st.markdown("#### Weighting Influences:")
            st.caption(
                f"Temp Yield: {breakdown.get('day',0) + breakdown.get('night',0)}/50 pts | "
                f"Soil Water: {breakdown.get('rain',0)}/30 pts | "
                f"Humidity Scalar: x{breakdown.get('rh_mod',1):.2f} | "
                f"Wind Penalty: x{breakdown.get('wind_pen',1):.2f} | "
                f"Diurnal Penalty: x{breakdown.get('diurnal_pen',1):.2f} | "
                f"pH Multiplier: x{breakdown.get('ph_mod',1):.2f}"
            )
