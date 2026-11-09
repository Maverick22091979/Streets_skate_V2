"""Integra i percorsi locali nella copia Aruba, solo nel database di confronto."""
import sys
from collections import Counter, defaultdict

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session

from app.db import engine
from app.main import _is_same_route_candidate, _normalize_route_name, _route_points_from_enrichment
from app.models import AuthConnection, CalendarEvent, OfficialSkateCandidate, OfficialSkateDecision, OfficialSkateSegment, Route, User, VerifiedSurfaceArea


SOURCE_DB = "street_skate"
TARGET_DB = "street_skate_aruba_20261109"


def values_without_ids(row, *excluded):
    return {column.key: getattr(row, column.key) for column in inspect(type(row)).columns if column.key not in excluded}


def same_points(first, second):
    return [(p.get("lat"), p.get("lng"), p.get("time")) for p in _route_points_from_enrichment(first.enrichment)] == [
        (p.get("lat"), p.get("lng"), p.get("time")) for p in _route_points_from_enrichment(second.enrichment)
    ]


def merge(source, target):
    source.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
    target.execute(text("LOCK TABLE users, auth_connections, routes, calendar_events, official_skate_candidates IN SHARE ROW EXCLUSIVE MODE"))
    source_user, target_user = source.get(User, 1), target.get(User, 3)
    source_conn, target_conn = source.get(AuthConnection, 1), target.get(AuthConnection, 3)
    if not (source_user and target_user and source_conn and target_conn and
            source_user.display_name == target_user.display_name == "Admin locale" and
            source_conn.user_id == source_user.id and target_conn.user_id == target_user.id and
            source_conn.provider == target_conn.provider == "manual" and
            source_conn.provider_user_id == target_conn.provider_user_id == "local_admin"):
        raise ValueError("Account o collegamento manuale non corrispondenti; nessuna modifica effettuata")

    source_routes = list(source.scalars(select(Route)))
    target_routes = list(target.scalars(select(Route)))
    candidates = list(source.scalars(select(OfficialSkateCandidate)))
    if (len(source_routes), len(target_routes), len(candidates)) != (412, 318, 273):
        raise ValueError("Il numero dei dati è cambiato rispetto al confronto; ripetere la verifica")
    if any(r.user_id != source_user.id or r.connection_id != source_conn.id for r in source_routes):
        raise ValueError("I percorsi locali non appartengono tutti all'account manuale previsto")
    for model in (OfficialSkateDecision, OfficialSkateSegment):
        if source.scalars(select(model)).first() or target.scalars(select(model)).first():
            raise ValueError("Decisioni o segmenti presenti: serve un'integrazione dedicata")
    if target.scalars(select(OfficialSkateCandidate)).first():
        raise ValueError("La copia Aruba contiene già candidature; interrompo per evitare duplicati")

    fields = ("name", "surface", "polygon", "is_verified", "notes")
    local_areas = sorted([tuple(str(getattr(a, k)) for k in fields) for a in source.scalars(select(VerifiedSurfaceArea))])
    target_areas = sorted([tuple(str(getattr(a, k)) for k in fields) for a in target.scalars(select(VerifiedSurfaceArea))])
    if local_areas != target_areas:
        raise ValueError("Le aree verificate differiscono: serve una verifica manuale")

    target_events = {(e.calendar_id, e.external_id): e.id for e in target.scalars(select(CalendarEvent))}
    source_events = {e.id: (e.calendar_id, e.external_id) for e in source.scalars(select(CalendarEvent))}
    if set(source_events.values()) != set(target_events):
        raise ValueError("Gli eventi Calendar non coincidono: serve un'integrazione dedicata")

    by_name = defaultdict(list)
    by_external_id = {(r.provider, r.external_id): r for r in target_routes}
    for route in target_routes:
        by_name[_normalize_route_name(route.name)].append(route)
    route_map, used_target = {}, set()
    counts = Counter()
    candidate_route_ids = {c.route_id for c in candidates}
    for row in source_routes:
        existing = by_external_id.get((row.provider, row.external_id))
        candidate = {"name": row.name, "distance_m": row.distance_m, "start_date_local": row.start_date_local, "enrichment": row.enrichment}
        matches = [existing] if existing else [r for r in by_name[_normalize_route_name(row.name)] if _is_same_route_candidate(r, candidate)]
        if len(matches) > 1 or (matches and matches[0].id in used_target):
            raise ValueError("Corrispondenza ambigua tra percorsi: nessuna modifica effettuata")
        if matches:
            dest = matches[0]
            if dest.user_id != target_user.id or (row.id in candidate_route_ids and not same_points(row, dest)):
                raise ValueError("Proprietario o punti GPS non corrispondono: serve una verifica manuale")
            used_target.add(dest.id)
            counts["percorsi_gia_presenti"] += 1
        else:
            dest = Route(**values_without_ids(row, "id", "user_id", "connection_id"), user_id=target_user.id, connection_id=target_conn.id)
            target.add(dest)
            target.flush()
            by_external_id[(dest.provider, dest.external_id)] = dest
            by_name[_normalize_route_name(dest.name)].append(dest)
            counts["percorsi_aggiunti"] += 1
        route_map[row.id] = dest.id

    for row in candidates:
        if row.owner_user_id != source_user.id or row.route_id not in route_map:
            raise ValueError("Candidatura associata a un utente o percorso inatteso")
        event_id = target_events[source_events[row.calendar_event_id]] if row.calendar_event_id is not None else None
        dest = OfficialSkateCandidate(**values_without_ids(row, "id", "route_id", "owner_user_id", "calendar_event_id"),
                                      route_id=route_map[row.route_id], owner_user_id=target_user.id, calendar_event_id=event_id)
        target.add(dest)
        counts["candidature_aggiunte"] += 1
    target.flush()
    if counts != {"percorsi_gia_presenti": 266, "percorsi_aggiunti": 146, "candidature_aggiunte": 273}:
        raise ValueError(f"Risultato inatteso: {dict(counts)}; nessuna modifica effettuata")
    return counts


def main():
    if sys.argv[1:] not in ([], ["--apply"]):
        raise SystemExit("Uso: python scripts/merge_local_routes.py [--apply]")
    source_engine = create_engine(engine.url.set(database=SOURCE_DB))
    target_engine = create_engine(engine.url.set(database=TARGET_DB))
    with Session(source_engine) as source, Session(target_engine) as target:
        try:
            counts = merge(source, target)
            if sys.argv[1:] == ["--apply"]:
                target.commit()
                print("Integrazione completata sulla copia Aruba locale:", dict(counts))
            else:
                target.rollback()
                print("Simulazione completata; nessun dato salvato:", dict(counts))
        except Exception:
            target.rollback()
            raise
    source_engine.dispose()
    target_engine.dispose()


if __name__ == "__main__":
    main()
