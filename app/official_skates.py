"""Recognition rules for official Street Skate outings.

The functions in this module are intentionally side-effect free.  Database and
calendar adapters supply data; this makes the GPS decision trail testable and
auditable.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

ROME = ZoneInfo("Europe/Rome")

# These are configurable operational anchors, not historical assertions.  They
# can be overridden through OFFICIAL_MEETING_POINTS_JSON before use in production.
DEFAULT_MEETING_POINTS = {
    "san_carlo_al_corso": {"label": "San Carlo al Corso / Largo Goldoni", "lat": 41.90649, "lng": 12.47871},
    "piazzale_flaminio": {"label": "Piazzale Flaminio", "lat": 41.91072, "lng": 12.47662},
    "ponte_pincio": {"label": "Ponte del Pincio, Viale delle Magnolie", "lat": 41.91527, "lng": 12.47688},
}


@dataclass(frozen=True)
class RecognitionResult:
    state: str
    reasons: list[str]
    start_index: int | None
    end_index: int | None
    meeting_point_key: str | None
    event_id: str | None
    event_match: bool


def haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    radius = 6_371_000.0
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(h))


def official_day(activity_date: date) -> bool:
    """Official schedule: first/third Monday, every Wednesday, configured Sundays."""
    if activity_date.weekday() == 2:
        return True
    if activity_date.weekday() == 0:
        occurrence = (activity_date.day - 1) // 7 + 1
        return occurrence in {1, 3}
    return activity_date.weekday() == 6


def is_scheduled_monday(activity_date: date) -> bool:
    return activity_date.weekday() == 0 and ((activity_date.day - 1) // 7 + 1) in {1, 3}


def parse_datetime(value: str | datetime | None) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(ROME) if value.tzinfo else value.replace(tzinfo=ROME)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(ROME) if parsed.tzinfo else parsed.replace(tzinfo=ROME)


def expected_rule(activity_date: date, historical_rules: list[dict[str, Any]]) -> dict[str, Any]:
    """Select the configured historical interval; no undocumented COVID dates."""
    for rule in historical_rules:
        start = date.fromisoformat(rule["start_date"]) if rule.get("start_date") else None
        end = date.fromisoformat(rule["end_date"]) if rule.get("end_date") else None
        if (start is None or activity_date >= start) and (end is None or activity_date <= end):
            return rule
    # The post-COVID operational default remains explicit rather than inventing a transition date.
    return {"name": "post_covid", "meeting_point_key": "piazzale_flaminio", "start_time": "21:00", "end_time": None}


def _time_matches(value: datetime | None, expected: str | None, tolerance_minutes: int = 90) -> bool:
    if value is None or not expected:
        return False
    try:
        expected_time = time.fromisoformat(expected)
    except ValueError:
        return False
    target = datetime.combine(value.date(), expected_time, tzinfo=ROME)
    return abs((value - target).total_seconds()) <= tolerance_minutes * 60


def _event_matches(activity_start: datetime | None, event: dict[str, Any] | None) -> bool:
    if not activity_start or not event:
        return False
    event_start = parse_datetime(event.get("start_at"))
    if not event_start:
        return False
    return abs((activity_start - event_start).total_seconds()) <= 4 * 3600


def nearest_meeting_passes(
    points: list[dict[str, Any]], meeting_points: dict[str, dict[str, Any]], tolerance_m: float
) -> dict[str, list[int]]:
    passes: dict[str, list[int]] = {key: [] for key in meeting_points}
    for index, point in enumerate(points):
        if point.get("lat") is None or point.get("lng") is None:
            continue
        pos = (float(point["lat"]), float(point["lng"]))
        for key, place in meeting_points.items():
            if haversine_m(pos, (float(place["lat"]), float(place["lng"]))) <= tolerance_m:
                passes[key].append(index)
    return {key: indexes for key, indexes in passes.items() if indexes}


def recognize_official_segment(
    points: list[dict[str, Any]],
    activity_start: str | datetime | None,
    historical_rules: list[dict[str, Any]],
    calendar_event: dict[str, Any] | None = None,
    meeting_points: dict[str, dict[str, Any]] | None = None,
    tolerance_m: float = 30,
) -> RecognitionResult:
    meeting_points = meeting_points or DEFAULT_MEETING_POINTS
    start = parse_datetime(activity_start) or (parse_datetime(points[0].get("time")) if points else None)
    if not start:
        return RecognitionResult("needs_review", ["timestamp attività assente"], None, None, None, None, False)
    if not official_day(start.date()):
        return RecognitionResult("personal", ["giorno non previsto dal calendario ufficiale"], None, None, None, None, False)
    if len(points) < 2:
        return RecognitionResult("needs_review", ["punti GPS insufficienti"], None, None, None, None, False)

    rule = expected_rule(start.date(), historical_rules)
    is_sunday = start.weekday() == 6
    # A Calendar location has precedence over a generic historical rule, but it
    # never turns a non-official day into an official one.
    location = str((calendar_event or {}).get("location") or "").casefold()
    event_point = None
    if "pincio" in location or "magnolie" in location:
        event_point = "ponte_pincio"
    elif "flaminio" in location:
        event_point = "piazzale_flaminio"
    elif "san carlo" in location or "goldoni" in location:
        event_point = "san_carlo_al_corso"
    allowed = {event_point} if event_point else ({"ponte_pincio", "piazzale_flaminio"} if is_sunday else {str(rule.get("meeting_point_key") or "piazzale_flaminio")})
    passes = nearest_meeting_passes(points, meeting_points, tolerance_m)
    candidates = [(idx, key) for key in allowed for idx in passes.get(key, [])]
    if not candidates:
        return RecognitionResult("needs_review", ["nessun passaggio nel punto di ritrovo entro tolleranza"], None, None, None, calendar_event.get("external_id") if calendar_event else None, False)
    start_index, point_key = min(candidates)
    same_point_visits = [idx for idx in passes.get(point_key, []) if idx > start_index]
    # Returning to the meeting point is the strongest automatic end boundary.
    end_index = same_point_visits[-1] if same_point_visits else len(points) - 1
    reasons: list[str] = []
    event_match = _event_matches(start, calendar_event)
    if calendar_event:
        if event_match:
            reasons.append("orario compatibile con evento Google Calendar")
        else:
            reasons.append("orario non compatibile con evento Google Calendar")
    elif is_sunday:
        reasons.append("domenica senza evento Google Calendar associato")
    elif _time_matches(start, rule.get("start_time")):
        reasons.append("orario compatibile con regola storica")
    else:
        reasons.append("orario non compatibile con regola storica")
    if not same_point_visits:
        reasons.append("ritorno al punto di ritrovo non rilevato")
    ambiguous = (is_sunday and not event_match) or not same_point_visits or (bool(calendar_event) and not event_match)
    return RecognitionResult(
        "needs_review" if ambiguous else "validated",
        reasons,
        start_index,
        end_index,
        point_key,
        str(calendar_event.get("external_id")) if calendar_event else None,
        event_match,
    )
