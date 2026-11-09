import json
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from app.db import Base
from app.main import ADMIN_USERNAME, _dashboard_routes, _is_route_owner, _visible_routes, admin_publish_routes, api_coverage, api_coverage_tracks, route_official_review, route_publication
from app.models import AuthConnection, OfficialSkateCandidate, OfficialSkateDecision, Route, User


class RoutePublicationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, autoflush=False)()
        self.owner, self.other = User(display_name="Proprietario"), User(display_name="Altro")
        self.db.add_all([self.owner, self.other]); self.db.flush()
        conn = AuthConnection(user_id=self.owner.id, provider="strava", provider_user_id="owner", access_token="token")
        self.db.add(conn); self.db.flush()
        self.route = Route(user_id=self.owner.id, connection_id=conn.id, provider="strava", external_id="route", name="Percorso privato",
                           enrichment={"point_profile": {"points": [{"lat": 45, "lng": 9}, {"lat": 45, "lng": 9.001}]}})
        self.db.add(self.route); self.db.commit()
        self.request = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "session": {"csrf_token": "token"}})

    def tearDown(self):
        self.db.close(); self.engine.dispose()

    def action(self, action, admin=False, user=None, csrf="token"):
        with patch("app.main._is_admin", return_value=admin), patch("app.main._get_session_user", return_value=(user, object())), \
             patch("app.main._write_audit_log", side_effect=lambda db, *args, **kwargs: db.commit()):
            return route_publication(self.route.id, self.request, action, csrf, self.db)

    def visible(self):
        return list(self.db.scalars(select(Route.id).where(_visible_routes(self.other.id))))

    def test_owner_consent_shows_route_and_revocation_hides_it(self):
        self.assertEqual(self.visible(), [])
        self.action("consent", user=self.owner)
        self.assertEqual(self.visible(), [self.route.id])
        self.assertEqual(len(_dashboard_routes(self.db, user_id=self.other.id)), 1)
        self.action("withdraw", user=self.owner)
        self.assertEqual(self.visible(), [])
        self.assertEqual(_dashboard_routes(self.db, user_id=self.other.id), [])

    def test_other_user_and_invalid_csrf_cannot_change_publication(self):
        with self.assertRaises(HTTPException): self.action("consent", user=self.other)
        with self.assertRaises(HTTPException): self.action("approve", user=self.owner)
        with self.assertRaises(HTTPException): self.action("approve", admin=True, user=self.other)
        with self.assertRaises(HTTPException): self.action("approve", admin=True, csrf="wrong")
        self.assertFalse(self.route.publication_consent)
        self.assertFalse(self.route.publication_approved)

    def test_admin_owner_enables_and_disables_with_one_action(self):
        with patch("app.main._is_route_owner", return_value=True):
            self.action("approve", admin=True)
            self.assertTrue(self.route.publication_consent)
            self.assertEqual(self.visible(), [self.route.id])
            self.action("unapprove", admin=True)
            self.assertEqual(self.visible(), [])

    def test_legacy_admin_can_consent_only_for_its_own_imports(self):
        self.db.add(AuthConnection(user_id=self.owner.id, provider="manual", provider_user_id="local_admin", access_token="manual"))
        self.db.commit()
        with patch("app.main._get_local_admin", return_value={"username": ADMIN_USERNAME}), patch("app.main._current_local_user", return_value=None):
            self.assertTrue(_is_route_owner(self.request, self.route, self.db))
            self.route.user_id = self.other.id
            self.assertFalse(_is_route_owner(self.request, self.route, self.db))

    def test_preview_contains_only_own_or_published_routes(self):
        with patch("app.main._get_local_admin", return_value=None), patch("app.main._get_session_user", return_value=(self.other, object())):
            response = api_coverage_tracks(self.request, after_id=0, limit=20, db=self.db)
            self.assertEqual(json.loads(response.body)["loaded_routes"], 0)
            self.assertEqual(json.loads(api_coverage(self.request, db=self.db).body)["total_routes"], 0)
            self.route.publication_approved = self.route.publication_consent = True
            self.db.commit()
            response = api_coverage_tracks(self.request, after_id=0, limit=20, db=self.db)
            self.assertEqual(json.loads(response.body)["loaded_routes"], 1)
            self.assertEqual(json.loads(api_coverage(self.request, db=self.db).body)["total_routes"], 1)

    def test_bulk_publish_is_atomic_and_limited_to_admin_own_routes(self):
        other_conn = AuthConnection(user_id=self.other.id, provider="strava", provider_user_id="other", access_token="token")
        self.db.add(other_conn); self.db.flush()
        other_route = Route(user_id=self.other.id, connection_id=other_conn.id, provider="strava", external_id="other-route")
        self.db.add(other_route); self.db.commit()
        with patch("app.main._is_admin", return_value=True), patch("app.main._admin_owner_id", return_value=self.owner.id), \
             patch("app.main._write_audit_log", side_effect=lambda db, *args, **kwargs: db.commit()):
            self.request.scope['headers'].append((b'x-csrf-token', b'token'))
            with self.assertRaises(HTTPException):
                admin_publish_routes(self.request, [self.route.id, other_route.id], self.db)
            self.assertFalse(self.route.publication_approved)
            response = admin_publish_routes(self.request, [self.route.id], self.db)
            self.assertEqual(json.loads(response.body)["published"], 1)
            self.assertTrue(self.route.publication_approved and self.route.publication_consent)
            self.assertFalse(other_route.publication_approved)

    def test_official_review_is_admin_only_and_does_not_change_visibility(self):
        with patch("app.main._is_admin", return_value=False):
            with self.assertRaises(HTTPException):
                route_official_review(self.route.id, self.request, "approved", "token", self.db)
        with patch("app.main._is_admin", return_value=True):
            route_official_review(self.route.id, self.request, "approved", "token", self.db)
            candidate = self.db.scalars(select(OfficialSkateCandidate)).one()
            self.assertEqual(candidate.status, "approved")
            self.assertFalse(self.route.publication_consent)
            self.assertTrue(_dashboard_routes(self.db)[0]["official_reviewed"])
            route_official_review(self.route.id, self.request, "rejected", "token", self.db)
            self.assertEqual(candidate.status, "approved")
            self.assertEqual(len(list(self.db.scalars(select(OfficialSkateDecision)))), 1)


if __name__ == "__main__": unittest.main()
