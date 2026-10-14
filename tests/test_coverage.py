import math
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.requests import Request

from app.coverage import build_coverage
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
            res = self.client.get("/coverage", follow_redirects=False)
            self.assertEqual(res.status_code, 303)
            self.assertEqual(res.headers["location"], "/")

    def test_password_change_required(self):
        with patch("app.main._must_change_password", return_value=True):
            self.assertEqual(self.client.get("/api/coverage").status_code, 403)
            res = self.client.get("/coverage", follow_redirects=False)
            self.assertEqual(res.headers["location"], "/change-password")

    def test_page_and_api(self):
        from unittest.mock import Mock

        db = Mock()
        db.execute.return_value = [
            ({"point_profile": {"points": [{"lat": p[0], "lng": p[1]} for p in [pt(0), pt(100)]]}}, None),
            (None, "_p~iF~ps|U_ulLnnqC_mqNvxq`@"),
            (None, "?"),
        ]
        app.dependency_overrides[get_db] = lambda: db
        with patch("app.main._get_local_admin", return_value={"label": "Admin"}):
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
        with patch("app.main._get_session_user", return_value=(object(), object())):
            self.assertEqual(self.client.get("/coverage").status_code, 200)
            self.assertEqual(self.client.get("/api/coverage").status_code, 200)

    def test_calculation_refreshes_server_session_even_on_error(self):
        from unittest.mock import Mock

        for error in (False, True):
            with self.subTest(error=error):
                req = Request({"type": "http", "session": {"session_last_seen_at": 1}})
                db = Mock()
                db.execute.return_value = []
                with patch("app.main._get_local_admin", return_value={"label": "Admin"}), \
                     patch("app.main.time.time", return_value=3600), \
                     patch("app.main.build_coverage", side_effect=RuntimeError("failed") if error else None, return_value={}):
                    if error:
                        with self.assertRaises(RuntimeError):
                            api_coverage(req, db)
                    else:
                        api_coverage(req, db)
                self.assertEqual(req.session["session_last_seen_at"], 3600)


if __name__ == "__main__":
    unittest.main()
