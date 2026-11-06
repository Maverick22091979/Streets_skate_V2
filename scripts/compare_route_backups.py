"""Confronta due database di backup senza modificare dati o stampare coordinate."""
import argparse
from collections import Counter, defaultdict

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db import engine
from app.main import _is_same_route_candidate, _normalize_route_name
from app.models import Route


def load_routes(database):
    audit_engine = create_engine(engine.url.set(database=database), pool_pre_ping=True)
    try:
        with Session(audit_engine) as session:
            return list(session.scalars(select(Route)))
    finally:
        audit_engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("production_database")
    parser.add_argument("local_database")
    args = parser.parse_args()
    production = load_routes(args.production_database)
    local = load_routes(args.local_database)
    by_name = defaultdict(list)
    by_external_id = defaultdict(list)
    for route in production:
        by_name[_normalize_route_name(route.name)].append(route)
        by_external_id[(route.provider, route.external_id)].append(route)

    counts = Counter()
    matched_production = set()
    for route in local:
        exact = by_external_id[(route.provider, route.external_id)]
        if exact:
            matches = exact
            counts["same_external_id"] += 1
        else:
            candidate = {"name": route.name, "distance_m": route.distance_m,
                         "start_date_local": route.start_date_local, "enrichment": route.enrichment}
            matches = [other for other in by_name[_normalize_route_name(route.name)]
                       if _is_same_route_candidate(other, candidate)]
            if len(matches) == 1:
                counts["application_match"] += 1
            elif matches:
                counts["ambiguous"] += 1
            else:
                counts["no_application_match"] += 1
        if len(matches) == 1:
            if matches[0].id in matched_production:
                counts["production_reused"] += 1
            matched_production.add(matches[0].id)

    print(f"Percorsi produzione: {len(production)}")
    print(f"Percorsi backup locale: {len(local)}")
    for key, label in [("same_external_id", "Stesso identificativo"),
                       ("application_match", "Corrispondenza secondo l'app"),
                       ("ambiguous", "Più corrispondenze"),
                       ("no_application_match", "Senza corrispondenza secondo l'app"),
                       ("production_reused", "Corrispondenze multiple sullo stesso percorso online")]:
        print(f"{label}: {counts[key]}")


if __name__ == "__main__":
    main()
