from __future__ import annotations

from collections import Counter
import math
from typing import Any

import httpx


def _fallback_surface_label(surface: str | None, smoothness: str | None, highway: str | None) -> str | None:
    if surface:
        return surface
    sm = (smoothness or "").lower()
    hw = (highway or "").lower()
    if sm in {"excellent", "good", "intermediate"}:
        return "paved_estimated"
    if sm in {"bad", "very_bad", "horrible", "very_horrible", "impassable"}:
        return "rough_estimated"
    if hw in {"cycleway", "residential", "living_street", "service", "secondary", "primary", "tertiary"}:
        return "asphalt_estimated"
    if hw in {"track", "path", "bridleway"}:
        return "unpaved_estimated"
    if hw in {"footway", "pedestrian"}:
        return "urban_path_estimated"
    return None


def sample_points(points: list[dict[str, Any]], limit: int = 24) -> list[dict[str, Any]]:
    if len(points) <= limit:
        return points
    step = max(1, len(points) // limit)
    out = points[::step]
    if out[-1] != points[-1]:
        out.append(points[-1])
    return out[:limit]


def _haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    radius = 6371000.0
    lat1, lng1 = math.radians(a[0]), math.radians(a[1])
    lat2, lng2 = math.radians(b[0]), math.radians(b[1])
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(h))


def _segment_distance_m(point: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    lat_scale = 111320.0
    lng_scale = lat_scale * math.cos(math.radians(point[0]))
    px, py = point[1] * lng_scale, point[0] * lat_scale
    ax, ay = a[1] * lng_scale, a[0] * lat_scale
    bx, by = b[1] * lng_scale, b[0] * lat_scale
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    factor = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + factor * dx), py - (ay + factor * dy))


def _route_validation_samples(points: list[dict[str, Any]], limit: int = 24) -> list[dict[str, Any]]:
    base = sample_points(points, limit=max(2, limit - 6))
    long_segments: list[tuple[float, dict[str, Any]]] = []
    for index in range(1, len(points)):
        previous, current = points[index - 1], points[index]
        if previous.get("lat") is None or previous.get("lng") is None or current.get("lat") is None or current.get("lng") is None:
            continue
        distance_m = _haversine_m(
            (float(previous["lat"]), float(previous["lng"])),
            (float(current["lat"]), float(current["lng"])),
        )
        if distance_m >= 60:
            long_segments.append(
                (
                    distance_m,
                    {
                        "lat": (float(previous["lat"]) + float(current["lat"])) / 2,
                        "lng": (float(previous["lng"]) + float(current["lng"])) / 2,
                        "sample_kind": "segment_midpoint",
                    },
                )
            )
    midpoints = [point for _, point in sorted(long_segments, key=lambda item: item[0], reverse=True)[:6]]
    return (base + midpoints)[:limit]


def _is_usable_way(tags: dict[str, Any]) -> bool:
    highway = str(tags.get("highway") or "").lower()
    access = str(tags.get("access") or "").lower()
    if str(tags.get("leisure") or "").lower() == "park":
        return True
    if not highway or highway in {"construction", "proposed", "raceway", "motorway", "motorway_link"}:
        return False
    return access not in {"no", "private"}


def _point_in_polygon(point: tuple[float, float], polygon: list[tuple[float, float]]) -> bool:
    if len(polygon) < 4 or polygon[0] != polygon[-1]:
        return False
    lat, lng = point
    inside = False
    for a, b in zip(polygon, polygon[1:]):
        if (a[0] > lat) == (b[0] > lat):
            continue
        cross_lng = (b[1] - a[1]) * (lat - a[0]) / (b[0] - a[0]) + a[1]
        if lng < cross_lng:
            inside = not inside
    return inside


def _way_distance_m(point: dict[str, Any], way: dict[str, Any]) -> float | None:
    geometry = way.get("geometry") or []
    coords = [
        (float(item["lat"]), float(item["lon"]))
        for item in geometry
        if item.get("lat") is not None and item.get("lon") is not None
    ]
    point_coords = (float(point["lat"]), float(point["lng"]))
    if str(way.get("leisure") or "").lower() == "park" and _point_in_polygon(point_coords, coords):
        return 0.0
    if len(coords) >= 2:
        return min(_segment_distance_m(point_coords, a, b) for a, b in zip(coords, coords[1:]))
    center = way.get("center") or {}
    if center.get("lat") is not None and center.get("lon") is not None:
        return _haversine_m((float(point["lat"]), float(point["lng"])), (float(center["lat"]), float(center["lon"])))
    return None


async def fetch_surface_profile(
    overpass_url: str,
    points: list[dict[str, Any]],
    radius_m: int = 25,
    validation_radius_m: int = 35,
    validation_sample_limit: int = 24,
) -> dict[str, Any]:
    pts = sample_points(points)
    validation_pts = _route_validation_samples(points, validation_sample_limit)
    if not pts:
        return {"dominant_surface": None, "samples": []}
    body = ["[out:json][timeout:25];("]
    query_radius_m = max(radius_m, validation_radius_m)
    for p in validation_pts:
        body.append(f'way(around:{query_radius_m},{p["lat"]},{p["lng"]})["highway"];')
        body.append(f'way(around:{query_radius_m},{p["lat"]},{p["lng"]})["leisure"="park"];')
    body.append(");out tags geom qt;")
    query = "".join(body)
    urls = [u.strip() for u in overpass_url.split(",") if u.strip()]
    last_exc: Exception | None = None
    had_successful_response = False
    elements: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=40) as c:
        for url in urls:
            try:
                r = await c.post(url, data={"data": query})
                r.raise_for_status()
                had_successful_response = True
                elements = r.json().get("elements", [])
                if elements:
                    last_exc = None
                    break
            except Exception as exc:
                last_exc = exc
    if last_exc is not None and not had_successful_response:
        raise last_exc
    ways: list[dict[str, Any]] = []
    for el in elements:
        tags = el.get("tags") or {}
        if not tags:
            continue
        surface = tags.get("surface")
        highway = tags.get("highway")
        smoothness = tags.get("smoothness")
        ways.append(
            {
                "lat": (el.get("center") or {}).get("lat"),
                "lng": (el.get("center") or {}).get("lon"),
                "surface": surface,
                "highway": highway,
                "smoothness": smoothness,
                "surface_fallback": _fallback_surface_label(surface, smoothness, highway),
                "access": tags.get("access"),
                "leisure": tags.get("leisure"),
                "geometry": el.get("geometry") or [],
                "center": el.get("center") or {},
            }
        )
    surfaces = [w["surface"] for w in ways if w.get("surface")]
    smoothness_values = [w["smoothness"] for w in ways if w.get("smoothness")]
    dominant = Counter(surfaces).most_common(1)[0][0] if surfaces else None
    if not dominant:
        fallback_surfaces = [w["surface_fallback"] for w in ways if w.get("surface_fallback")]
        dominant = Counter(fallback_surfaces).most_common(1)[0][0] if fallback_surfaces else None
    dominant_smoothness = Counter(smoothness_values).most_common(1)[0][0] if smoothness_values else None
    has_cobblestone = any((w.get("surface") or "").lower() in {"cobblestone", "sett"} for w in ways)
    usable_ways = [way for way in ways if _is_usable_way(way)]
    off_network: list[dict[str, Any]] = []
    matched = 0
    for index, point in enumerate(validation_pts):
        distances = [distance for way in usable_ways if (distance := _way_distance_m(point, way)) is not None]
        nearest = min(distances) if distances else None
        if nearest is not None and nearest <= validation_radius_m:
            matched += 1
        elif len(off_network) < 12:
            off_network.append(
                {
                    "sample_index": index,
                    "lat": round(float(point["lat"]), 6),
                    "lng": round(float(point["lng"]), 6),
                    "sample_kind": point.get("sample_kind") or "track_point",
                    "nearest_usable_way_m": round(nearest, 1) if nearest is not None else None,
                }
            )
    sampled_count = len(validation_pts)
    off_network_count = max(0, sampled_count - matched)
    off_network_ratio = off_network_count / sampled_count if sampled_count else None
    if not sampled_count:
        alignment_status = "unavailable"
        alignment_summary = "Punti GPS insufficienti per il confronto OSM"
    elif off_network_ratio is not None and off_network_ratio > 0.2:
        alignment_status = "warning"
        alignment_summary = f"{off_network_count}/{sampled_count} campioni lontani dalla rete OSM"
    else:
        alignment_status = "ok"
        alignment_summary = "Traccia coerente con la rete OSM campionata"
    return {
        "dominant_surface": dominant,
        "dominant_smoothness": dominant_smoothness,
        "has_cobblestone": has_cobblestone,
        "samples": [
            {
                "lat": way["lat"],
                "lng": way["lng"],
                "surface": way["surface"],
                "surface_fallback": way["surface_fallback"],
                "highway": way["highway"],
                "smoothness": way["smoothness"],
            }
            for way in ways[:100]
        ],
        "track_alignment": {
            "status": alignment_status,
            "summary": alignment_summary,
            "sampled_point_count": sampled_count,
            "matched_point_count": matched,
            "off_network_count": off_network_count,
            "off_network_ratio": round(off_network_ratio, 3) if off_network_ratio is not None else None,
            "radius_m": validation_radius_m,
            "off_network_samples": off_network,
        },
    }
