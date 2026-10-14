import math
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.requests import Request

from app.coverage import build_coverage, track_coords, track_lines
from app.main import api_coverage, app, get_db


def pt(x, y=0):
    return 45 + y / 111195, 9 + x / (111195 * math.cos(math.radians(45)))


def counts(data):
    return {f["properties"]["count"] for f in data["geojson"]["features"]}


class CoverageTests(unittest.TestCase):
    def test_empty(self):
        data = build_coverage([])
        self.assertEqual(data["total_routes"], 0)
        self.assertEqual(data["geojson"]["features"], [])

    def test_once_per_route_and_reverse(self):
        data = build_coverage([
            [pt(0), pt(100), pt(0), pt(100)],
            [pt(100), pt(63), pt(15), pt(0)],
            [],
        ])
        self.assertEqual(counts(data), {2})
        self.assertEqual(data["total_routes"], 3)
        self.assertEqual(data["mapped_routes"], 2)
        for f in data["geojson"]["features"]:
            self.assertAlmostEqual(f["properties"]["percentage"], 200 / 3)

    def test_gps_offset(self):
        data = build_coverage([[pt(0), pt(100)], [pt(0, 6), pt(100, 6)]])
        self.assertEqual(counts(data), {2})

    def test_geojson_uses_longitude_latitude_not_projected_meters(self):
        data = build_coverage([[pt(0), pt(100)], [pt(0, 6), pt(100, 6)]])
        for f in data["geojson"]["features"]:
            for line in f["geometry"]["coordinates"]:
                for lng, lat in line:
                    self.assertTrue(9 <= lng < 9.01)
                    self.assertTrue(45 <= lat < 45.01)

    def test_preview_preserves_gaps(self):
        pts = [pt(0), pt(20), None, pt(40), pt(60), pt(1000), pt(1020)]
        lines = track_lines(pts)
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[0], [[pt(0)[1], pt(0)[0]], [pt(20)[1], pt(20)[0]]])

    def test_invalid_profile_falls_back_to_stored_polyline(self):
        data = track_coords([{"lat": None, "lng": None}] * 2, "stored", lambda _: [pt(0), pt(20)])
        self.assertEqual(data, [pt(0), pt(20)])

    def test_separate_parallel_streets(self):
        data = build_coverage([[pt(0), pt(100)], [pt(0, 30), pt(100, 30)]])
        self.assertEqual(counts(data), {1})

    def test_crossing_is_not_a_shared_street(self):
        data = build_coverage([[pt(-100), pt(100)], [pt(0, -100), pt(0, 100)]])
        self.assertEqual(counts(data), {1})

    def test_partial_overlap(self):
        data = build_coverage([[pt(0), pt(100), pt(200)], [pt(100), pt(200)]])
        self.assertEqual(counts(data), {1, 2})
        for f in data["geojson"]["features"]:
            if f["properties"]["count"] == 2:
                for a, b in f["geometry"]["coordinates"]:
                    self.assertGreaterEqual(min(a[0], b[0]), pt(80)[1])

    def test_gaps_and_invalid_coordinates_do_not_join(self):
        data = build_coverage([
            [pt(0), pt(1000)],
            [pt(0), None, pt(100)],
            [pt(0), (float("nan"), 9), pt(100)],
            [(91, 9), (92, 9)],
        ])
        self.assertEqual(data["mapped_routes"], 0)
        self.assertEqual(data["missing_routes"], 4)
        self.assertEqual(data["skipped_gaps"], 1)
        self.assertEqual(counts(data), set())


class CoverageApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        app.dependency_overrides[get_db] = lambda: None

    def tearDown(self):
        app.dependency_overrides.pop(get_db, None)
        self.client.close()

    def test_requires_login(self):
        with patch("app.main._get_session_user", return_value=(None, None)):
            self.assertEqual(self.client.get("/api/coverage").status_code, 401)
            self.assertEqual(self.client.get("/api/coverage/tracks").status_code, 401)
            res = self.client.get("/coverage", follow_redirects=False)
            self.assertEqual(res.status_code, 303)
            self.assertEqual(res.headers["location"], "/")

    def test_password_change_required(self):
        with patch("app.main._must_change_password", return_value=True):
            self.assertEqual(self.client.get("/api/coverage").status_code, 403)
            self.assertEqual(self.client.get("/api/coverage/tracks").status_code, 403)
            res = self.client.get("/coverage", follow_redirects=False)
            self.assertEqual(res.headers["location"], "/change-password")

    def test_page_and_api(self):
        from unittest.mock import Mock

        db = Mock()
        data = build_coverage([[pt(0), pt(100)], [], []])
        app.dependency_overrides[get_db] = lambda: db
        with patch("app.main._get_local_admin", return_value={"label": "Admin"}), \
             patch("app.main._coverage_cache.get", return_value=(data, 200)):
            res = self.client.get("/coverage")
            self.assertEqual(res.status_code, 200)
            self.assertIn('id="coverageMap"', res.text)
            self.assertLess(res.text.index('/static/js/session-guard.js'), res.text.index('/static/js/coverage.js'))
            res = self.client.get("/api/coverage")
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.headers["cache-control"], "no-store")
            data = res.json()
            self.assertEqual(data["total_routes"], 3)
            self.assertEqual(data["mapped_routes"], 1)
            self.assertEqual(counts(data), {1})

    def test_provider_session_can_read_global_archive(self):
        from unittest.mock import Mock

        db = Mock()
        db.execute.return_value = []
        app.dependency_overrides[get_db] = lambda: db
        with patch("app.main._get_session_user", return_value=(object(), object())), \
             patch("app.main._coverage_cache.get", return_value=(build_coverage([]), 200)):
            self.assertEqual(self.client.get("/coverage").status_code, 200)
            self.assertEqual(self.client.get("/api/coverage").status_code, 200)

    def test_pending_calculation_returns_without_waiting(self):
        data = {"status": "processing", "total_routes": 10, "retry_after": 2}
        with patch("app.main._get_local_admin", return_value={"label": "Admin"}), \
             patch("app.main._coverage_cache.get", return_value=(data, 202)):
            res = self.client.get("/api/coverage")
            self.assertEqual(res.status_code, 202)
            self.assertEqual(res.headers["retry-after"], "2")
            self.assertEqual(res.json()["status"], "processing")

    def test_preview_loads_database_routes_in_pages(self):
        from unittest.mock import Mock

        db = Mock()
        pts = [{"lat": p[0], "lng": p[1]} for p in [pt(0), pt(100)]]
        db.execute.return_value = [(1, pts, None), (2, pts, None)]
        app.dependency_overrides[get_db] = lambda: db
        with patch("app.main._get_local_admin", return_value={"label": "Admin"}):
            res = self.client.get("/api/coverage/tracks?limit=1")
            self.assertEqual(res.status_code, 200)
            self.assertTrue(res.json()["has_more"])
            self.assertEqual(res.json()["next_after"], 1)
            self.assertEqual(res.json()["loaded_routes"], 1)
            self.assertEqual(len(res.json()["geojson"]["geometry"]["coordinates"]), 1)

    def test_calculation_refreshes_server_session_even_on_error(self):
        from unittest.mock import Mock

        for error in (False, True):
            with self.subTest(error=error):
                req = Request({"type": "http", "session": {"session_last_seen_at": 1}})
                db = Mock()
                db.execute.return_value = []
                with patch("app.main._get_local_admin", return_value={"label": "Admin"}), \
                     patch("app.main.time.time", return_value=3600), \
                     patch("app.main._coverage_cache.get", side_effect=RuntimeError("failed") if error else None, return_value=({}, 200)):
                    if error:
                        with self.assertRaises(RuntimeError):
                            api_coverage(req, db)
                    else:
                        api_coverage(req, db)
                self.assertEqual(req.session["session_last_seen_at"], 3600)


if __name__ == "__main__":
    unittest.main()
