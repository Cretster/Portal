import streamlit as st
import requests
import re
import plotly.graph_objects as go
import pydeck as pdk
from datetime import datetime, timedelta
import streamlit.components.v1 as components
import json

# 1. Species Parameter Matrix (edible species only)
# 1. Species Parameter Matrix (Including restored wild and edible target variants)
SPECIES_MATRIX = {
     "🍄 Field Mushroom (Agaricus campestris)": {"day_min": 8, "day_max": 14, "night_min": 5, "night_max": 9, "rain_trigger": 12, "frost_kill": True}
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

def render_ph_map_with_picker():
    """
    Renders the pH map as an HTML component with click-to-select functionality.
    When a point is clicked, it sends coordinates back to Streamlit.
    """
    # Read the HTML file content
    with open('WorkingPHmap.html', 'r') as f:
        html_content = f.read()
    
    # Inject JavaScript to communicate with Streamlit
    # We'll wrap the existing map and add a click handler that sends coordinates to Streamlit
    map_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1.0" />
        <title>Isle of Man – Soil pH (embedded GeoTIFF)</title>
        <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
        <style>
            html, body {{ height: 100%; margin: 0; font-family: system-ui, sans-serif; }}
            #map {{ height: 100%; width: 100%; }}
            .info {{
                padding: 10px 12px;
                background: rgba(255,255,255,0.93);
                box-shadow: 0 0 15px rgba(0,0,0,0.2);
                border-radius: 6px;
                line-height: 1.45;
                max-width: 300px;
                font-size: 13px;
            }}
            .info h4 {{ margin: 0 0 6px; font-size: 14px; }}
            .legend i {{
                width: 18px; height: 14px; float: left; margin-right: 8px;
                opacity: 0.9; border: 1px solid #999;
            }}
            .ph-value {{ font-size: 18px; font-weight: 600; color: #1a5276; }}
            .note {{ font-size: 11px; color: #555; margin-top: 8px; }}
            .loading {{ color: #888; font-style: italic; }}
            .click-hint {{
                position: absolute;
                bottom: 20px;
                left: 50%;
                transform: translateX(-50%);
                background: rgba(0,0,0,0.7);
                color: white;
                padding: 8px 16px;
                border-radius: 20px;
                font-size: 14px;
                z-index: 1000;
                pointer-events: none;
                opacity: 0.8;
            }}
        </style>
    </head>
    <body>
        <div id="map"></div>
        <div class="click-hint">Click anywhere on land to select location</div>

        <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
        <script src="https://cdn.jsdelivr.net/npm/geotiff@2.1.3/dist-browser/geotiff.js"></script>

        <script>
            // ========== EMBEDDED GeoTIFF (base64) ==========
            // SoilGrids phh2o 0-5cm mean for Isle of Man (~250 m)
            // Values are pH × 10 (integer). Nodata = -32768
            const TIF_BASE64 = "SUkqAAgAAAAWAAABAwABAAAAugAAAAEBAwABAAAA3gAAAAIBAwABAAAAEAAAAAMBAwABAAAACAAAAAYBAwABAAAAAQAAABUBAwABAAAAAQAAABoBBQABAAAAFgEAABsBBQABAAAAHgEAABwBAwABAAAAAQAAACgBAwABAAAAAgAAAD0BAwABAAAAAgAAAEIBAwABAAAAAAEAAEMBAwABAAAAAAEAAEQBBAABAAAAzgEAAEUBBAABAAAAmB4AAFMBAwABAAAAAgAAAA6DDAADAAAALgEAAIKEDAAGAAAARgEAAK+HAwAgAAAAdgEAALCHDAACAAAAtgEAALGHAgAIAAAAxgEAAIGkAgAHAAAAJgEAAAAAAABIAAAAAQAAAEgAAAABAAAALTMyNzY4AABQ1uMvsqBsP/enKzFWc2I/AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAABohTnkmJkTwLdHfQkAQEtAAAAAAAAAAAABAAEAAAAHAAAEAAABAAIAAQQAAAEAAQAACAAAAQDmEAEIsYcHAAAABggAAAEAjiMJCLCHAQABAAsIsIcBAAAAiG10lh2kckAAAABAplRYQVdHUyA4NHwAeF7sW9Fy2zYQXIBOfsqW5H6Z8Wlt0vaf2pC8ztziCDbTPtiEkrXBzYxEUVRmvXu4OwAkCk78aChprsRlFChprsRlFChprsRlFChprsRlFChprsRlFChprsRlFChprsRlFChprsRlFChprsRlFChprsRlFChprsRlFChprsRlFChprsRlFChprsRlFChprsRlFChprsRlFChprsRlFChprsRlFChprsRlFChprsRlFChprsRlFChprsRlFChprsRlFChprsRlFChprsRlFChprsRlFChprsRlFChprsRlFChprsRlFChprsRlFChprsRlFChprsRlFChprsRlFChprsRlFChprsRlFChprsRlFChprsRlFChprsRlFChprsRFHLdilvDby3birVDSXImLJJ5LgjmADODX0/+hcHX/v7xcSkY6/R8Q3SNASXMlLqJ4LECGWYZZAvDl9P/D4laA5D6vBpgtNvl5swwgY+3RASpprsTlp+NW1WC3B8CjICN5TMAjADA7GAFKmitx+em4ldXotNnkWWAxYKreE+f4/7i4lHXL92YJCavnfYJn0tn/fQBca50n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";

            function base64ToArrayBuffer(base64) {
                const binary = atob(base64);
                const len = binary.length;
                const bytes = new Uint8Array(len);
                for (let i = 0; i < len; i++) bytes[i] = binary.charCodeAt(i);
                return bytes.buffer;
            }

            // ========== MAP ==========
            const iomBounds = L.latLngBounds([54.04, -4.85], [54.43, -4.30]);

            const map = L.map('map', {
                center: [54.23, -4.55],
                zoom: 10,
                maxBounds: iomBounds.pad(0.4),
                minZoom: 8
            });

            L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {
                attribution: '&copy; OpenStreetMap | Soil data: SoilGrids / ISRIC (CC-BY 4.0)',
                maxZoom: 19
            }).addTo(map);

            map.fitBounds(iomBounds);

            // ========== INFO PANEL ==========
            const info = L.control({ position: 'topright' });
            info.onAdd = function () {
                this._div = L.DomUtil.create('div', 'info');
                this._div.innerHTML = '<h4>Isle of Man Soil pH</h4><div class="loading">Loading embedded GeoTIFF…</div>';
                return this._div;
            };
            info.update = function (lat, lng, ph) {
                let html = '<h4>Isle of Man Soil pH (0–5 cm)</h4>';
                html += '<div class="legend">';
                html += '<i style="background:#d73027"></i> &lt; 5.0<br>';
                html += '<i style="background:#fc8d59"></i> 5.0 – 5.5 <b>(possible)</b><br>';
                html += '<i style="background:#fee08b"></i> 5.5 – 6.0 <b>(best!)</b><br>';
                html += '<i style="background:#d9ef8b"></i> 6.0 – 6.5 <b>(possible)</b><br>';
                html += '<i style="background:#91cf60"></i> 6.5 – 7.0<br>';
                html += '<i style="background:#1a9850"></i> &gt; 7.0<br>';
                html += '</div>';
                if (ph != null) {
                    html += `<div style="margin-top:10px">Clicked: ${lat.toFixed(5)}, ${lng.toFixed(5)}<br>
                             <span class="ph-value">pH ≈ ${ph.toFixed(1)}</span></div>`;
                } else {
                    html += '<div style="margin-top:8px;color:#666">Click anywhere on land to read pH</div>';
                }
                html += '<div class="note">Data: SoilGrids 250 m mean prediction (embedded local GeoTIFF). Values are model estimates. Most of the island falls in the 5–6.5 range.</div>';
                this._div.innerHTML = html;
            };
            info.addTo(map);

            // ========== LOAD & SAMPLE ==========
            let tiffImage = null;
            let rasterWidth, rasterHeight, bbox; // [west, south, east, north]

            async function loadTiff() {
                try {
                    const arrayBuffer = base64ToArrayBuffer(TIF_BASE64);
                    const tiff = await GeoTIFF.fromArrayBuffer(arrayBuffer);
                    tiffImage = await tiff.getImage();
                    rasterWidth = tiffImage.getWidth();
                    rasterHeight = tiffImage.getHeight();
                    bbox = tiffImage.getBoundingBox(); // [west, south, east, north]
                    console.log('GeoTIFF loaded', rasterWidth, '×', rasterHeight, 'bbox', bbox);
                    info.update(null, null, null);
                } catch (err) {
                    console.error(err);
                    info._div.innerHTML = '<h4>Error</h4><p>Could not decode the embedded GeoTIFF.<br>' + err.message + '</p>';
                }
            }

            async function samplePh(lat, lng) {
                if (!tiffImage) return null;
                const [west, south, east, north] = bbox;
                const x = Math.floor((lng - west) / (east - west) * rasterWidth);
                const y = Math.floor((north - lat) / (north - south) * rasterHeight);
                if (x < 0 || x >= rasterWidth || y < 0 || y >= rasterHeight) return null;

                const data = await tiffImage.readRasters({
                    window: [x, y, x + 1, y + 1],
                    width: 1,
                    height: 1
                });
                const raw = data[0][0];
                if (raw === -32768 || raw === undefined || isNaN(raw)) return null;
                return raw / 10.0;
            }

            // ========== CLICK HANDLER - SEND TO STREAMLIT ==========
            map.on('click', async function (e) {
                const { lat, lng } = e.latlng;
                const ph = await samplePh(lat, lng);
                info.update(lat, lng, ph);

                let content;
                if (ph != null) {
                    content = `<b>pH ≈ ${ph.toFixed(1)}</b><br>
                               ${lat.toFixed(5)}, ${lng.toFixed(5)}<br>
                               <small>SoilGrids 0–5 cm mean prediction</small>`;
                } else {
                    content = `No data (sea or outside raster)<br>${lat.toFixed(5)}, ${lng.toFixed(5)}`;
                }
                L.popup().setLatLng(e.latlng).setContent(content).openOn(map);

                // Send coordinates to Streamlit
                const coordStr = `${lat.toFixed(6)}, ${lng.toFixed(6)}`;
                window.parent.postMessage({
                    type: 'streamlit:setComponentValue',
                    value: coordStr
                }, '*');
                
                // Also send a custom event that we can catch
                window.parent.postMessage({
                    type: 'streamlit:locationSelected',
                    lat: lat,
                    lng: lng,
                    ph: ph
                }, '*');
            });

            async function addColourOverlay() {
                if (!tiffImage) return;
                const data = await tiffImage.readRasters();
                const values = data[0];
                const canvas = document.createElement('canvas');
                canvas.width = rasterWidth;
                canvas.height = rasterHeight;
                const ctx = canvas.getContext('2d');
                const imgData = ctx.createImageData(rasterWidth, rasterHeight);

                function colour(ph) {
                    if (ph < 5.0) return [215, 48, 39, 160];
                    if (ph < 5.5) return [252, 141, 89, 160];
                    if (ph < 6.0) return [254, 224, 139, 160];
                    if (ph < 6.5) return [217, 239, 139, 180];
                    if (ph < 7.0) return [145, 207, 96, 160];
                    return [26, 152, 80, 160];
                }

                for (let i = 0; i < values.length; i++) {
                    const raw = values[i];
                    const ph = (raw === -32768) ? NaN : raw / 10;
                    const c = isNaN(ph) ? [0,0,0,0] : colour(ph);
                    imgData.data[i*4]     = c[0];
                    imgData.data[i*4 + 1] = c[1];
                    imgData.data[i*4 + 2] = c[2];
                    imgData.data[i*4 + 3] = c[3];
                }
                ctx.putImageData(imgData, 0, 0);

                const [west, south, east, north] = bbox;
                const overlay = L.imageOverlay(canvas.toDataURL(), [[south, west], [north, east]], {
                    opacity: 0.65,
                    interactive: false
                }).addTo(map);

                const opacityCtrl = L.control({ position: 'bottomleft' });
                opacityCtrl.onAdd = function () {
                    const div = L.DomUtil.create('div', 'info');
                    div.innerHTML = `<label style="font-size:12px">Overlay opacity
                        <input type="range" min="0" max="1" step="0.05" value="0.65" id="op">
                    </label>`;
                    L.DomEvent.disableClickPropagation(div);
                    return div;
                };
                opacityCtrl.addTo(map);
                document.getElementById('op').addEventListener('input', function () {
                    overlay.setOpacity(parseFloat(this.value));
                });
            }

            loadTiff().then(addColourOverlay);
        </script>
    </body>
    </html>
    """
    
    # Render the HTML component with a specific height
    # Use components.html and capture any messages from the component
    components.html(map_html, height=600, scrolling=False)
    
    # The coordinates will be sent via the JavaScript postMessage
    # We'll need to handle this with a custom component that listens for messages
    # For now, we'll use a workaround with session state

# Add a custom component that listens for messages from the pH map
def ph_map_selector():
    """
    Custom component that renders the pH map and listens for click events.
    Returns the coordinates as a string when a location is clicked.
    """
    # This is a simplified approach - in practice, you'd want to use st.components.v1.html
    # with a custom message handler. For now, we'll use a text input approach.
    
    # Create a hidden text input that will be updated by JavaScript
    # We'll use a workaround with st.markdown and JavaScript injection
    st.markdown("""
    <style>
        .map-coord-input { display: none; }
    </style>
    """, unsafe_allow_html=True)
    
    # Use a session state variable to store the clicked coordinates
    if 'clicked_coords' not in st.session_state:
        st.session_state.clicked_coords = ""
    
    # Render the map
    with open('WorkingPHmap.html', 'r') as f:
        html_content = f.read()
    
    # We need to modify the HTML to store the clicked coordinates in a visible element
    # that Streamlit can read via a text input
    # For simplicity, we'll use a text input that is updated by JavaScript
    
    # Since we can't easily do bidirectional communication with components.html,
    # we'll use a different approach: use a text input with JavaScript injection
    
    # For now, let's use the simple approach: render the map and use a text input
    # that the user can manually enter coordinates into, or they can click the map
    # and we'll auto-fill it via a custom component
    
    # Actually, let me use the approach of loading the map and using a callback
    # with a custom component
    
    # I'll use a simpler approach: just render the HTML and have it update a text input
    map_html_modified = html_content.replace(
        'window.parent.postMessage({type: \'streamlit:locationSelected\', lat: lat, lng: lng, ph: ph}, \'*\');',
        '''
        window.parent.postMessage({type: 'streamlit:locationSelected', lat: lat, lng: lng, ph: ph}, '*');
        // Also update the hidden input
        const coordStr = `${lat.toFixed(6)}, ${lng.toFixed(6)}`;
        const input = document.getElementById('map_coord_input');
        if (input) {
            input.value = coordStr;
            input.dispatchEvent(new Event('input', { bubbles: true }));
        }
        '''
    )
    
    # Add a hidden input to the HTML
    map_html_with_input = map_html_modified.replace(
        '<div id="map"></div>',
        '''
        <div id="map"></div>
        <input type="text" id="map_coord_input" style="display:none;" />
        '''
    )
    
    # Render the map
    components.html(map_html_with_input, height=600, scrolling=False)
    
    # Use a text input that can be manually edited or auto-filled
    coord_input = st.text_input(
        "Selected coordinates (or paste lat, lon):",
        value=st.session_state.clicked_coords,
        placeholder="54.150, -4.480",
        key="map_coord_input"
    )
    
    # Update session state
    if coord_input != st.session_state.clicked_coords:
        st.session_state.clicked_coords = coord_input
    
    return coord_input

# --- FRONTEND ---
st.set_page_config(page_title="Dr Pablo's Mushroom Portal", page_icon="🍄", layout="wide")
st.title("🍄 Dr Pablo's My Celium Portal")

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
    location_mode = st.sidebar.radio("Specify location by:", ["📮 Postcode Zone", "🗺️ pH Map Click", "🔗 Google Maps Link"])

    zone_info = None
    location_error = None
    coords_from_map = ""

    if location_mode == "📮 Postcode Zone":
        selected_outcode = st.sidebar.selectbox("Select Target Postcode:", list(IOM_POSTCODE_DB.keys()))
        zone_info = IOM_POSTCODE_DB[selected_outcode]
        display_label = selected_outcode

    elif location_mode == "🗺️ pH Map Click":
        st.sidebar.markdown("---")
        st.sidebar.markdown("### Click on the map below to select a location")
        st.sidebar.markdown("The map shows soil pH data - click anywhere on land to get coordinates and pH value.")
        
        # Display the pH map with click-to-select functionality
        # We'll use a custom component approach
        col1, col2 = st.columns([3, 1])
        with col1:
            # Use our custom map selector
            map_coords = ph_map_selector()
            
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
                    st.sidebar.success(f"✅ Selected: {display_label}")
                    st.sidebar.caption(f"📐 Elevation: ~{elevation}m (terrain bonus: +{bonus})")
                except:
                    st.sidebar.warning("⚠️ Please click on the map to select a location")
            else:
                st.sidebar.info("👆 Click anywhere on the map to select a location")
        
        with col2:
            st.markdown("### pH Map Legend")
            st.markdown("""
            <div style="background: rgba(255,255,255,0.9); padding: 10px; border-radius: 8px;">
                <div><span style="display:inline-block; width:20px; height:20px; background:#d73027; border-radius:4px;"></span> &lt; 5.0</div>
                <div><span style="display:inline-block; width:20px; height:20px; background:#fc8d59; border-radius:4px;"></span> 5.0 – 5.5</div>
                <div><span style="display:inline-block; width:20px; height:20px; background:#fee08b; border-radius:4px;"></span> 5.5 – 6.0 <b>★ best!</b></div>
                <div><span style="display:inline-block; width:20px; height:20px; background:#d9ef8b; border-radius:4px;"></span> 6.0 – 6.5</div>
                <div><span style="display:inline-block; width:20px; height:20px; background:#91cf60; border-radius:4px;"></span> 6.5 – 7.0</div>
                <div><span style="display:inline-block; width:20px; height:20px; background:#1a9850; border-radius:4px;"></span> &gt; 7.0</div>
            </div>
            <div style="margin-top: 8px; font-size: 12px; color: #666;">
                Click map to select location &amp; see pH
            </div>
            """, unsafe_allow_html=True)

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
        mode = column.radio("Source:", ["📮 Postcode", "🗺️ pH Map", "🔗 Maps/Coords"], key=f"zone{zone_num}_mode", label_visibility="collapsed")
        if mode == "📮 Postcode":
            outcode = column.selectbox("Postcode:", list(IOM_POSTCODE_DB.keys()), index=default_postcode_index, key=f"zone{zone_num}_postcode", label_visibility="collapsed")
            return {"label": outcode, **IOM_POSTCODE_DB[outcode]}
        elif mode == "🗺️ pH Map":
            # For the comparison view, we'll use a simpler approach - just a text input
            # where the user can paste coordinates from the pH map
            coords = column.text_input("Coordinates from pH map:", placeholder="54.150, -4.480", key=f"zone{zone_num}_phmap", label_visibility="collapsed")
            if coords:
                try:
                    lat_str, lon_str = coords.split(',')
                    lat = float(lat_str.strip())
                    lon = float(lon_str.strip())
                    bonus, elevation = get_elevation_bonus(lat, lon)
                    column.caption(f"📐 ~{elevation}m (bonus +{bonus})")
                    return {"label": f"pH Map ({lat:.4f}, {lon:.4f})", "name": "pH Map location", "lat": lat, "lon": lon, "upland_offset": bonus}
                except:
                    column.error("⚠️ Invalid coordinates")
                    return None
            else:
                column.info("Enter coordinates")
                return None
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
