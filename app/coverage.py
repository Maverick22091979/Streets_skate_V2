import math
from collections import defaultdict


TOLERANCE_M = 12
STEP_M = 20
MAX_GAP_M = 250
CELL_M = 40


def _coord(p):
    try:
        lat, lng = float(p[0]), float(p[1])
        if math.isfinite(lat) and math.isfinite(lng) and abs(lat) <= 85 and abs(lng) <= 180:
            return lat, lng
    except (TypeError, ValueError, IndexError, KeyError, OverflowError):
        pass
    return None


def _distance(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    t = max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy)))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def build_coverage(tracks):
    tracks = [[_coord(p) for p in pts] for pts in tracks]
    origin = next((p for pts in tracks for p in pts if p), (45, 9))
    sx = 111195 * math.cos(math.radians(origin[0]))
    sy = 111195
    cells = defaultdict(list)
    edges = []
    mapped = set()
    gaps = 0

    def project(p):
        return (p[1] - origin[1]) * sx, (p[0] - origin[0]) * sy

    def unproject(p):
        return [round(p[0] / sx + origin[1], 7), round(p[1] / sy + origin[0], 7)]

    for rid, pts in enumerate(tracks):
        for p, q in zip(pts, pts[1:]):
            if p is None or q is None:
                continue
            a, b = project(p), project(q)
            length = math.dist(a, b)
            if length > MAX_GAP_M:
                gaps += 1
                continue
            if length < 0.5:
                continue
            n = math.ceil(length / STEP_M)
            for i in range(n):
                c = (a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n)
                d = (a[0] + (b[0] - a[0]) * (i + 1) / n, a[1] + (b[1] - a[1]) * (i + 1) / n)
                v = ((d[0] - c[0]) / (length / n), (d[1] - c[1]) / (length / n))
                key = (math.floor((c[0] + d[0]) / 2 / CELL_M), math.floor((c[1] + d[1]) / 2 / CELL_M))
                edge = (rid, c, d, v)
                cells[key].append(edge)
                edges.append(edge)
                mapped.add(rid)

    groups = defaultdict(list)
    drawn = set()
    for rid, a, b, v in edges:
        start, end = unproject(a), unproject(b)
        key = tuple(sorted((tuple(start), tuple(end))))
        if key in drawn:
            continue
        drawn.add(key)
        matches = None
        for t in (0.2, 0.5, 0.8):
            p = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
            cx, cy = math.floor(p[0] / CELL_M), math.floor(p[1] / CELL_M)
            found = {rid}
            for x in range(cx - 1, cx + 2):
                for y in range(cy - 1, cy + 2):
                    for other, c, d, w in cells.get((x, y), ()):
                        if other in found or (matches is not None and other not in matches):
                            continue
                        if abs(v[0] * w[0] + v[1] * w[1]) >= 0.8660254 and _distance(p, c, d) <= TOLERANCE_M:
                            found.add(other)
            matches = found if matches is None else matches & found
            if len(matches) == 1:
                break
        count = len(matches)
        groups[count].append([start, end])

    total = len(tracks)
    return {
        "total_routes": total,
        "mapped_routes": len(mapped),
        "missing_routes": total - len(mapped),
        "skipped_gaps": gaps,
        "tolerance_m": TOLERANCE_M,
        "max_gap_m": MAX_GAP_M,
        "geojson": {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"count": count, "percentage": 100 * count / total},
                    "geometry": {"type": "MultiLineString", "coordinates": groups[count]},
                }
                for count in sorted(groups)
            ],
        },
    }


def track_coords(points, polyline, decode):
    pts = [
        _coord((p.get("lat"), p.get("lng"))) if isinstance(p, dict) else None
        for p in points
    ] if isinstance(points, list) else []
    if sum(p is not None for p in pts) >= 2:
        return pts
    try:
        return [_coord(p) for p in decode(polyline or "")]
    except (IndexError, TypeError, ValueError):
        return []


def track_lines(points):
    lines = []
    line = []
    prev = None
    for p in points:
        gap = False
        if p and prev:
            dx = (p[1] - prev[1]) * 111195 * math.cos(math.radians((p[0] + prev[0]) / 2))
            dy = (p[0] - prev[0]) * 111195
            gap = math.hypot(dx, dy) > MAX_GAP_M
        if p is None or gap:
            if len(line) >= 2:
                lines.append(line)
            line = []
        if p:
            line.append([p[1], p[0]])
        prev = p
    if len(line) >= 2:
        lines.append(line)
    return lines
