import json
import logging
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

from sqlalchemy import func, select

from app.coverage import build_coverage, track_coords
from app.models import CoverageSnapshot, Route


def route_version(db):
    count, last_id, updated = db.execute(
        select(func.count(Route.id), func.max(Route.id), func.max(Route.updated_at))
    ).one()
    return json.dumps([2, count, last_id, str(updated)]), count


def route_geometry_query():
    return select(Route.id, Route.enrichment["point_profile"]["points"], Route.summary_polyline).order_by(Route.id)


class CoverageCache:
    def __init__(self, factory, decode):
        self.factory = factory
        self.decode = decode
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="coverage")
        self.lock = Lock()
        self.future = None

    def get(self, db):
        signature, count = route_version(db)
        cached = db.execute(select(CoverageSnapshot.payload).where(
            CoverageSnapshot.id == 1, CoverageSnapshot.signature == signature,
        )).scalar_one_or_none()
        if cached is not None:
            return cached, 200
        with self.lock:
            if self.future is not None and self.future.done():
                error = self.future.exception()
                self.future = None
                if error:
                    return {"detail": "Calcolo delle frequenze non disponibile. Riprova."}, 503
            if self.future is None:
                self.future = self.pool.submit(self._compute)
        return {"status": "processing", "total_routes": count, "retry_after": 2}, 202

    def warm(self):
        try:
            with self.factory() as db:
                self.get(db)
        except Exception:
            logging.getLogger(__name__).exception("Impossibile preparare la mappa frequenze")

    def _compute(self):
        try:
            for _ in range(3):
                with self.factory() as db:
                    signature, _ = route_version(db)
                    tracks = [track_coords(pts, line, self.decode) for _, pts, line in db.execute(route_geometry_query())]
                data = build_coverage(tracks)
                with self.factory() as db:
                    if route_version(db)[0] != signature:
                        continue
                    cached = db.get(CoverageSnapshot, 1)
                    if cached is None:
                        db.add(CoverageSnapshot(id=1, signature=signature, payload=data))
                    else:
                        cached.signature = signature
                        cached.payload = data
                    db.commit()
                return
        except Exception:
            logging.getLogger(__name__).exception("Calcolo delle frequenze fallito")
            raise
