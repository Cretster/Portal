import streamlit as st
import requests
import re
import plotly.graph_objects as go
import pydeck as pdk
from datetime import datetime, timedelta

# 1. Species Parameter Matrix (edible species only)
SPECIES_MATRIX = {
    "🍄 Field Mushroom (Agaricus campestris)": {"day_min": 14, "day_max": 20, "night_min": 10, "night_max": 15, "rain_trigger": 10, "frost_kill": False},
    "🍄 Pearl Oyster (Pleurotus ostreatus)": {"day_min": 10, "day_max": 18, "night_min": 6, "night_max": 12, "rain_trigger": 15, "frost_kill": False}
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
    "IM9": {"name": "Castletown / Ballasalla / Port Erin", "lat": 54.08, "lon": -4.63, "upland_offset": 1}
}


@st.cache_data(ttl=1800)  # cache each location's weather for 30 minutes
def fetch_live_weather(lat, lon):
    """
    Pulls real day/night temps from the last 24h and rolling 48h rainfall
    from Open-Meteo (free, no API key required), plus whether frost
    occurred in the last 24h.
    """
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
    """
    Pulls daily max/min temp and daily rainfall totals from Open-Meteo,
    spanning `days_back` days of history through `days_forward` days
    of forecast, for trend plotting.
    """
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

    dates = data["time"]
    day_max = data["temperature_2m_max"]
    night_min = data["temperature_2m_min"]
    rain = data["precipitation_sum"]

    return dates, day_max, night_min, rain


def score_color(score):
    """Traffic-light colour for a given probability score."""
    if score >= 80:
        return "#2ecc71"  # green
    elif score >= 50:
        return "#f1c40f"  # amber
    else:
        return "#e74c3c"  # red


def build_trend_chart(dates, day_max, night_min, rain, scores, species_name):
    fig = go.Figure()

    # Temperature + rainfall lines (left axis for temp, right axis for rain)
    fig.add_trace(go.Scatter(x=dates, y=day_max, mode="lines+markers", name="Day Temp Max (°C)",
                              line=dict(color="#e67e22", width=2)))
    fig.add_trace(go.Scatter(x=dates, y=night_min, mode="lines+markers", name="Night Temp Min (°C)",
                              line=dict(color="#3498db", width=2)))
    fig.add_trace(go.Scatter(x=dates, y=rain, mode="lines+markers", name="Daily Rainfall (mm)",
                              line=dict(color="#9b59b6", width=2), yaxis="y2"))

    # Fruiting probability as a thick traffic-light coloured line, drawn as
    # one short coloured segment per day so colour changes along its length.
    first_seg = True
    for i in range(len(dates) - 1):
        seg_color = score_color((scores[i] + scores[i + 1]) / 2)
        fig.add_trace(go.Scatter(
            x=[dates[i], dates[i + 1]], y=[scores[i], scores[i + 1]],
            mode="lines", line=dict(color=seg_color, width=6),
            yaxis="y2", showlegend=first_seg,
            name="Fruiting Probability (%)",
            legendgroup="probability", hoverinfo="skip",
        ))
        first_seg = False
    # Markers on top of the probability line, individually coloured, with hover text
    fig.add_trace(go.Scatter(
        x=dates, y=scores, mode="markers", yaxis="y2",
        marker=dict(color=[score_color(s) for s in scores], size=9, line=dict(color="white", width=1)),
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
    """
    Extracts lat/lon from a Google Maps link, or from raw "lat, lon"
    coordinate text pasted directly (the most reliable option, e.g. from
    a long-press pin in the Maps app). Handles full links with an
    @lat,lon in the URL, place-pin links with !3d/!4d coordinates, and
    plain ?q=lat,lon links.

    Shortened goo.gl/maps.app.goo.gl links are deliberately hard to read
    programmatically — Google serves bots a page with no coordinates in
    it to force the redirect through their app/JS — so those aren't
    reliably supported here; the UI points users to a full URL or raw
    coordinates instead.
    """
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
        raise ValueError("Short goo.gl links can't be read directly — open it in a browser once and paste the full address-bar URL instead, or paste coordinates (e.g. 54.150, -4.480).")
    raise ValueError("Couldn't find coordinates in that link. Try pasting raw coordinates instead (e.g. 54.150, -4.480).")


@st.cache_data(ttl=86400)  # elevation doesn't change, cache for a day
def get_elevation_bonus(lat, lon):
    """
    Approximates the same 0-4 'upland grazing bonus' scale used for the
    postcode zones, but from real elevation data, for custom pinned
    locations that aren't in the postcode database.
    """
    resp = requests.get("https://api.open-meteo.com/v1/elevation", params={"latitude": lat, "longitude": lon}, timeout=10)
    resp.raise_for_status()
    elevation = resp.json()["elevation"][0]
    bonus = min(4, int(elevation // 50))
    return bonus, elevation


def calculate_precise_index(day_temp, night_temp, rain_48h, has_had_frost, bonus, s_rules):
    if s_rules["frost_kill"] and has_had_frost:
        return 0, "🔴 Season Terminated: Recent ground frost detected.", 0, 0, 0

    day_score = 35 if s_rules["day_min"] <= day_temp <= s_rules["day_max"] else (15 if (s_rules["day_min"] - 4) <= day_temp <= (s_rules["day_max"] + 3) else 0)
    night_score = 25 if s_rules["night_min"] <= night_temp <= s_rules["night_max"] else (10 if (s_rules["night_min"] - 4) <= night_temp <= (s_rules["night_max"] + 3) else 0)
    rain_score = 40 if rain_48h >= s_rules["rain_trigger"] else (20 if rain_48h >= (s_rules["rain_trigger"] / 2) else 0)

    total_score = min(day_score + night_score + rain_score + (bonus * 5), 100)

    if total_score >= 80:
        verdict = "🟩 EXCELLENT: Strong probability of active seasonal growth flushes."
    elif total_score >= 50:
        verdict = "🟨 MODERATE: Sporadic growth possible. Check damp, unfertilized slopes."
    else:
        verdict = "🟥 POOR: Unviable conditions. Highly unlikely to observe growth right now."

    return total_score, verdict, day_score, night_score, rain_score


def render_location_map(lat, lon, label):
    """
    Draws a map centred on the given point, with a soft highlighted circle
    around it (approximating the postcode/pinned zone) plus a solid marker
    at the exact coordinates.
    """
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


# --- FRONTEND ---
st.set_page_config(page_title="Manx Mushroom Portal", page_icon="🍄", layout="wide")
st.title("🍄 Real-Time Isle of Man Mycological Observation Dashboard")

st.sidebar.header("Target Organism Matrix")
selected_species = st.sidebar.selectbox("Select Target Variety:", list(SPECIES_MATRIX.keys()))
rules = SPECIES_MATRIX[selected_species]

st.sidebar.markdown("---")
st.sidebar.header("Navigation Panel")
app_mode = st.sidebar.radio("Go to view:", ["📍 Hyperlocal Focused Zone", "⚖️ 3-Zone Head-to-Head Compare"])

# ==========================================
# VIEW A: HYPERLOCAL FOCUSED ZONE VIEW
# ==========================================
if app_mode == "📍 Hyperlocal Focused Zone":
    st.sidebar.markdown("---")
    st.sidebar.header("Location Source")
    location_mode = st.sidebar.radio("Specify location by:", ["📮 Postcode Zone", "🔗 Google Maps Link"])

    zone_info = None
    location_error = None

    if location_mode == "📮 Postcode Zone":
        selected_outcode = st.sidebar.selectbox("Select Target Postcode:", list(IOM_POSTCODE_DB.keys()))
        zone_info = IOM_POSTCODE_DB[selected_outcode]
        display_label = selected_outcode

    else:  # Google Maps Link
        gmaps_input = st.sidebar.text_input(
            "Paste a full Maps URL or coordinates:",
            placeholder="54.150, -4.480  or  https://www.google.com/maps/@54.15,-4.48,15z",
        )
        st.sidebar.caption("Short maps.app.goo.gl links won't work directly — open once in a browser and paste the full URL, or paste coordinates from a long-press pin.")
        display_label = "Pinned location"
        if gmaps_input:
            try:
                lat, lon = google_maps_link_to_latlon(gmaps_input)
                bonus, elevation = get_elevation_bonus(lat, lon)
                zone_info = {"name": "Pinned Google Maps location", "lat": lat, "lon": lon, "upland_offset": bonus}
                st.sidebar.caption(f"📐 Elevation: ~{elevation}m (terrain bonus: +{bonus})")
            except Exception as e:
                location_error = str(e)

    if location_error:
        st.sidebar.error(f"⚠️ {location_error}")

    if zone_info is None:
        st.info("👈 Enter a location in the sidebar to see live data.")
        st.stop()

    st.subheader(f"🗺️ {display_label}")
    render_location_map(zone_info["lat"], zone_info["lon"], zone_info.get("name", display_label))
    st.markdown("---")

    st.subheader(f"📈 Historical & Forecast Trend: {display_label}")
    st.caption("Use this to line up your own field observations against what the model expected at the time.")

    history_days = st.slider("Days of history to include", 7, 30, 21)

    try:
        with st.spinner("Loading trend data..."):
            dates, day_max, night_min, trend_rain = fetch_historical_daily(zone_info["lat"], zone_info["lon"], days_back=history_days)

        # Build a rolling 48h rain figure per day for scoring, and compute the
        # daily probability score using the same model as the scorecard below.
        daily_scores = []
        for i in range(len(dates)):
            rain_48h = trend_rain[i] + (trend_rain[i - 1] if i > 0 else 0)
            had_frost = night_min[i] <= 0
            score, _, _, _, _ = calculate_precise_index(
                day_max[i], night_min[i], rain_48h, had_frost, zone_info["upland_offset"], rules
            )
            daily_scores.append(score)

        fig = build_trend_chart(dates, day_max, night_min, trend_rain, daily_scores, selected_species)
        st.plotly_chart(fig, use_container_width=True)
        st.caption("🟢 ≥80% · 🟡 50–79% · 🔴 <50% — the thick line's colour follows the same probability model as the scorecard below.")

    except Exception as e:
        st.error(f"⚠️ Could not load historical trend data ({e}).")

    st.markdown("---")

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
            d_temp = st.slider("Day Temp Max (°C)", 0.0, 25.0, float(d_temp), 0.5)
            n_temp = st.slider("Night Temp Min (°C)", -5.0, 15.0, float(n_temp), 0.5)
            rain = st.slider("Rolling 48h Rain (mm)", 0.0, 50.0, float(rain), 0.5)
            frost_input = st.toggle("Active Ground Frost Event?", value=frost_input)

        except Exception as e:
            st.error(f"⚠️ Could not fetch live weather data ({e}). Falling back to manual input.")
            d_temp = st.slider("Day Temp Max (°C)", 0.0, 25.0, 12.0, 0.5)
            n_temp = st.slider("Night Temp Min (°C)", -5.0, 15.0, 7.0, 0.5)
            rain = st.slider("Rolling 48h Rain (mm)", 0.0, 50.0, 15.0, 0.5)
            frost_input = st.toggle("Active Ground Frost Event?", value=False)

    with right_panel:
        st.subheader("🧬 Environmental Factor Scorecard")

        prob, verdict, d_s, n_s, r_s = calculate_precise_index(d_temp, n_temp, rain, frost_input, zone_info["upland_offset"], rules)

        st.metric(label=f"Fruiting Probability Index for {display_label}", value=f"{prob}%")
        st.progress(prob / 100)
        st.markdown(f"### Status: {verdict}")
        st.markdown("---")

        st.markdown("### Metric Breakdown Performance")
        c1, c2, c3 = st.columns(3)
        c1.markdown(f"**Day Temp:** {d_temp}°C"); c2.markdown(f"⭐ {int((d_s / 35) * 10)}/10"); c3.caption(f"Target: {rules['day_min']}-{rules['day_max']}°C")
        c1, c2, c3 = st.columns(3)
        c1.markdown(f"**Night Temp:** {n_temp}°C"); c2.markdown(f"⭐ {int((n_s / 25) * 10)}/10"); c3.caption(f"Target: {rules['night_min']}-{rules['night_max']}°C")
        c1, c2, c3 = st.columns(3)
        c1.markdown(f"**Recent Rain:** {rain}mm"); c2.markdown(f"⭐ {int((r_s / 40) * 10)}/10"); c3.caption(f"Target: >={rules['rain_trigger']}mm")

        if zone_info["upland_offset"] > 0:
            st.info(f"⛰️ **Upland Grazing Bonus:** +{zone_info['upland_offset'] * 5}% terrain advantage mapping applied to {zone_info['name']}.")

# ==========================================
# VIEW B: 3-ZONE HEAD-TO-HEAD COMPARISON
# ==========================================
else:
    st.header("⚖️ 3-Zone Head-to-Head Comparison Matrix")
    st.write("Compares live current weather across three distinct Manx districts simultaneously. Each zone can be a postcode or a custom pinned location:")

    def render_zone_picker(column, zone_num, default_postcode_index):
        column.markdown(f"**Zone {zone_num}**")
        mode = column.radio("Source:", ["📮 Postcode", "🔗 Maps/Coords"], key=f"zone{zone_num}_mode", label_visibility="collapsed")
        if mode == "📮 Postcode":
            outcode = column.selectbox("Postcode:", list(IOM_POSTCODE_DB.keys()), index=default_postcode_index, key=f"zone{zone_num}_postcode", label_visibility="collapsed")
            return {"label": outcode, **IOM_POSTCODE_DB[outcode]}
        else:
            raw = column.text_input("Location:", placeholder="54.150, -4.480 or Maps URL", key=f"zone{zone_num}_custom", label_visibility="collapsed")
            if not raw:
                return None
            try:
                lat, lon = google_maps_link_to_latlon(raw)
                bonus, elevation = get_elevation_bonus(lat, lon)
                column.caption(f"📐 ~{elevation}m (bonus +{bonus})")
                return {"label": "Custom pin", "name": "Custom pinned location", "lat": lat, "lon": lon, "upland_offset": bonus}
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
                    weather["day_temp"], weather["night_temp"], weather["rain_48h"], weather["had_frost"],
                    config["upland_offset"], rules
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
