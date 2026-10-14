import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.coverage import build_coverage
from app.coverage_store import CoverageCache
from app.db import Base
from app.models import Route


class CoverageCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self.tmp.name) / 'coverage.db'}")
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(self.engine)
        self.cache = CoverageCache(self.factory, lambda _: [])
        self.add_route(1)

    def tearDown(self):
        self.cache.pool.shutdown(wait=True)
        self.engine.dispose()
        self.tmp.cleanup()

    def add_route(self, rid):
        with self.factory() as db:
            db.add(Route(
                id=rid, user_id=1, connection_id=1, provider="manual", external_id=str(rid),
                enrichment={"point_profile": {"points": [{"lat": 45, "lng": 9}, {"lat": 45, "lng": 9.001}]}},
            ))
            db.commit()

    def get(self, cache=None):
        with self.factory() as db:
            return (cache or self.cache).get(db)

    def test_nonblocking_single_job_and_persistent_result(self):
        started, release = Event(), Event()

        def compute(tracks):
            started.set()
            if not release.wait(5):
                raise RuntimeError("Test did not release calculation")
            return build_coverage(tracks)

        with patch("app.coverage_store.build_coverage", side_effect=compute) as build:
            try:
                self.assertEqual(self.get()[1], 202)
                self.assertTrue(started.wait(3))
                future = self.cache.future
                self.assertEqual(self.get()[1], 202)
                self.assertIs(self.cache.future, future)
            finally:
                release.set()
            future.result(timeout=5)
            self.assertEqual(build.call_count, 1)
        data, status = self.get()
        self.assertEqual(status, 200)
        self.assertEqual(data["total_routes"], 1)
        fresh = CoverageCache(self.factory, lambda _: [])
        try:
            with patch("app.coverage_store.build_coverage") as build:
                self.assertEqual(self.get(fresh), (data, 200))
                build.assert_not_called()
        finally:
            fresh.pool.shutdown(wait=True)

    def test_import_and_delete_invalidate_result(self):
        self.get()
        self.cache.future.result(timeout=5)
        self.add_route(2)
        self.assertEqual(self.get()[1], 202)
        self.cache.future.result(timeout=5)
        self.assertEqual(self.get()[0]["total_routes"], 2)
        with self.factory() as db:
            db.delete(db.get(Route, 1))
            db.commit()
        self.assertEqual(self.get()[1], 202)
        self.cache.future.result(timeout=5)
        self.assertEqual(self.get()[0]["total_routes"], 1)

    def test_route_changes_during_calculation_are_reloaded(self):
        started, release = Event(), Event()
        calls = []

        def compute(tracks):
            calls.append(len(tracks))
            if len(calls) == 1:
                started.set()
                if not release.wait(5):
                    raise RuntimeError("Test did not release calculation")
            return build_coverage(tracks)

        with patch("app.coverage_store.build_coverage", side_effect=compute):
            try:
                self.get()
                self.assertTrue(started.wait(3))
                self.add_route(2)
            finally:
                release.set()
            self.cache.future.result(timeout=5)
        self.assertEqual(calls, [1, 2])
        self.assertEqual(self.get()[0]["total_routes"], 2)

    def test_failed_calculation_can_be_retried(self):
        with self.assertLogs("app.coverage_store", level="ERROR"), \
             patch("app.coverage_store.build_coverage", side_effect=RuntimeError("failed")):
            self.get()
            with self.assertRaises(RuntimeError):
                self.cache.future.result(timeout=5)
        self.assertEqual(self.get()[1], 503)
        self.assertEqual(self.get()[1], 202)
        self.cache.future.result(timeout=5)
        self.assertEqual(self.get()[1], 200)
