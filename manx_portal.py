import streamlit as st
import requests
import re
import plotly.graph_objects as go
import pydeck as pdk
import streamlit.components.v1 as components
import urllib.parse
from datetime import datetime, timedelta

# 1. Species Parameter Matrix (edible species only)
SPECIES_MATRIX = {
    "🍄 Liberty Cap (Psilocybe semilanceata)": {"day_min": 8, "day_max": 14, "night_min": 5, "night_max": 9, "rain_trigger": 12, "frost_kill": True},
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


@st.cache_data(ttl=1800)
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

    # Fruiting probability as a thick traffic-light coloured line
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
    # Markers on top of the probability line
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
    coordinate text pasted directly.
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


@st.cache_data(ttl=86400)
def get_elevation_bonus(lat, lon):
    """
    Approximates the same 0-4 'upland grazing bonus' scale used for the
    postcode zones, but from real elevation data.
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


def ph_map_selector():
    """
    Renders the pH map and automatically selects the location when clicked.
    Returns coordinates and triggers the app to update.
    """
    # Use session state to store coordinates
    if 'ph_map_coords' not in st.session_state:
        st.session_state.ph_map_coords = ""
    
    # Read the HTML file
    try:
        with open('WorkingPHmap.html', 'r') as f:
            map_html = f.read()
    except FileNotFoundError:
        st.warning("⚠️ WorkingPHmap.html not found. Please make sure the file is in the same directory.")
        return ""
    
    # Add JavaScript to the map to capture clicks and auto-submit
    click_capture_script = """
    <script>
    // Add click handler to send coordinates to parent
    (function() {
        // Wait for map to be ready
        let attempts = 0;
        const maxAttempts = 20;
        
        function setupClickHandler() {
            const mapContainer = document.getElementById('map');
            if (mapContainer) {
                // Add click listener to the map container
                mapContainer.addEventListener('click', function(e) {
                    // Wait for the popup to appear with coordinates
                    setTimeout(function() {
                        const popup = document.querySelector('.leaflet-popup-content');
                        if (popup) {
                            const content = popup.textContent;
                            const match = content.match(/(-?\\d+\\.\\d+),\\s*(-?\\d+\\.\\d+)/);
                            if (match) {
                                const lat = parseFloat(match[1]);
                                const lng = parseFloat(match[2]);
                                if (!isNaN(lat) && !isNaN(lng)) {
                                    const coordStr = lat.toFixed(6) + ', ' + lng.toFixed(6);
                                    // Send to parent (Streamlit)
                                    if (window.parent) {
                                        window.parent.postMessage({
                                            type: 'streamlit:setComponentValue',
                                            value: coordStr
                                        }, '*');
                                    }
                                }
                            }
                        }
                    }, 300);
                });
                return true;
            }
            return false;
        }
        
        function trySetup() {
            if (setupClickHandler()) {
                return;
            }
            attempts++;
            if (attempts < maxAttempts) {
                setTimeout(trySetup, 500);
            }
        }
        
        // Start trying to set up the click handler
        setTimeout(trySetup, 1000);
    })();
    </script>
    """
    
    # Insert the script before </body>
    map_html_with_script = map_html.replace('</body>', click_capture_script + '</body>')
    
    # URL encode the map HTML for embedding in an iframe
    encoded_map = urllib.parse.quote(map_html_with_script)
    
    # Create a wrapper HTML with the map in an iframe
    wrapper_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <style>
            body {{ margin: 0; padding: 0; background: #f5f5f5; }}
            #container {{ height: 580px; width: 100%; position: relative; }}
            iframe {{ 
                width: 100%; 
                height: 100%; 
                border: none;
                border-radius: 8px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            }}
            #status {{
                position: absolute;
                bottom: 20px;
                left: 50%;
                transform: translateX(-50%);
                background: rgba(0,0,0,0.75);
                color: white;
                padding: 6px 16px;
                border-radius: 16px;
                font-size: 13px;
                font-family: system-ui, sans-serif;
                pointer-events: none;
                z-index: 10;
            }}
        </style>
    </head>
    <body>
        <div id="container">
            <iframe id="mapFrame" src="data:text/html;charset=utf-8,{encoded_map}"></iframe>
            <div id="status">🖱️ Click anywhere on land to select location</div>
        </div>
        <script>
            // Listen for messages from the iframe
            window.addEventListener('message', function(event) {{
                if (event.data && event.data.type === 'streamlit:setComponentValue') {{
                    const coordStr = event.data.value;
                    // Update status
                    document.getElementById('status').textContent = '📍 ' + coordStr;
                    // Update the Streamlit input
                    try {{
                        const inputs = window.parent.document.querySelectorAll('input[data-testid="stTextInput"]');
                        inputs.forEach(function(input) {{
                            if (input.id && input.id.includes('ph_map_coord_input')) {{
                                input.value = coordStr;
                                input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                            }}
                        }});
                    }} catch(e) {{
                        // Cross-origin issues, ignore
                    }}
                }}
            }});
        </script>
    </body>
    </html>
    """
    
    # Render the component
    components.html(wrapper_html, height=620, scrolling=False)
    
    # Create a text input to store the coordinates
    coord_input = st.text_input(
        "📍 Selected Coordinates:",
        value=st.session_state.ph_map_coords,
        placeholder="Click on the map above to select a location",
        key="ph_map_coord_input",
        help="Click on the pH map above to auto-fill coordinates",
        label_visibility="collapsed"
    )
    
    # Update session state
    if coord_input != st.session_state.ph_map_coords:
        st.session_state.ph_map_coords = coord_input
    
    return coord_input


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
    location_mode = st.sidebar.radio("Specify location by:", ["🗺️ pH Map Click", "📮 Postcode Zone", "🔗 Google Maps Link"])
    
    # Initialize zone_info from session state if available
    if 'zone_info' not in st.session_state:
        st.session_state.zone_info = None
    if 'display_label' not in st.session_state:
        st.session_state.display_label = ""
    
    zone_info = None
    location_error = None
    display_label = ""

    if location_mode == "🗺️ pH Map Click":
        st.sidebar.markdown("---")
        st.sidebar.markdown("### 🗺️ Click on the map to select a location")
        st.sidebar.markdown("The map shows soil pH data - click anywhere on land to automatically select that location and load weather data.")
        
        # Display the map and get coordinates
        map_coords = ph_map_selector()
        
        # Auto-trigger location selection when coordinates are available
        if map_coords and map_coords.strip():
            try:
                # Parse the coordinates
                lat_str, lon_str = map_coords.split(',')
                lat = float(lat_str.strip())
                lon = float(lon_str.strip())
                
                # Get elevation bonus
                bonus, elevation = get_elevation_bonus(lat, lon)
                zone_info = {
                    "name": f"pH Map Location ({lat:.5f}, {lon:.5f})",
                    "lat": lat,
                    "lon": lon,
                    "upland_offset": bonus
                }
                display_label = f"📍 {lat:.5f}, {lon:.5f}"
                
                # Store in session state for persistence
                st.session_state.zone_info = zone_info
                st.session_state.display_label = display_label
                
                st.sidebar.success(f"✅ Location selected automatically!")
                st.sidebar.caption(f"📐 Elevation: ~{elevation}m (terrain bonus: +{bonus})")
                
            except Exception as e:
                st.sidebar.warning(f"⚠️ Could not parse coordinates: {e}")
                zone_info = None
        else:
            # Check if we have a previously stored location
            if st.session_state.zone_info is not None:
                zone_info = st.session_state.zone_info
                display_label = st.session_state.display_label
                st.sidebar.info(f"📍 Using previously selected location: {display_label}")
            else:
                st.sidebar.info("👆 Click on the map above to automatically select a location")
                zone_info = None

    elif location_mode == "📮 Postcode Zone":
        selected_outcode = st.sidebar.selectbox("Select Target Postcode:", list(IOM_POSTCODE_DB.keys()))
        zone_info = IOM_POSTCODE_DB[selected_outcode]
        display_label = selected_outcode
        # Store in session state
        st.session_state.zone_info = zone_info
        st.session_state.display_label = display_label

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
                # Store in session state
                st.session_state.zone_info = zone_info
                st.session_state.display_label = display_label
            except Exception as e:
                location_error = str(e)

    if location_error:
        st.sidebar.error(f"⚠️ {location_error}")

    # If no zone_info but we have it in session state, use it
    if zone_info is None and st.session_state.zone_info is not None:
        zone_info = st.session_state.zone_info
        display_label = st.session_state.display_label

    if zone_info is None:
        st.info("👈 Select a location using the map above or choose a postcode to see live data.")
        st.stop()

    st.subheader(f"🗺️ {display_label}")
    st.markdown(f"**Location:** {zone_info.get('name', display_label)}")
    st.markdown(f"**Coordinates:** {zone_info['lat']:.5f}, {zone_info['lon']:.5f}")
    st.markdown("---")

    st.subheader(f"📈 Historical & Forecast Trend: {display_label}")
    st.caption("Use this to line up your own field observations against what the model expected at the time.")

    history_days = st.slider("Days of history to include", 7, 30, 21)

    try:
        with st.spinner("Loading trend data..."):
            dates, day_max, night_min, trend_rain = fetch_historical_daily(zone_info["lat"], zone_info["lon"], days_back=history_days)

        # Build a rolling 48h rain figure per day for scoring
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
        mode = column.radio("Source:", ["📮 Postcode", "🗺️ pH Map", "🔗 Maps/Coords"], key=f"zone{zone_num}_mode", label_visibility="collapsed")
        
        if mode == "📮 Postcode":
            outcode = column.selectbox("Postcode:", list(IOM_POSTCODE_DB.keys()), index=default_postcode_index, key=f"zone{zone_num}_postcode", label_visibility="collapsed")
            return {"label": outcode, **IOM_POSTCODE_DB[outcode]}
        elif mode == "🗺️ pH Map":
            coords = column.text_input("Coordinates from pH map:", placeholder="54.150, -4.480", key=f"zone{zone_num}_phmap", label_visibility="collapsed")
            column.caption("Click the pH map in the main view to get coordinates, then paste here")
            if coords:
                try:
                    lat_str, lon_str = coords.split(',')
                    lat = float(lat_str.strip())
                    lon = float(lon_str.strip())
                    bonus, elevation = get_elevation_bonus(lat, lon)
                    column.caption(f"📐 ~{elevation}m (bonus +{bonus})")
                    return {"label": f"pH Map ({lat:.4f}, {lon:.4f})", "name": "pH Map location", "lat": lat, "lon": lon, "upland_offset": bonus}
                except:
                    column.error("⚠️ Invalid coordinates format. Use: latitude, longitude")
                    return None
            else:
                column.info("Enter coordinates from pH map")
                return None
        else:  # Maps/Coords
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
