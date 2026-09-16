from __future__ import annotations

import math
from datetime import datetime
from typing import Any


def _haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    radius = 6371000.0
    lat1, lng1 = math.radians(a[0]), math.radians(a[1])
    lat2, lng2 = math.radians(b[0]), math.radians(b[1])
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(h))


def _timestamp(value: Any) -> float | None:
    if isinstance(value, datetime):
        return value.timestamp()
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(raw).timestamp()
    except ValueError:
        return None


def analyze_track_quality(
    points: list[dict[str, Any]],
    *,
    long_segment_m: float,
    time_collision_distance_m: float,
    implausible_speed_kmh: float,
    elapsed_gap_sec: float,
    max_samples: int = 12,
) -> dict[str, Any]:
    valid = [p for p in points if p.get("lat") is not None and p.get("lng") is not None]
    warnings: list[dict[str, Any]] = []
    long_segment_count = 0
    time_collision_count = 0
    implausible_speed_count = 0
    elapsed_gap_count = 0
    max_distance_m = 0.0
    max_speed_kmh = 0.0
    timed_segments = 0

    for index in range(1, len(valid)):
        prev, current = valid[index - 1], valid[index]
        distance_m = _haversine_m(
            (float(prev["lat"]), float(prev["lng"])),
            (float(current["lat"]), float(current["lng"])),
        )
        max_distance_m = max(max_distance_m, distance_m)
        previous_ts = _timestamp(prev.get("time"))
        current_ts = _timestamp(current.get("time"))
        elapsed_sec = current_ts - previous_ts if previous_ts is not None and current_ts is not None else None
        speed_kmh = None
        if elapsed_sec is not None and elapsed_sec > 0:
            timed_segments += 1
            speed_kmh = distance_m / elapsed_sec * 3.6
            max_speed_kmh = max(max_speed_kmh, speed_kmh)

        issue_types: list[str] = []
        if distance_m >= long_segment_m:
            long_segment_count += 1
            issue_types.append("long_segment")
        if elapsed_sec is not None and elapsed_sec <= 1 and distance_m >= time_collision_distance_m:
            time_collision_count += 1
            issue_types.append("time_collision")
        if speed_kmh is not None and speed_kmh >= implausible_speed_kmh:
            implausible_speed_count += 1
            issue_types.append("implausible_speed")
        if distance_m >= long_segment_m and elapsed_sec is not None and elapsed_sec >= elapsed_gap_sec:
            elapsed_gap_count += 1
            issue_types.append("possible_gps_gap")
        if issue_types and len(warnings) < max_samples:
            warnings.append(
                {
                    "from_index": index - 1,
                    "to_index": index,
                    "types": issue_types,
                    "distance_m": round(distance_m, 1),
                    "elapsed_sec": round(elapsed_sec, 1) if elapsed_sec is not None else None,
                    "speed_kmh": round(speed_kmh, 1) if speed_kmh is not None else None,
                    "from": {"lat": float(prev["lat"]), "lng": float(prev["lng"])},
                    "to": {"lat": float(current["lat"]), "lng": float(current["lng"])},
                }
            )

    warning_count = long_segment_count + time_collision_count + implausible_speed_count
    if len(valid) < 2:
        status = "unavailable"
        summary = "Punti GPS insufficienti per il controllo"
    elif warning_count:
        status = "warning"
        summary = f"{warning_count} anomalia/e GPS rilevata/e"
    else:
        status = "ok"
        summary = "Nessun salto GPS rilevato"
    return {
        "status": status,
        "summary": summary,
        "point_count": len(valid),
        "timed_segment_count": timed_segments,
        "long_segment_count": long_segment_count,
        "time_collision_count": time_collision_count,
        "implausible_speed_count": implausible_speed_count,
        "possible_gps_gap_count": elapsed_gap_count,
        "max_segment_distance_m": round(max_distance_m, 1),
        "max_speed_kmh": round(max_speed_kmh, 1) if max_speed_kmh else None,
        "warnings": warnings,
        "thresholds": {
            "long_segment_m": long_segment_m,
            "time_collision_distance_m": time_collision_distance_m,
            "implausible_speed_kmh": implausible_speed_kmh,
            "elapsed_gap_sec": elapsed_gap_sec,
        },
    }
