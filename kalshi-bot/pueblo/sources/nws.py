"""Servicio Meteorológico de EE. UU. (api.weather.gov): previsiones y lo observado hoy.

Es la misma fuente con la que Kalshi decide los mercados de temperatura máxima (el
informe climático diario del NWS de cada estación). La API es gratuita y pide un
User-Agent que identifique la aplicación.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import requests

API = "https://api.weather.gov"


@dataclass(frozen=True)
class City:
    key: str  # como en la serie de Kalshi: KXHIGH + key
    name: str
    station: str  # estación con la que se decide el mercado
    lat: float
    lon: float
    tz: str


CITIES = {
    c.key: c
    for c in (
        City("NY", "Nueva York", "KNYC", 40.7789, -73.9692, "America/New_York"),
        City("CHI", "Chicago", "KMDW", 41.7861, -87.7522, "America/Chicago"),
        City("LAX", "Los Ángeles", "KLAX", 33.9425, -118.4081, "America/Los_Angeles"),
        City("MIA", "Miami", "KMIA", 25.7933, -80.2906, "America/New_York"),
        City("AUS", "Austin", "KAUS", 30.1945, -97.6699, "America/Chicago"),
        City("DEN", "Denver", "KDEN", 39.8466, -104.6562, "America/Denver"),
        City("PHIL", "Filadelfia", "KPHL", 39.8683, -75.2311, "America/New_York"),
    )
}


def c_to_f(celsius: float) -> float:
    return celsius * 9 / 5 + 32


def climate_day(city: City, now: datetime) -> tuple:
    """(día, inicio) del día climático: el NWS lo cuenta en hora estándar, también en verano."""
    local = now.astimezone(ZoneInfo(city.tz))
    standard = timezone(local.utcoffset() - (local.dst() or timedelta(0)))
    std_now = now.astimezone(standard)
    start = datetime(std_now.year, std_now.month, std_now.day, tzinfo=standard)
    return std_now.date(), start


class NWSClient:
    def __init__(self, contact: str = "pueblo-de-bots", session=None, timeout: float = 20.0):
        self.session = session or requests.Session()
        self.headers = {"User-Agent": f"(pueblo-de-bots, {contact})", "Accept": "application/geo+json"}
        self.timeout = timeout
        self._forecast_urls: dict = {}

    def _get(self, url: str, **params) -> dict:
        resp = self.session.get(url, params=params or None, headers=self.headers, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def forecast_highs(self, city: City) -> dict:
        """Máxima prevista por día (fecha local → °F), de los periodos de día de la previsión."""
        url = self._forecast_urls.get(city.key)
        if url is None:
            point = self._get(f"{API}/points/{city.lat:.4f},{city.lon:.4f}")
            url = point["properties"]["forecast"]
            self._forecast_urls[city.key] = url
        periods = self._get(url)["properties"]["periods"]
        highs: dict = {}
        for period in periods:
            if not period.get("isDaytime"):
                continue
            temp = period.get("temperature")
            if temp is None:
                continue
            if period.get("temperatureUnit") == "C":
                temp = round(c_to_f(float(temp)))
            day = datetime.fromisoformat(period["startTime"]).date()
            highs.setdefault(day.isoformat(), int(temp))
        return highs

    def observed_max(self, city: City, now: Optional[datetime] = None) -> Optional[dict]:
        """La temperatura más alta medida hoy en la estación (°F, aproximada) y la última lectura."""
        now = now or datetime.now(timezone.utc)
        day, start = climate_day(city, now)
        data = self._get(f"{API}/stations/{city.station}/observations", start=start.isoformat(timespec="seconds"))
        readings = []
        for feature in data.get("features", []):
            props = feature.get("properties") or {}
            value = (props.get("temperature") or {}).get("value")
            stamp = props.get("timestamp")
            if value is None or not stamp:
                continue
            readings.append((datetime.fromisoformat(stamp), c_to_f(float(value))))
        if not readings:
            return None
        readings.sort()
        return {
            "day": day.isoformat(),
            "max_f": round(max(t for _, t in readings)),
            "last_f": round(readings[-1][1]),
            "last_at": readings[-1][0].isoformat(),
        }


def today_in(city: City, now: datetime) -> date:
    return now.astimezone(ZoneInfo(city.tz)).date()
