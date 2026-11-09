import unittest
from types import SimpleNamespace

from app.models import Route
from scripts.merge_local_routes import same_points, values_without_ids


class MergeLocalRoutesTests(unittest.TestCase):
    def test_same_points_requires_identical_order_and_time(self):
        def route(points):
            return SimpleNamespace(enrichment={"point_profile": {"points": points}})

        point = {"lat": 41.9, "lng": 12.5, "time": "2020-01-01T12:00:00Z"}
        self.assertTrue(same_points(route([point]), route([dict(point)])))
        self.assertFalse(same_points(route([point]), route([dict(point, time="2020-01-01T12:00:01Z")])))

    def test_copy_does_not_reuse_database_or_owner_ids(self):
        row = Route(id=12, user_id=1, connection_id=1, provider="manual", external_id="abc")
        data = values_without_ids(row, "id", "user_id", "connection_id")
        self.assertNotIn("id", data)
        self.assertNotIn("user_id", data)
        self.assertNotIn("connection_id", data)
        self.assertEqual(data["external_id"], "abc")


if __name__ == "__main__":
    unittest.main()
