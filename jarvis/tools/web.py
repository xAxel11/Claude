import webbrowser
from urllib.parse import quote_plus

import requests

from .. import ai
from ..bridge import bridge
from ._base import tool

UA = {"User-Agent": "jarvis-desktop-assistant"}


@tool
def web_search(query: str) -> str:
    """Search the internet for current information: news, facts, prices, sports scores, etc."""
    return ai.web_search(query)


@tool
def get_weather(location: str) -> str:
    """Get the current weather and today's forecast for a city or place."""
    data = requests.get(f"https://wttr.in/{quote_plus(location)}?format=j1", timeout=10).json()
    now = data["current_condition"][0]
    today = data["weather"][0]
    return (
        f"{location}: {now['weatherDesc'][0]['value']}, {now['temp_C']}°C "
        f"(feels like {now['FeelsLikeC']}°C), humidity {now['humidity']}%, "
        f"wind {now['windspeedKmph']} km/h. Today: low {today['mintempC']}°C, high {today['maxtempC']}°C."
    )


def _geocode(place):
    r = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": place, "format": "json", "limit": 1},
        headers=UA,
        timeout=10,
    )
    results = r.json()
    if not results:
        return None
    top = results[0]
    return float(top["lat"]), float(top["lon"]), top.get("display_name", place)


@tool
def show_map(location: str, zoom: int = 12, map_type: str = "") -> str:
    """Show a place on the HUD map. zoom: 3 (continent) to 18 (street).
    map_type (optional): 'hud' (dark holographic), 'road', 'satellite', 'hybrid' or 'terrain'."""
    found = _geocode(location)
    if not found:
        return f"Couldn't find {location} on the map."
    lat, lon, name = found
    bridge.show_map(lat, lon, location, zoom, map_type)
    return f"Showing {name} ({lat:.4f}, {lon:.4f}) on the map."


@tool
def add_map_marker(location: str, label: str = "") -> str:
    """Pin an extra place on the map without clearing existing pins."""
    found = _geocode(location)
    if not found:
        return f"Couldn't find {location}."
    bridge.add_marker(found[0], found[1], label or location)
    return f"Pinned {found[2]}."


@tool
def clear_map() -> str:
    """Remove all pins and routes from the map."""
    bridge.clear_map()
    return "Map cleared."


@tool
def expand_map(on: bool = True) -> str:
    """Make the map take over the big centre area (on=True) or shrink it back (on=False)."""
    bridge.expand_map(on)
    return "Map expanded." if on else "Map restored."


def _ip_location():
    data = requests.get("https://ipinfo.io/json", headers=UA, timeout=10).json()
    lat, lon = (float(v) for v in data["loc"].split(","))
    return lat, lon, f"{data.get('city', '')}, {data.get('country', '')}".strip(", ")


@tool
def where_am_i() -> str:
    """Find the user's approximate location (from their internet connection) and show it on the map."""
    lat, lon, name = _ip_location()
    bridge.show_map(lat, lon, "YOU ARE HERE", 12, "")
    return f"You appear to be near {name} ({lat:.3f}, {lon:.3f})."


@tool
def get_directions(destination: str, origin: str = "here", mode: str = "driving",
                   open_google_maps: bool = False) -> str:
    """Plot a route on the HUD map and report distance and travel time.
    origin 'here' = the user's current location. mode: driving, walking or cycling.
    Set open_google_maps=True to also open turn-by-turn directions in the browser."""
    if origin.lower() in ("here", "my location", "current location", ""):
        o_lat, o_lon, o_name = _ip_location()
    else:
        found = _geocode(origin)
        if not found:
            return f"Couldn't find {origin}."
        o_lat, o_lon, o_name = found
    found = _geocode(destination)
    if not found:
        return f"Couldn't find {destination}."
    d_lat, d_lon, _ = found
    profile = {"walking": "foot", "cycling": "bike", "bicycling": "bike"}.get(mode, "driving")
    summary = ""
    try:
        r = requests.get(
            f"https://router.project-osrm.org/route/v1/{profile}/{o_lon},{o_lat};{d_lon},{d_lat}",
            params={"overview": "full", "geometries": "geojson"},
            headers=UA,
            timeout=15,
        ).json()
        route = r["routes"][0]
        points = [(lat, lon) for lon, lat in route["geometry"]["coordinates"]]
        bridge.draw_route(points, destination)
        km = route["distance"] / 1000
        minutes = route["duration"] / 60
        summary = f"Route plotted: {km:.1f} km, about {minutes:.0f} minutes {mode}."
    except Exception:  # noqa: BLE001  (routing server down or no road route)
        bridge.draw_route([(o_lat, o_lon), (d_lat, d_lon)], destination)
        summary = "Couldn't get a road route, so I've drawn a straight line."
    if open_google_maps:
        mode_g = {"cycling": "bicycling"}.get(mode, mode)
        webbrowser.open(
            "https://www.google.com/maps/dir/?api=1"
            f"&origin={o_lat},{o_lon}&destination={quote_plus(destination)}&travelmode={mode_g}"
        )
    return f"From {o_name} to {destination}. {summary}"


TOOLS = [web_search, get_weather, show_map, add_map_marker, clear_map, expand_map, where_am_i, get_directions]
