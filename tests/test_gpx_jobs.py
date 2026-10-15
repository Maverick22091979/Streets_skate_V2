import asyncio
import base64
import io
import json
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException, UploadFile
import httpx
from itsdangerous import TimestampSigner
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.db import Base
from app.main import (
    GPX_IMPORT_JOBS, GPX_IMPORT_TASKS, _import_gpx_bundles, _read_gpx_bundles,
    api_gpx_import_status, api_import_gpx_start,
    app, APP_SECRET_KEY, SESSION_COOKIE_NAME,
)
from app.models import Route


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
        gate = asyncio.Event()

        async def work(*args, **kwargs):
            await gate.wait()
            return {"imported": 1, "routes": None}

        factory = Mock()
        factory.return_value.__enter__ = Mock(return_value=Mock())
        factory.return_value.__exit__ = Mock(return_value=False)
        req = request()
        with patch("app.main.SessionLocal", factory), patch("app.main._import_gpx_bundles", side_effect=work):
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
        gate = asyncio.Event()

        async def work(*args, **kwargs):
            await gate.wait()
            return {"imported": 1}

        cookie = TimestampSigner(APP_SECRET_KEY).sign(base64.b64encode(json.dumps(request().session).encode())).decode()
        factory = Mock()
        factory.return_value.__enter__ = Mock(return_value=Mock())
        factory.return_value.__exit__ = Mock(return_value=False)
        with patch("app.main.SessionLocal", factory), patch("app.main._import_gpx_bundles", side_effect=work):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test", cookies={SESSION_COOKIE_NAME: cookie}) as client:
                try:
                    res = await asyncio.wait_for(client.post(
                        "/api/manual-import/gpx/start", data={"csrf_token": "test"}, files={"files": ("test.gpx", b"<gpx/>")},
                    ), timeout=2)
                    self.assertEqual(res.status_code, 202)
                    url = f"/api/manual-import/jobs/{res.json()['job_id']}"
                    poll = await client.get(url)
                    self.assertEqual(poll.status_code, 200)
                    self.assertEqual(poll.json()["status"], "running")
                finally:
                    gate.set()
                    await asyncio.gather(*GPX_IMPORT_TASKS)
                self.assertEqual((await client.get(url)).json()["status"], "done")

    async def test_start_requires_admin_and_csrf(self):
        for req, token, code in [(request(admin=False), "test", 403), (request(), "wrong", 403)]:
            with self.assertRaises(HTTPException) as exc:
                await api_import_gpx_start(req, csrf_token=token, files=[upload()])
            self.assertEqual(exc.exception.status_code, code)

    async def test_status_is_private_to_the_admin_session(self):
        GPX_IMPORT_JOBS["job"] = {"status": "running", "_owner": "test"}
        for req, code in [(request("other"), 404), (request(admin=False), 403)]:
            with self.assertRaises(HTTPException) as exc:
                api_gpx_import_status("job", req)
            self.assertEqual(exc.exception.status_code, code)

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
