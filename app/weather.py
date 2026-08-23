from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import httpx


_CACHE: dict[str, dict[str, Any]] = {}


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _midpoint(points: list[dict[str, Any]], bbox: dict[str, Any] | None) -> tuple[float, float] | None:
    if points:
        mid = points[len(points) // 2]
        if mid.get("lat") is not None and mid.get("lng") is not None:
            return float(mid["lat"]), float(mid["lng"])
    if not isinstance(bbox, dict):
        return None
    vals = [bbox.get("south"), bbox.get("west"), bbox.get("north"), bbox.get("east")]
    if any(v is None for v in vals):
        return None
    return (float(bbox["south"]) + float(bbox["north"])) / 2, (float(bbox["west"]) + float(bbox["east"])) / 2


def _nearest_idx(times: list[str], target: datetime) -> int | None:
    best_idx = None
    best_diff = None
    for idx, item in enumerate(times):
        ref = _parse_dt(item)
        if not ref:
            continue
        if ref.tzinfo is None and target.tzinfo is not None:
            ref = ref.replace(tzinfo=target.tzinfo)
        if ref.tzinfo is not None and target.tzinfo is None:
            ref = ref.replace(tzinfo=None)
        diff = abs((ref - target).total_seconds())
        if best_diff is None or diff < best_diff:
            best_diff = diff
            best_idx = idx
    return best_idx


async def fetch_weather_snapshot(
    archive_url: str,
    forecast_url: str,
    start_date_local: str | None,
    points: list[dict[str, Any]],
    bbox: dict[str, Any] | None,
) -> dict[str, Any] | None:
    when = _parse_dt(start_date_local)
    pos = _midpoint(points, bbox)
    if not when or not pos:
        return None
    lat, lng = pos
    has_tz = when.tzinfo is not None
    tz = "GMT" if has_tz else "auto"
    day = when.date().isoformat()
    if has_tz:
        api = archive_url if when.astimezone(UTC).date() <= datetime.now(UTC).date() else forecast_url
    else:
        api = archive_url if when.date() <= date.today() else forecast_url
    key = f"{api}|{round(lat, 3)}|{round(lng, 3)}|{day}|{when.hour}|{tz}"
    if key in _CACHE:
        return _CACHE[key]
    params = {
        "latitude": lat,
        "longitude": lng,
        "hourly": "temperature_2m,surface_pressure",
        "start_date": day,
        "end_date": day,
        "timezone": tz,
    }
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(api, params=params)
    r.raise_for_status()
    body = r.json()
    hourly = body.get("hourly") or {}
    times = hourly.get("time") or []
    idx = _nearest_idx(times, when.astimezone(UTC) if has_tz else when)
    if idx is None:
        return None
    out = {
        "temperature_c": (hourly.get("temperature_2m") or [None])[idx],
        "atmospheric_pressure_hpa": (hourly.get("surface_pressure") or [None])[idx],
        "weather_source": "open-meteo-archive" if api == archive_url else "open-meteo-forecast",
        "weather_observed_at": times[idx],
        "weather_lat": round(lat, 6),
        "weather_lng": round(lng, 6),
    }
    _CACHE[key] = out
    return out
