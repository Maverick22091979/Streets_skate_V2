import asyncio
import base64
import io
import json
import time
import threading
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException, UploadFile
import httpx
from itsdangerous import TimestampSigner
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.db import Base
from app.main import (
    GPX_IMPORT_JOBS, GPX_IMPORT_TASKS, _import_gpx_bundles, _read_gpx_bundles,
    api_gpx_import_status, api_import_gpx_start,
    app, APP_SECRET_KEY, SESSION_COOKIE_NAME, _require_gpx_admin,
)
from app.models import AuditLog, Route


def request(token="test", admin=True):
    return Request({
        "type": "http", "method": "POST", "path": "/api/manual-import/gpx/start", "headers": [],
        "session": {"csrf_token": token, "session_started_at": time.time(), "session_last_seen_at": time.time(),
                    **({"local_admin": {"label": "Admin", "username": "admin"}} if admin else {})},
    })


def upload(name="test.gpx", raw=b"<gpx/>"):
    return UploadFile(filename=name, file=io.BytesIO(raw))


class GpxJobTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        for task in list(GPX_IMPORT_TASKS):
            task.cancel()
        await asyncio.gather(*GPX_IMPORT_TASKS, return_exceptions=True)
        GPX_IMPORT_JOBS.clear()

    async def test_upload_returns_before_enrichment_finishes_and_polls_to_completion(self):
        gate = threading.Event()

        async def work(*args, **kwargs):
            if not gate.wait(5):
                raise TimeoutError("worker non sbloccato")
            return {"imported": 1, "routes": None}

        factory = Mock()
        factory.return_value.__enter__ = Mock(return_value=Mock())
        factory.return_value.__exit__ = Mock(return_value=False)
        req = request()
        with patch("app.main.SessionLocal", factory), patch("app.main._coverage_cache.warm"), patch("app.main._import_gpx_bundles", side_effect=work):
            res = await api_import_gpx_start(req, csrf_token="test", files=[upload()])
            self.assertEqual(res.status_code, 202)
            jid = json.loads(res.body)["job_id"]
            await asyncio.sleep(0)
            pending = json.loads(api_gpx_import_status(jid, req).body)
            self.assertEqual(pending["status"], "running")
            self.assertNotIn("_owner", pending)
            gate.set()
            await asyncio.gather(*GPX_IMPORT_TASKS)
            done = json.loads(api_gpx_import_status(jid, req).body)
            self.assertEqual(done["status"], "done")
            self.assertEqual(done["result"]["imported"], 1)

    async def test_http_start_and_poll_work_while_enrichment_is_waiting(self):
        gate = threading.Event()
        started = threading.Event()

        async def work(*args, **kwargs):
            started.set()
            if not gate.wait(5):
                raise TimeoutError("worker non sbloccato")
            return {"imported": 1}

        cookie = TimestampSigner(APP_SECRET_KEY).sign(base64.b64encode(json.dumps(request().session).encode())).decode()
        factory = Mock()
        factory.return_value.__enter__ = Mock(return_value=Mock())
        factory.return_value.__exit__ = Mock(return_value=False)
        with patch("app.main.SessionLocal", factory), patch("app.main._coverage_cache.warm"), patch("app.main._import_gpx_bundles", side_effect=work):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test", cookies={SESSION_COOKIE_NAME: cookie}) as client:
                try:
                    res = await asyncio.wait_for(client.post(
                        "/api/manual-import/gpx/start", data={"csrf_token": "test"}, files={"files": ("test.gpx", b"<gpx/>")},
                    ), timeout=2)
                    self.assertEqual(res.status_code, 202)
                    url = f"/api/manual-import/jobs/{res.json()['job_id']}"
                    for _ in range(100):
                        if started.is_set():
                            break
                        await asyncio.sleep(0.01)
                    self.assertTrue(started.is_set())
                    poll = await asyncio.wait_for(client.get(url), timeout=1)
                    self.assertEqual(poll.status_code, 200)
                    self.assertEqual(poll.json()["status"], "running")
                finally:
                    gate.set()
                    await asyncio.gather(*GPX_IMPORT_TASKS)
                self.assertEqual((await client.get(url)).json()["status"], "done")

    async def test_start_requires_admin_and_csrf(self):
        for req, token, code in [(request(admin=False), "test", 401), (request(), "wrong", 403)]:
            with self.assertRaises(HTTPException) as exc:
                await api_import_gpx_start(req, csrf_token=token, files=[upload()])
            self.assertEqual(exc.exception.status_code, code)

    async def test_full_queue_rejects_further_jobs_without_starting_a_worker(self):
        GPX_IMPORT_JOBS.update({str(i): {"status": "running"} for i in range(8)})
        with self.assertRaises(HTTPException) as exc:
            await api_import_gpx_start(request(), csrf_token="test", files=[upload()])
        self.assertEqual(exc.exception.status_code, 429)

    async def test_status_is_private_to_the_admin_session(self):
        GPX_IMPORT_JOBS["job"] = {"status": "running", "_owner": "test"}
        for req, code in [(request("other"), 404), (request(admin=False), 401)]:
            with self.assertRaises(HTTPException) as exc:
                api_gpx_import_status("job", req)
            self.assertEqual(exc.exception.status_code, code)

    async def test_active_job_prevents_inactivity_expiry_only_for_its_admin_session(self):
        GPX_IMPORT_JOBS["active"] = {"status": "running", "_owner": "test"}
        req = request()
        req.session["session_last_seen_at"] = time.time() - 3600
        with patch("app.main.SESSION_INACTIVITY_TIMEOUT_SEC", 60):
            _require_gpx_admin(req)
            self.assertIn("local_admin", req.session)
            self.assertLess(time.time() - req.session["session_last_seen_at"], 1)
            GPX_IMPORT_JOBS["active"]["status"] = "done"
            req.session["session_last_seen_at"] = time.time() - 3600
            with patch("app.main._log_session_timeout"):
                with self.assertRaises(HTTPException) as exc:
                    _require_gpx_admin(req)
            self.assertEqual(exc.exception.status_code, 401)

    async def test_other_admin_job_does_not_extend_an_expired_session(self):
        GPX_IMPORT_JOBS["other"] = {"status": "running", "_owner": "other"}
        req = request()
        req.session["session_last_seen_at"] = time.time() - 3600
        with patch("app.main.SESSION_INACTIVITY_TIMEOUT_SEC", 60), patch("app.main._log_session_timeout"):
            with self.assertRaises(HTTPException) as exc:
                _require_gpx_admin(req)
        self.assertEqual(exc.exception.status_code, 401)

    async def test_authenticated_non_admin_cannot_reuse_an_active_admin_job(self):
        GPX_IMPORT_JOBS["active"] = {"status": "running", "_owner": "test"}
        req = request(admin=False)
        req.session.update(user_id=7, connection_id=9)
        with self.assertRaises(HTTPException) as exc:
            _require_gpx_admin(req)
        self.assertEqual(exc.exception.status_code, 403)

    async def test_http_distinguishes_invalid_cookie_from_authenticated_non_admin(self):
        session = dict(request(admin=False).session, user_id=7, connection_id=9)
        payload = base64.b64encode(json.dumps(session).encode())
        cookies = [
            (TimestampSigner("old-key-before-deploy").sign(payload).decode(), 401),
            (TimestampSigner(APP_SECRET_KEY).sign(payload).decode(), 403),
        ]
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            for cookie, code in cookies:
                res = await client.post(
                    "/api/manual-import/gpx/start", data={"csrf_token": "test"},
                    files={"files": ("test.gpx", b"<gpx/>")},
                    headers={"Cookie": f"{SESSION_COOKIE_NAME}={cookie}"},
                )
                self.assertEqual(res.status_code, code)
                self.assertIn("admin", res.json()["detail"])

    async def test_expired_session_logs_once_without_reentering_authentication(self):
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        req = request()
        req.session["session_last_seen_at"] = time.time() - 3600
        with patch("app.main.SessionLocal", side_effect=lambda: Session(engine)), \
             patch("app.main.SESSION_INACTIVITY_TIMEOUT_SEC", 60):
            with self.assertRaises(HTTPException) as exc:
                _require_gpx_admin(req)
        self.assertEqual(exc.exception.status_code, 401)
        self.assertEqual(req.session, {})
        with Session(engine) as db:
            logs = list(db.scalars(select(AuditLog)))
            self.assertEqual(len(logs), 1)
            self.assertEqual(logs[0].action, "session_timeout")
            self.assertEqual(logs[0].actor_role, "Admin")
        engine.dispose()

    async def test_json_only_and_oversized_uploads_are_rejected(self):
        with self.assertRaises(HTTPException) as exc:
            await _read_gpx_bundles([upload("test.json", b"[]")])
        self.assertEqual(exc.exception.status_code, 400)
        with patch("app.main.GPX_UPLOAD_MAX_BYTES", 8):
            with self.assertRaises(HTTPException) as exc:
                await _read_gpx_bundles([upload(raw=b"123456789")])
            self.assertEqual(exc.exception.status_code, 413)

    async def test_background_import_saves_a_gpx_and_skips_repeated_filename(self):
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        raw = b'''<gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><name>Test</name><type>skating</type><trkseg>
        <trkpt lat="45" lon="9"><time>2026-10-01T10:00:00Z</time></trkpt>
        <trkpt lat="45" lon="9.01"><time>2026-10-01T10:05:00Z</time></trkpt>
        </trkseg></trk></gpx>'''
        bundles = {"test": {".gpx": ("test.gpx", raw)}}
        with Session(engine) as db, \
             patch("app.main._enrich_route_data", new=AsyncMock(side_effect=lambda db, data: data)), \
             patch("app.main._coverage_cache.warm"), patch("app.main._write_audit_log_for_actor"):
            result = await _import_gpx_bundles(request(), db, bundles, 1, include_routes=False)
            self.assertEqual(result["imported"], 1)
            self.assertIsNone(result["routes"])
            result = await _import_gpx_bundles(request(), db, bundles, 1, include_routes=False)
            self.assertEqual(result["skipped_filename"], 1)
            self.assertEqual(len(list(db.scalars(select(Route)))), 1)
        engine.dispose()

    async def test_database_connection_is_released_during_parsing_and_enrichment(self):
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        active = 0

        def checkout(*args):
            nonlocal active
            active += 1

        def checkin(*args):
            nonlocal active
            active -= 1

        event.listen(engine, "checkout", checkout)
        event.listen(engine, "checkin", checkin)

        def parse(*args, **kwargs):
            self.assertEqual(active, 0)
            return {"sport_type": "skating", "distance_m": 1500}

        async def enrich(db, data):
            self.assertIsNone(db)
            self.assertEqual(active, 0)
            await asyncio.sleep(0)
            return data

        bundles = {"test": {".gpx": ("test.gpx", b"<gpx/>")}}
        with Session(engine) as db, \
             patch("app.main.parse_gpx_bytes", side_effect=parse), \
             patch("app.main._normalize_for_storage", return_value={"enrichment": {}}), \
             patch("app.main._enrich_route_data", side_effect=enrich), \
             patch("app.main._store_routes"), patch("app.main._write_audit_log_for_actor"):
            await _import_gpx_bundles(request(), db, bundles, 1, include_routes=False)
        self.assertEqual(active, 0)
        engine.dispose()

    async def test_successful_files_remain_committed_if_a_later_file_is_invalid(self):
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        raw = b'''<gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><type>skating</type><trkseg>
        <trkpt lat="45" lon="9"><time>2026-10-01T10:00:00Z</time></trkpt>
        <trkpt lat="45" lon="9.02"><time>2026-10-01T10:05:00Z</time></trkpt>
        </trkseg></trk></gpx>'''
        bundles = {"ok": {".gpx": ("ok.gpx", raw)}, "bad": {".gpx": ("bad.gpx", b"invalid")}}
        with Session(engine) as db, \
             patch("app.main._enrich_route_data", new=AsyncMock(side_effect=lambda db, data: data)):
            with self.assertRaises(Exception):
                await _import_gpx_bundles(request(), db, bundles, 2, include_routes=False)
        with Session(engine) as db:
            self.assertEqual(len(list(db.scalars(select(Route)))), 1)
        engine.dispose()
