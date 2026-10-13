"""Clima actual por ciudad (Open-Meteo, sin clave). Si algo falla devuelve None y el DJ solo dice la hora."""
import json
import time
import urllib.parse
import urllib.request

_WMO = {
    0: "cielo despejado", 1: "mayormente despejado", 2: "parcialmente nublado", 3: "nublado",
    45: "con neblina", 48: "con neblina", 51: "con llovizna ligera", 53: "con llovizna", 55: "con llovizna intensa",
    61: "con lluvia ligera", 63: "con lluvia", 65: "con lluvia fuerte", 80: "con chubascos ligeros",
    81: "con chubascos", 82: "con chubascos fuertes", 95: "con tormenta eléctrica", 96: "con tormenta eléctrica",
    99: "con tormenta eléctrica",
}
_CACHE: dict[str, tuple[float, str | None]] = {}
_VIGENCIA = 1800


def _get(url: str, params: dict) -> dict:
    with urllib.request.urlopen(url + "?" + urllib.parse.urlencode(params), timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def clima_actual(ciudad: str) -> str | None:
    if not ciudad:
        return None
    ahora = time.time()
    if ciudad in _CACHE and ahora - _CACHE[ciudad][0] < _VIGENCIA:
        return _CACHE[ciudad][1]
    texto = None
    try:
        geo = _get("https://geocoding-api.open-meteo.com/v1/search",
                   {"name": ciudad, "count": 1, "language": "es"})
        lugar = (geo.get("results") or [None])[0]
        # solo poblaciones: "Venezuela" (un país) no tiene un clima que decir al aire
        if lugar and str(lugar.get("feature_code", "")).startswith("PPL"):
            actual = _get("https://api.open-meteo.com/v1/forecast",
                          {"latitude": lugar["latitude"], "longitude": lugar["longitude"],
                           "current": "temperature_2m,weather_code"})["current"]
            cielo = _WMO.get(actual["weather_code"])
            texto = f"{round(actual['temperature_2m'])} grados" + (f", {cielo}" if cielo else "")
    except Exception:
        texto = None
    _CACHE[ciudad] = (ahora, texto)
    return texto
