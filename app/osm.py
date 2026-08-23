from __future__ import annotations

from collections import Counter
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


async def fetch_surface_profile(overpass_url: str, points: list[dict[str, Any]], radius_m: int = 25) -> dict[str, Any]:
    pts = sample_points(points)
    if not pts:
        return {"dominant_surface": None, "samples": []}
    body = ["[out:json][timeout:25];("]
    for p in pts:
        body.append(f'way(around:{radius_m},{p["lat"]},{p["lng"]})["highway"];')
    body.append(");out tags center qt;")
    query = "".join(body)
    urls = [u.strip() for u in overpass_url.split(",") if u.strip()]
    last_exc: Exception | None = None
    elements: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=40) as c:
        for url in urls:
            try:
                r = await c.get(url, params={"data": query})
                r.raise_for_status()
                elements = r.json().get("elements", [])
                if elements:
                    last_exc = None
                    break
            except Exception as exc:
                last_exc = exc
    if not elements and last_exc is None:
        raise RuntimeError("Nessun elemento OSM trovato sui mirror Overpass configurati")
    if last_exc is not None:
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
    return {
        "dominant_surface": dominant,
        "dominant_smoothness": dominant_smoothness,
        "has_cobblestone": has_cobblestone,
        "samples": ways,
    }
