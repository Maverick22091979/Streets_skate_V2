from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from xml.etree import ElementTree as ET
import os

from app.slope import smooth_elevations, trend_metrics


GPX_NS = {"gpx": "http://www.topografix.com/GPX/1/1"}
SLOPE_SMOOTHING_WINDOW = int(os.getenv("SLOPE_SMOOTHING_WINDOW", "5"))
SLOPE_MIN_RUN_DISTANCE_M = float(os.getenv("SLOPE_MIN_RUN_DISTANCE_M", "20"))
SLOPE_MAX_CAP_PCT = float(os.getenv("SLOPE_MAX_CAP_PCT", "35"))


@dataclass(frozen=True)
class DifficultyLabel:
    level: str
    color: str
    score_min: int
    score_max: int
    source: str


def _extract_date_from_filename(filename: str) -> datetime | None:
    patterns = [
        (r"(?<!\d)(\d{4})[-_](\d{2})[-_](\d{2})(?!\d)", "%Y-%m-%d"),
        (r"(?<!\d)(\d{2})[-_](\d{2})[-_](\d{4})(?!\d)", "%d-%m-%Y"),
        (r"(?<!\d)(\d{8})(?!\d)", None),
    ]
    for pattern, date_format in patterns:
        match = re.search(pattern, filename)
        if not match:
            continue
        if date_format:
            try:
                normalized = "-".join(match.groups())
                return datetime.strptime(normalized, date_format)
            except ValueError:
                continue
        compact_date = match.group(1)
        for compact_format in ("%Y%m%d", "%d%m%Y"):
            try:
                return datetime.strptime(compact_date, compact_format)
            except ValueError:
                continue
    return None


def _declared_difficulty_from_date(route_date: datetime) -> DifficultyLabel:
    weekday = route_date.weekday()
    if weekday == 0:
        occurrence = ((route_date.day - 1) // 7) + 1
        if occurrence in {1, 3}:
            return DifficultyLabel("Easy", "green", 0, 15, f"{occurrence}° lunedì del mese")
        if occurrence in {2, 4}:
            return DifficultyLabel("EasyLong", "yellow", 16, 40, f"{occurrence}° lunedì del mese")
        return DifficultyLabel("Unknown", "gray", 0, 100, "5° lunedì non classificato")
    if weekday == 2:
        return DifficultyLabel("Advanced", "red", 41, 70, "mercoledì")
    if weekday == 4:
        return DifficultyLabel("Pro", "black", 71, 100, "venerdì")
    if weekday == 6:
        return DifficultyLabel("EasyLong", "yellow", 16, 40, "domenica")
    return DifficultyLabel("Unknown", "gray", 0, 100, "giorno non classificato")


def _declared_difficulty_from_filename(filename: str) -> dict[str, Any] | None:
    route_date = _extract_date_from_filename(filename)
    if route_date is None:
        return None
    label = _declared_difficulty_from_date(route_date)
    return {
        "date": route_date.date().isoformat(),
        "weekday": route_date.strftime("%A"),
        "level": label.level,
        "color": label.color,
        "score_min": label.score_min,
        "score_max": label.score_max,
        "source": label.source,
    }


def parse_iso_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    r = 6371000
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _find_first_text(el: ET.Element, names: list[str]) -> str | None:
    for child in el.iter():
        tag = child.tag.split("}")[-1].lower()
        if tag in names and child.text:
            return child.text.strip()
    return None


def _find_first_float(el: ET.Element, names: list[str]) -> float | None:
    text = _find_first_text(el, names)
    if text is None:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _sport_type_from_gpx(root: ET.Element) -> str | None:
    for path in (".//gpx:trk/gpx:type", ".//gpx:metadata/gpx:type", ".//gpx:rte/gpx:type"):
        value = root.findtext(path, default="", namespaces=GPX_NS)
        if value and value.strip():
            return value.strip()
    for el in root.iter():
        tag = el.tag.split("}")[-1].lower()
        if tag in {"type", "sport", "activitytype", "activity"} and el.text and el.text.strip():
            return el.text.strip()
    return None


def _point_profile(points: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str], float, float]:
    if len(points) < 2:
        return points, [], 0.0, 0.0
    points = smooth_elevations(points, SLOPE_SMOOTHING_WINDOW)
    out: list[dict[str, Any]] = [dict(points[0], segment_distance_m=0.0, cumulative_distance_m=0.0, slope_pct=None, speed_kmh=None)]
    colors: list[str] = []
    cumulative = 0.0
    for i in range(1, len(points)):
        prev = points[i - 1]
        cur = points[i]
        seg_m = haversine_m((prev["lat"], prev["lng"]), (cur["lat"], cur["lng"]))
        cumulative += seg_m
        slope = None
        if prev.get("elevation") is not None and cur.get("elevation") is not None and seg_m > 0:
            slope = ((float(cur["elevation"]) - float(prev["elevation"])) / seg_m) * 100
        speed = None
        if prev.get("time") and cur.get("time"):
            dt = (cur["time"] - prev["time"]).total_seconds()
            if dt > 0 and seg_m > 0:
                speed = (seg_m / dt) * 3.6
        if speed is None and cur.get("speed_kmh_sidecar") is not None:
            speed = float(cur["speed_kmh_sidecar"])
        item = dict(cur)
        item["segment_distance_m"] = round(seg_m, 2)
        item["cumulative_distance_m"] = round(float(cur.get("cumulative_distance_m_sidecar", cumulative)), 2)
        item["slope_pct"] = round(slope, 2) if slope is not None else None
        item["speed_kmh"] = round(speed, 2) if speed is not None else None
        out.append(item)
    max_slope, weighted_avg, _ = trend_metrics(
        out[1:],
        min_run_distance_m=SLOPE_MIN_RUN_DISTANCE_M,
        max_slope_cap_pct=SLOPE_MAX_CAP_PCT,
    )
    return out, colors, max_slope, weighted_avg


def _parse_sidecar_json_bytes(raw: bytes) -> list[dict[str, Any]]:
    body = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(body, list):
        return []
    out: list[dict[str, Any]] = []
    for item in body:
        if not isinstance(item, dict):
            continue
        lat = item.get("latitude")
        lng = item.get("longitude")
        if lat is None or lng is None:
            continue
        ts = item.get("timestamp")
        dt = datetime.fromtimestamp(ts / 1000, tz=None).astimezone() if isinstance(ts, (int, float)) else None
        out.append(
            {
                "lat": float(lat),
                "lng": float(lng),
                "elevation": float(item["altitude"]) if item.get("altitude") is not None else None,
                "time": dt,
                "speed_kmh_sidecar": float(item["speed"]) if item.get("speed") is not None else None,
                "cumulative_distance_m_sidecar": float(item["distance"]) if item.get("distance") is not None else None,
                "elevation_gain_total_m_sidecar": float(item["elevation_gain"]) if item.get("elevation_gain") is not None else None,
                "elevation_loss_total_m_sidecar": float(item["elevation_loss"]) if item.get("elevation_loss") is not None else None,
            }
        )
    return out


def _merge_sidecar_points(
    gpx_points: list[dict[str, Any]],
    sidecar_points: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not gpx_points or not sidecar_points:
        return gpx_points
    last_gpx = max(len(gpx_points) - 1, 1)
    last_side = max(len(sidecar_points) - 1, 1)
    merged: list[dict[str, Any]] = []
    for i, point in enumerate(gpx_points):
        idx = min(last_side, round(i * last_side / last_gpx))
        side = sidecar_points[idx]
        item = dict(point)
        if item.get("elevation") is None and side.get("elevation") is not None:
            item["elevation"] = side["elevation"]
        if item.get("time") is None and side.get("time") is not None:
            item["time"] = side["time"]
        if side.get("speed_kmh_sidecar") is not None:
            item["speed_kmh_sidecar"] = side["speed_kmh_sidecar"]
        if side.get("cumulative_distance_m_sidecar") is not None:
            item["cumulative_distance_m_sidecar"] = side["cumulative_distance_m_sidecar"]
        if side.get("elevation_gain_total_m_sidecar") is not None:
            item["elevation_gain_total_m_sidecar"] = side["elevation_gain_total_m_sidecar"]
        if side.get("elevation_loss_total_m_sidecar") is not None:
            item["elevation_loss_total_m_sidecar"] = side["elevation_loss_total_m_sidecar"]
        merged.append(item)
    return merged


def parse_gpx_bytes(raw: bytes, filename: str, sidecar_raw: bytes | None = None) -> dict[str, Any]:
    root = ET.fromstring(raw)
    name = root.findtext(".//gpx:trk/gpx:name", default="", namespaces=GPX_NS) or filename.rsplit(".", 1)[0]
    sport_type = _sport_type_from_gpx(root) or "unknown"
    pts: list[dict[str, Any]] = []
    for trkpt in root.findall(".//gpx:trkpt", GPX_NS):
        lat = trkpt.attrib.get("lat")
        lon = trkpt.attrib.get("lon")
        if lat is None or lon is None:
            continue
        point = {
            "lat": float(lat),
            "lng": float(lon),
            "elevation": float(trkpt.findtext("gpx:ele", default="", namespaces=GPX_NS)) if trkpt.findtext("gpx:ele", default="", namespaces=GPX_NS) else None,
            "time": parse_iso_dt(trkpt.findtext("gpx:time", default=None, namespaces=GPX_NS)),
        }
        ext = trkpt.find("gpx:extensions", GPX_NS)
        if ext is not None:
            point["temperature_c"] = _find_first_float(ext, ["atemp", "temperature", "temp", "airtemp"])
            point["pressure_hpa"] = _find_first_float(ext, ["pressure", "atmosphericpressure", "barometer"])
            point["surface"] = _find_first_text(ext, ["surface"])
        pts.append(point)
    sidecar_points = _parse_sidecar_json_bytes(sidecar_raw) if sidecar_raw else []
    pts = _merge_sidecar_points(pts, sidecar_points)

    profile, _, max_slope, weighted_slope = _point_profile(pts)
    total_dist = 0.0
    max_speed = 0.0
    elev_gain = 0.0
    for i in range(1, len(profile)):
        total_dist += float(profile[i]["segment_distance_m"] or 0)
        if profile[i].get("speed_kmh") is not None:
            max_speed = max(max_speed, float(profile[i]["speed_kmh"]))
        prev_ele = profile[i - 1].get("elevation")
        cur_ele = profile[i].get("elevation")
        if prev_ele is not None and cur_ele is not None and cur_ele > prev_ele:
            elev_gain += float(cur_ele) - float(prev_ele)
    if profile and profile[-1].get("cumulative_distance_m_sidecar") is not None:
        total_dist = float(profile[-1]["cumulative_distance_m_sidecar"])
    if profile and profile[-1].get("elevation_gain_total_m_sidecar") is not None:
        elev_gain = float(profile[-1]["elevation_gain_total_m_sidecar"])

    times = [p["time"] for p in profile if p.get("time")]
    moving_time_sec = int((times[-1] - times[0]).total_seconds()) if len(times) >= 2 else None
    temperature_vals = [float(p["temperature_c"]) for p in profile if p.get("temperature_c") is not None]
    pressure_vals = [float(p["pressure_hpa"]) for p in profile if p.get("pressure_hpa") is not None]

    return {
        "external_id": f"gpx:{filename}:{len(profile)}",
        "name": name,
        "sport_type": sport_type,
        "start_date_local": times[0].isoformat() if times else None,
        "distance_m": round(total_dist, 1),
        "elevation_gain_m": round(elev_gain, 1),
        "average_speed_ms": (total_dist / moving_time_sec) if moving_time_sec and total_dist else 0.0,
        "moving_time_sec": moving_time_sec,
        "summary_polyline": "",
        "points": [
            {
                "lat": p["lat"],
                "lng": p["lng"],
                "elevation": p.get("elevation"),
                "time": p["time"].isoformat() if p.get("time") else None,
                "segment_distance_m": p.get("segment_distance_m"),
                "cumulative_distance_m": p.get("cumulative_distance_m"),
                "slope_pct": p.get("slope_pct"),
                "speed_kmh": p.get("speed_kmh"),
            }
            for p in profile
        ],
        "metrics": {
            "max_slope_pct": max_slope,
            "weighted_avg_slope_pct": weighted_slope,
            "temperature_c": round(sum(temperature_vals) / len(temperature_vals), 2) if temperature_vals else None,
            "atmospheric_pressure_hpa": round(sum(pressure_vals) / len(pressure_vals), 2) if pressure_vals else None,
            "segment_max_speed_kmh": round(max_speed, 2) if max_speed else None,
        },
        "raw_payload": {
            "filename": filename,
            "sidecar_json": bool(sidecar_raw),
            "sport_type": sport_type,
            "declared_difficulty": _declared_difficulty_from_filename(filename),
        },
    }
