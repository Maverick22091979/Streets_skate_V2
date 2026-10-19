import asyncio
import json
import threading
import time
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from app.db import Base
from app.main import (
    IMPORT_JOBS, _import_provider_api, _normalize_for_storage,
    _store_routes, _run_provider_import_job, api_import_job_status,
)
from app.models import AuthConnection, Route, User
from app.providers import build_provider_registry


def activity(n):
    return {
        "id": n, "name": f"Pattinata {n}", "sport_type": "InlineSkate", "distance": 1200,
        "start_date": "2026-10-01T10:00:00Z", "start_date_local": "2026-10-01T12:00:00Z",
        "map": {"summary_polyline": "_p~iF~ps|U_ulLnnqC_mqNvxq`@"},
    }


POINTS = [{"lat": 45, "lng": 9}, {"lat": 45, "lng": 9.005}, {"lat": 45.001, "lng": 9.01}]


class StravaAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_streams_request_and_aligned_points_with_optional_measurements(self):
        adapter = build_provider_registry("http://test")["strava"]

        def respond(req):
            self.assertEqual(req.url.path, "/api/v3/activities/7/streams")
            self.assertEqual(req.url.params["key_by_type"], "true")
            self.assertIn("latlng", req.url.params["keys"])
            self.assertEqual(req.headers["Authorization"], "Bearer test-token")
            return httpx.Response(200, json={
                "latlng": {"data": [[45, 9], None, [45.001, 9.002]]},
                "altitude": {"data": [100, 105, 110]},
                "time": {"data": [0, 5, 10]},
                "distance": {"data": [0, 120, 240]},
                "velocity_smooth": {"data": [2, 3, 4]},
            })

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        with patch("app.providers.httpx.AsyncClient", return_value=client):
            pts = await adapter.fetch_activity_points("test-token", activity(7))
        self.assertEqual(len(pts), 2)
        self.assertEqual(pts[1]["time"], "2026-10-01T10:00:10+00:00")
        self.assertEqual(pts[1]["elevation"], 110)
        self.assertEqual(pts[1]["cumulative_distance_m"], 240)
        self.assertAlmostEqual(pts[1]["speed_kmh"], 14.4)

    async def test_missing_optional_streams_still_returns_all_gps_points(self):
        adapter = build_provider_registry("http://test")["strava"]
        with patch.object(adapter, "_get", new=AsyncMock(return_value={"latlng": {"data": [[45, 9], [45, 9.1]]}})):
            self.assertEqual(len(await adapter.fetch_activity_points("token", activity(1))), 2)

    async def test_short_rate_limit_waits_and_retries_without_consuming_daily_limit(self):
        adapter = build_provider_registry("http://test")["strava"]
        calls = 0

        def respond(req):
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx.Response(429, headers={"Retry-After": "12", "X-ReadRateLimit-Limit": "200,2000", "X-ReadRateLimit-Usage": "200,500"})
            return httpx.Response(200, json=[])

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        with patch("app.providers.httpx.AsyncClient", return_value=client), patch("app.providers.asyncio.sleep", new=AsyncMock()) as sleep:
            self.assertEqual(await adapter.fetch_activities("token", None, 1, 50), [])
            sleep.assert_awaited_once_with(12)
        self.assertEqual(calls, 2)

    async def test_daily_rate_limit_stops_with_actionable_error(self):
        adapter = build_provider_registry("http://test")["strava"]
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(
            429, headers={"X-ReadRateLimit-Limit": "200,2000", "X-ReadRateLimit-Usage": "50,2000"},
        )))
        with patch("app.providers.httpx.AsyncClient", return_value=client):
            with self.assertRaisesRegex(RuntimeError, "giornaliero Strava"):
                await adapter.fetch_activities("token", None, 1, 50)


class StravaImportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.user = User(display_name="Test")
        self.db.add(self.user)
        self.db.flush()
        self.conn = AuthConnection(user_id=self.user.id, provider="strava", provider_user_id="athlete", access_token="test", expires_at=datetime.now(UTC) + timedelta(hours=1))
        self.db.add(self.conn)
        self.db.commit()
        self.adapter = build_provider_registry("http://test")["strava"]
        self.reg = patch("app.main.PROVIDERS", {"strava": self.adapter})
        self.reg.start()
        self.enrich = patch("app.main._enrich_route_data", new=AsyncMock(side_effect=lambda db, data: data))
        self.enrich.start()
        self.warm = patch("app.main._coverage_cache.warm")
        self.warm.start()
        self.refresh = patch("app.main._refresh_connection_token", new=AsyncMock(return_value="test"))
        self.refresh.start()

    def tearDown(self):
        self.refresh.stop()
        self.warm.stop()
        self.enrich.stop()
        self.reg.stop()
        self.db.close()
        self.engine.dispose()

    async def run_import(self, pages, **kwargs):
        async def fetch(token, conn, page, per_page):
            self.assertFalse(self.db.in_transaction())
            return pages.get(page, [])

        with patch.object(self.adapter, "fetch_activities", side_effect=fetch) as fetch_pages:
            result = await _import_provider_api(self.db, self.user, self.conn, "strava", **kwargs)
        return result, fetch_pages

    async def test_imports_more_than_fifty_and_continues_after_short_page(self):
        progress = AsyncMock()
        pages = {1: [activity(i) for i in range(1, 51)], 2: [activity(51)], 3: [activity(52)]}

        async def points(token, entry):
            self.assertFalse(self.db.in_transaction())
            return POINTS

        with patch.object(self.adapter, "fetch_activity_points", side_effect=points):
            result, fetch = await self.run_import(pages, on_progress=progress)
        self.assertEqual(result[1], 52)
        self.assertEqual(len(result[0]), 52)
        self.assertEqual([c.args[2] for c in fetch.call_args_list], [1, 2, 3, 4])
        self.assertEqual(progress.await_args.args[:3], (52, 52, 52))
        route = self.db.scalar(select(Route).where(Route.external_id == "1"))
        self.assertEqual(route.enrichment["gps_source"], "strava-streams")
        self.assertEqual(len(route.enrichment["point_profile"]["points"]), 3)
        self.assertEqual(route.polyline_points_count, 3)
        self.assertEqual(route.bbox["south"], 45)

    async def test_existing_summary_is_upgraded_in_place_and_detailed_routes_are_reused(self):
        item = _normalize_for_storage("strava", self.adapter.normalize_activity(activity(1)))
        _store_routes(self.db, self.user, self.conn, [item], load_routes=False)
        rid = self.db.scalar(select(Route.id))
        with patch.object(self.adapter, "fetch_activity_points", new=AsyncMock(return_value=POINTS)) as streams:
            result, _ = await self.run_import({1: [activity(1)]})
            self.assertEqual(result[1], 1)
            self.assertEqual(result[0][0].id, rid)
            result, _ = await self.run_import({1: [activity(1)]})
            self.assertEqual(result[1], 0)
            streams.assert_awaited_once()
        self.assertEqual(len(list(self.db.scalars(select(Route)))), 1)

    async def test_stream_failure_uses_summary_and_does_not_stop_later_activities(self):
        warnings = []
        with patch.object(self.adapter, "fetch_activity_points", new=AsyncMock(side_effect=[httpx.ReadTimeout("timeout"), POINTS])):
            result, _ = await self.run_import({1: [activity(1), activity(2)]}, warnings=warnings)
        self.assertEqual(result[1], 2)
        self.assertEqual(len(warnings), 1)
        routes = {r.external_id: r for r in result[0]}
        self.assertEqual(routes["1"].enrichment["gps_source"], "strava-summary")
        self.assertEqual(routes["2"].enrichment["gps_source"], "strava-streams")

    async def test_later_page_failure_preserves_previously_saved_routes(self):
        with patch.object(self.adapter, "fetch_activities", new=AsyncMock(side_effect=[[activity(1)], httpx.ReadTimeout("page timeout")])), \
             patch.object(self.adapter, "fetch_activity_points", new=AsyncMock(return_value=POINTS)):
            with self.assertRaises(httpx.ReadTimeout):
                await _import_provider_api(self.db, self.user, self.conn, "strava")
        self.db.close()
        self.assertEqual(len(list(self.db.scalars(select(Route)))), 1)

    async def test_manual_activities_do_not_request_gps(self):
        entry = {**activity(1), "manual": True, "map": {}}
        with patch.object(self.adapter, "fetch_activity_points", new=AsyncMock()) as streams:
            result, _ = await self.run_import({1: [entry]})
        streams.assert_not_awaited()
        self.assertEqual(result[0][0].enrichment["gps_source"], "strava-no-gps")

    async def test_job_status_requires_owner_and_refreshes_session(self):
        IMPORT_JOBS["private"] = {"status": "running", "_connection_id": 7}
        try:
            for cid in [None, 8, 7]:
                req = Request({"type": "http", "headers": [], "session": {
                    "user_id": 1, "connection_id": cid, "session_started_at": time.time(), "session_last_seen_at": time.time() - 20,
                }})
                if cid != 7:
                    with self.assertRaises(HTTPException):
                        api_import_job_status("private", req)
                else:
                    response = api_import_job_status("private", req)
                    self.assertNotIn("_connection_id", json.loads(response.body))
                    self.assertLess(time.time() - req.session["session_last_seen_at"], 1)
        finally:
            IMPORT_JOBS.pop("private", None)

    async def test_background_import_keeps_status_available_during_blocking_enrichment(self):
        entered, release = threading.Event(), threading.Event()
        uid, cid = self.user.id, self.conn.id
        self.db.close()
        IMPORT_JOBS["worker"] = {"status": "running", "_connection_id": cid}

        async def enrich(db, data):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("worker non sbloccato")
            return data

        with patch("app.main.SessionLocal", sessionmaker(bind=self.engine)), \
             patch.object(self.adapter, "fetch_activities", new=AsyncMock(side_effect=[[activity(1)], []])), \
             patch.object(self.adapter, "fetch_activity_points", new=AsyncMock(return_value=POINTS)), \
             patch("app.main._enrich_route_data", side_effect=enrich):
            task = asyncio.create_task(_run_provider_import_job("worker", uid, cid, "strava"))
            try:
                for _ in range(100):
                    if entered.is_set():
                        break
                    await asyncio.sleep(0.01)
                self.assertTrue(entered.is_set(), IMPORT_JOBS["worker"])
                req = Request({"type": "http", "headers": [], "session": {
                    "user_id": uid, "connection_id": cid, "session_started_at": time.time(), "session_last_seen_at": time.time(),
                }})
                response = api_import_job_status("worker", req)
                self.assertEqual(json.loads(response.body)["status"], "running")
            finally:
                release.set()
                await task
            self.assertEqual(IMPORT_JOBS["worker"]["status"], "done")
            self.assertEqual(IMPORT_JOBS["worker"]["result"]["imported"], 1)
        IMPORT_JOBS.pop("worker", None)
