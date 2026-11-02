from datetime import datetime
import unittest

from app.official_skates import DEFAULT_MEETING_POINTS, official_day, recognize_official_segment


def point(key: str, hour: int) -> dict:
    place = DEFAULT_MEETING_POINTS[key]
    return {"lat": place["lat"], "lng": place["lng"], "time": f"2026-10-05T{hour:02d}:00:00+02:00"}


class OfficialSkatesTests(unittest.TestCase):
    rules = [{"name": "post", "meeting_point_key": "piazzale_flaminio", "start_time": "21:00", "end_time": None}]

    def test_calendar_days(self):
        self.assertTrue(official_day(datetime(2026, 10, 5).date()))  # first Monday
        self.assertTrue(official_day(datetime(2026, 10, 19).date()))  # third Monday
        self.assertTrue(official_day(datetime(2026, 10, 7).date()))  # Wednesday
        self.assertTrue(official_day(datetime(2026, 10, 11).date()))  # Sunday
        self.assertFalse(official_day(datetime(2026, 10, 6).date()))

    def test_complete_round_trip_is_validated(self):
        result = recognize_official_segment([point("piazzale_flaminio", 21), {"lat": 41.912, "lng": 12.48, "time": "2026-10-05T21:30:00+02:00"}, point("piazzale_flaminio", 22)], "2026-10-05T21:00:00+02:00", self.rules)
        self.assertEqual(result.state, "validated")
        self.assertEqual((result.start_index, result.end_index), (0, 2))

    def test_multiple_crossings_choose_last_return(self):
        result = recognize_official_segment([point("piazzale_flaminio", 21), point("piazzale_flaminio", 21), {"lat": 41.912, "lng": 12.48, "time": "2026-10-05T21:30:00+02:00"}, point("piazzale_flaminio", 22)], "2026-10-05T21:00:00+02:00", self.rules)
        self.assertEqual(result.end_index, 3)

    def test_sunday_without_event_needs_review(self):
        result = recognize_official_segment([point("ponte_pincio", 10), {"lat": 41.916, "lng": 12.477, "time": "2026-10-11T10:30:00+02:00"}, point("ponte_pincio", 11)], "2026-10-11T10:00:00+02:00", self.rules)
        self.assertEqual(result.state, "needs_review")

    def test_calendar_location_overrides_generic_meeting_point(self):
        event = {"external_id": "event-1", "start_at": "2026-10-05T21:00:00+02:00", "location": "Ponte del Pincio"}
        result = recognize_official_segment([point("ponte_pincio", 21), {"lat": 41.916, "lng": 12.477, "time": "2026-10-05T21:30:00+02:00"}, point("ponte_pincio", 22)], "2026-10-05T21:00:00+02:00", self.rules, event)
        self.assertEqual(result.meeting_point_key, "ponte_pincio")

    def test_personal_day_is_not_candidate(self):
        result = recognize_official_segment([point("piazzale_flaminio", 21), point("piazzale_flaminio", 22)], "2026-10-06T21:00:00+02:00", self.rules)
        self.assertEqual(result.state, "personal")


if __name__ == "__main__":
    unittest.main()
