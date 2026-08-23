from __future__ import annotations

from typing import Any


def smooth_elevations(points: list[dict[str, Any]], window: int = 3) -> list[dict[str, Any]]:
    if window <= 1 or len(points) < 3:
        return [dict(p) for p in points]
    radius = max(1, window // 2)
    out: list[dict[str, Any]] = []
    for idx, point in enumerate(points):
        item = dict(point)
        if point.get("elevation") is None:
            out.append(item)
            continue
        vals: list[float] = []
        start = max(0, idx - radius)
        end = min(len(points), idx + radius + 1)
        for j in range(start, end):
            ele = points[j].get("elevation")
            if ele is not None:
                vals.append(float(ele))
        if vals:
            item["elevation"] = round(sum(vals) / len(vals), 2)
        out.append(item)
    return out


def classify_trend(slope_pct: float | None, flat_pct: float = 1.5) -> int:
    if slope_pct is None:
        return 0
    if slope_pct > flat_pct:
        return 1
    if slope_pct < -flat_pct:
        return -1
    return 0


def trend_metrics(
    steps: list[dict[str, Any]],
    flat_pct: float = 1.5,
    min_run_distance_m: float = 20.0,
    max_slope_cap_pct: float = 35.0,
) -> tuple[float, float, list[dict[str, Any]]]:
    runs: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    total_dist = 0.0
    weighted = 0.0
    max_abs = 0.0

    for step in steps:
        seg_m = float(step.get("segment_distance_m") or 0)
        slope_pct = step.get("slope_pct")
        if seg_m <= 0 or slope_pct is None:
            continue
        slope = float(slope_pct)
        elev = (slope / 100) * seg_m
        trend = classify_trend(slope, flat_pct)
        if current is None or current["trend"] != trend:
            if current is not None:
                runs.append(current)
            current = {"trend": trend, "distance_m": 0.0, "elevation_delta_m": 0.0}
        current["distance_m"] += seg_m
        current["elevation_delta_m"] += elev

    if current is not None:
        runs.append(current)

    for run in runs:
        dist = float(run["distance_m"] or 0)
        if dist <= 0 or dist < min_run_distance_m:
            continue
        slope = (float(run["elevation_delta_m"] or 0) / dist) * 100
        slope = max(-max_slope_cap_pct, min(max_slope_cap_pct, slope))
        run["slope_pct"] = round(slope, 2)
        total_dist += dist
        weighted += abs(slope) * dist
        if run["trend"] != 0:
            max_abs = max(max_abs, abs(slope))

    weighted_avg = weighted / total_dist if total_dist else 0.0
    return round(max_abs, 2), round(weighted_avg, 2), runs
