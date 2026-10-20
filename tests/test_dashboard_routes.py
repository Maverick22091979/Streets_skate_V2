import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import _dashboard_routes, app
from app.models import AuthConnection, Route, User


class DashboardRoutesTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.user = User(display_name="Pattinatore")
        self.db.add(self.user)
        self.db.flush()
        self.conn = AuthConnection(user_id=self.user.id, provider="manual", provider_user_id="admin", access_token="test")
        self.db.add(self.conn)
        self.db.flush()
        for i in range(1, 4):
            self.db.add(Route(
                user_id=self.user.id, connection_id=self.conn.id, provider="manual", external_id=str(i),
                name=f"Percorso {i}", start_date_local=f"2026-10-0{i}",
                raw_payload={"filename": f"percorso-{i}.gpx", "large": "x" * 100000},
                enrichment={"point_profile": {"points": [{"lat": 45, "lng": 9}] * 10000},
                            "segment_speed_index": {"large": "x" * 100000}, "surface_samples": [1] * 10000,
                            "has_cobblestone": False, "track_quality": {"status": "warning"}},
                difficulty={"score": i * 10},
            ))
        self.db.commit()
        app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()
        self.engine.dispose()

    def test_metadata_preserves_cards_without_loading_track_arrays(self):
        rows = _dashboard_routes(self.db)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["name"], "Percorso 3")
        self.assertEqual(rows[0]["import_user_label"], "Pattinatore")
        self.assertEqual(rows[0]["source_label"], "Import manuale GPX/JSON")
        self.assertEqual(rows[0]["enrichment"]["track_quality"]["status"], "warning")
        self.assertNotIn("point_profile", rows[0]["enrichment"])
        self.assertNotIn("segment_speed_index", rows[0]["enrichment"])
        self.assertNotIn("surface_samples", rows[0]["enrichment"])
        self.assertNotIn("raw_payload", rows[0])

    def test_api_pages_cover_entire_archive_and_legacy_request_returns_all(self):
        with patch("app.main._get_local_admin", return_value={"label": "Admin"}):
            first = self.client.get("/api/routes?after_id=0&limit=2")
            self.assertEqual(first.status_code, 200)
            data = first.json()
            self.assertEqual([r["id"] for r in data["routes"]], [1, 2])
            self.assertTrue(data["has_more"])
            self.assertTrue(data["users"])
            self.assertLess(len(first.content), 10000)
            second = self.client.get(f"/api/routes?after_id={data['next_after']}&limit=2").json()
            self.assertEqual([r["id"] for r in second["routes"]], [3])
            self.assertFalse(second["has_more"])
            self.assertEqual(len(self.client.get("/api/routes").json()["routes"]), 3)
            self.assertEqual(self.client.get("/api/routes?limit=0").status_code, 422)

    def test_api_requires_authenticated_session(self):
        self.assertEqual(self.client.get("/api/routes?limit=25").status_code, 401)

    def test_postgres_projection_removes_heavy_json_keys_in_database(self):
        db = Mock()
        db.get_bind.return_value.dialect.name = "postgresql"
        db.execute.return_value.mappings.return_value = []
        _dashboard_routes(db, limit=25)
        sql = str(db.execute.call_args.args[0].compile(dialect=postgresql.dialect()))
        self.assertIn("CAST(routes.enrichment AS JSONB)", sql)
        self.assertIn("- 'point_profile'", sql)
        self.assertIn("- 'segment_speed_index'", sql)
