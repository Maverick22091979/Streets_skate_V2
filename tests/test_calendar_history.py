import asyncio
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.main import _fetch_calendar_year, _run_calendar_history
from app.models import AuthConnection, CalendarEvent, CalendarSyncState, OfficialSkateCandidate, Route, User


class CalendarHistoryTests(unittest.TestCase):
    def test_fetches_each_page_with_italian_year_boundaries(self):
        requests = []

        def respond(request):
            requests.append(request)
            if len(requests) == 1:
                return httpx.Response(200, json={"items": [{"id": "first"}], "nextPageToken": "page-two"})
            return httpx.Response(200, json={"items": [{"id": "second"}]})

        real_client = httpx.AsyncClient
        with patch("app.main.GOOGLE_CALENDAR_API_KEY", "test-key"), \
             patch("app.main.httpx.AsyncClient", side_effect=lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs)):
            items = asyncio.run(_fetch_calendar_year(2020))
        self.assertEqual([item["id"] for item in items], ["first", "second"])
        self.assertEqual(requests[0].url.params["timeMin"], "2020-01-01T00:00:00+01:00")
        self.assertEqual(requests[0].url.params["timeMax"], "2021-01-01T00:00:00+01:00")
        self.assertEqual(requests[1].url.params["pageToken"], "page-two")

    def test_yearly_job_preserves_decisions_and_reports_failed_years(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, autoflush=False)
        year = datetime.now(ZoneInfo("Europe/Rome")).year
        points = [{"lat": 41.91072, "lng": 12.47662, "time": f"{year}-01-07T21:00:00+01:00"},
                  {"lat": 41.911, "lng": 12.477, "time": f"{year}-01-07T22:00:00+01:00"}]
        with factory() as db:
            user = User(display_name="Test")
            db.add(user); db.flush()
            conn = AuthConnection(user_id=user.id, provider="manual", provider_user_id="test", access_token="manual")
            db.add(conn); db.flush()
            for index, activity_year in enumerate((year - 1, year)):
                route = Route(user_id=user.id, connection_id=conn.id, provider="manual", external_id=f"test-{index}",
                              start_date_local=f"{activity_year}-01-07T21:00:00+01:00",
                              enrichment={"point_profile": {"points": points}})
                db.add(route); db.flush()
                if index == 1:
                    db.add(OfficialSkateCandidate(route_id=route.id, owner_user_id=user.id, status="approved", reasons=["decisione esistente"]))
            db.add(CalendarSyncState(calendar_id="test-calendar", sync_token="keep-me"))
            db.commit()

        async def fetch(activity_year):
            if activity_year == year - 1:
                raise RuntimeError("simulated failure")
            return [{"id": "event", "summary": "Evento", "start": {"dateTime": f"{year}-01-07T21:00:00+01:00"},
                     "end": {"dateTime": f"{year}-01-07T22:00:00+01:00"}}]

        job = {"status": "running", "completed_years": 0, "events_updated": 0, "routes_checked": 0, "failed_years": []}
        with patch("app.main.SessionLocal", factory), patch("app.main.GOOGLE_CALENDAR_ID", "test-calendar"), \
             patch("app.main._fetch_calendar_year", new=AsyncMock(side_effect=fetch)), \
             patch("app.main._send_candidate_review_email") as send:
            _run_calendar_history(job)
        self.assertEqual(job["status"], "partial")
        self.assertEqual(job["failed_years"], [year - 1])
        self.assertEqual(job["routes_checked"], 1)
        send.assert_not_called()
        with factory() as db:
            candidate = db.scalars(select(OfficialSkateCandidate)).one()
            self.assertEqual((candidate.status, candidate.reasons), ("approved", ["decisione esistente"]))
            self.assertEqual(db.scalars(select(CalendarEvent)).one().external_id, "event")
            self.assertEqual(db.scalars(select(CalendarSyncState)).one().sync_token, "keep-me")
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
