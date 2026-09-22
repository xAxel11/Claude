import webbrowser
from urllib.parse import quote_plus

import requests

from .. import ai
from ..bridge import bridge
from ._base import tool


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
        headers={"User-Agent": "jarvis-desktop-assistant"},
        timeout=10,
    )
    results = r.json()
    if not results:
        return None
    top = results[0]
    return float(top["lat"]), float(top["lon"]), top.get("display_name", place)


@tool
def show_map(location: str, zoom: int = 12, map_type: str = "road") -> str:
    """Show a place on the HUD's map panel. zoom: 3 (continent) to 18 (street).
    map_type: 'road' or 'satellite'."""
    found = _geocode(location)
    if not found:
        return f"Couldn't find {location} on the map."
    lat, lon, name = found
    bridge.show_map(lat, lon, location, zoom, map_type)
    return f"Showing {name} ({lat:.4f}, {lon:.4f}) on the map."


@tool
def get_directions(origin: str, destination: str, mode: str = "driving") -> str:
    """Open turn-by-turn directions in Google Maps. mode: driving, walking, transit or bicycling."""
    url = (
        "https://www.google.com/maps/dir/?api=1"
        f"&origin={quote_plus(origin)}&destination={quote_plus(destination)}&travelmode={mode}"
    )
    webbrowser.open(url)
    found = _geocode(destination)
    if found:
        bridge.show_map(found[0], found[1], destination, 12, "road")
    return f"Opened {mode} directions from {origin} to {destination} in the browser."


TOOLS = [web_search, get_weather, show_map, get_directions]
