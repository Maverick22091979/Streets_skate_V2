import asyncio
import math
import secrets
import json
import time
import uuid
import ipaddress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import quote, urlparse
from urllib.request import urlopen

from fastapi import Body, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from app.config import env_bool, env_float, env_int, env_value
from app.db import Base, SessionLocal, engine, get_db
from app.gpx_import import _declared_difficulty_from_filename, parse_gpx_bytes
from app.models import AuditLog, AuthConnection, Route, User
from app.osm import fetch_surface_profile
from app.providers import ProviderAdapter, build_provider_registry
from app.slope import smooth_elevations, trend_metrics
from app.weather import fetch_weather_snapshot

APP_NAME = env_value("APP_NAME", "Street Skate Login")
APP_ENV = env_value("APP_ENV", "development").strip().lower()
APP_SECRET_KEY = env_value("APP_SECRET_KEY", "") or secrets.token_urlsafe(48)
ASSET_VERSION = env_value("ASSET_VERSION", datetime.now(UTC).strftime("%Y%m%d%H%M%S"))
BASE_URL = env_value("BASE_URL", "http://localhost:5000")
ADMIN_USERNAME = env_value("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = env_value("ADMIN_PASSWORD", "")
SESSION_COOKIE_NAME = env_value("SESSION_COOKIE_NAME", "street_skate_session")
SESSION_SAME_SITE = env_value("SESSION_SAME_SITE", "lax").strip().lower() or "lax"
SESSION_COOKIE_SECURE = env_bool("SESSION_COOKIE_SECURE", APP_ENV == "production")
SESSION_COOKIE_BROWSER_CLOSE = env_bool("SESSION_COOKIE_BROWSER_CLOSE", True)
SESSION_COOKIE_MAX_AGE_SEC = env_int("SESSION_COOKIE_MAX_AGE_SEC", 1209600)
SESSION_INACTIVITY_TIMEOUT_SEC = env_int("SESSION_INACTIVITY_TIMEOUT_SEC", 1800)
SECURITY_ENABLE_HSTS = env_bool("SECURITY_ENABLE_HSTS", APP_ENV == "production")
SECURITY_HSTS_MAX_AGE = env_int("SECURITY_HSTS_MAX_AGE", 31536000)
OVERPASS_API_URL = env_value("OVERPASS_API_URL", "https://overpass-api.de/api/interpreter")
OPEN_METEO_ARCHIVE_URL = env_value("OPEN_METEO_ARCHIVE_URL", "https://archive-api.open-meteo.com/v1/archive")
OPEN_METEO_FORECAST_URL = env_value("OPEN_METEO_FORECAST_URL", "https://api.open-meteo.com/v1/forecast")
IP_GEOLOOKUP_URL = env_value("IP_GEOLOOKUP_URL", "https://ipwho.is/{ip}")
IP_GEOLOOKUP_TIMEOUT_SEC = env_float("IP_GEOLOOKUP_TIMEOUT_SEC", 3)
ADIDAS_EXPORT_DIR = env_value("ADIDAS_EXPORT_DIR", "data/export/Sport-sessions/GPS-data")
ADIDAS_EXPORT_USER_DIR = env_value("ADIDAS_EXPORT_USER_DIR", "data/export/User")
MIN_IMPORT_DISTANCE_M = env_float("MIN_IMPORT_DISTANCE_M", 500)
SLOPE_SETTINGS = {
    "smoothing_window": env_int("SLOPE_SMOOTHING_WINDOW", 5),
    "min_run_distance_m": env_float("SLOPE_MIN_RUN_DISTANCE_M", 20),
    "max_cap_pct": env_float("SLOPE_MAX_CAP_PCT", 35),
}

app = FastAPI(title=APP_NAME)
app.add_middleware(
    SessionMiddleware,
    secret_key=APP_SECRET_KEY,
    session_cookie=SESSION_COOKIE_NAME,
    max_age=None if SESSION_COOKIE_BROWSER_CLOSE else SESSION_COOKIE_MAX_AGE_SEC,
    same_site=SESSION_SAME_SITE,
    https_only=SESSION_COOKIE_SECURE,
)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

PROVIDERS = build_provider_registry(BASE_URL)
BASE_SKATE_WEIGHTS = {
    "max_slope": env_int("DIFFICULTY_WEIGHT_MAX_SLOPE", 25),
    "weighted_slope": env_int("DIFFICULTY_WEIGHT_WEIGHTED_SLOPE", 25),
    "surface": env_int("DIFFICULTY_WEIGHT_SURFACE", 15),
    "smoothness": env_int("DIFFICULTY_WEIGHT_SMOOTHNESS", 0),
    "pressure": env_int("DIFFICULTY_WEIGHT_PRESSURE", 5),
    "temperature": env_int("DIFFICULTY_WEIGHT_TEMPERATURE", 20),
    "segment_speed": env_int("DIFFICULTY_WEIGHT_SEGMENT_SPEED", 10),
}
DUPLICATE_SETTINGS = {
    "distance_diff_pct": env_float("DUPLICATE_DISTANCE_DIFF_PCT", 5),
    "endpoint_tolerance_m": env_float("DUPLICATE_ENDPOINT_TOLERANCE_M", 300),
    "allow_reverse_match": env_bool("DUPLICATE_ALLOW_REVERSE_MATCH", True),
}
VIEWER_SETTINGS = {
    "show_direction_arrows": env_bool("VIEWER_SHOW_DIRECTION_ARROWS", True),
}
DEFAULT_PROVIDER_VISIBILITY = {
    "adidas": False,
    "mapmyrun": False,
    "runkeeper": False,
    "garmin": False,
    "inline_route_tracking": False,
}
DEFAULT_PROVIDER_ENABLED = {
    "adidas": False,
    "mapmyrun": False,
}
PROVIDER_BUTTON_SETTINGS: dict[str, dict[str, bool]] = {
    key: {
        "visible": env_bool(f"{key.upper()}_LOGIN_VISIBLE", DEFAULT_PROVIDER_VISIBILITY.get(key, True)),
        "enabled": env_bool(f"{key.upper()}_LOGIN_ENABLED", DEFAULT_PROVIDER_ENABLED.get(key, PROVIDERS[key].cfg.configured)),
    }
    for key in PROVIDERS
}
IMPORT_JOBS: dict[str, dict[str, Any]] = {}
FAILED_ADMIN_LOGINS: dict[str, dict[str, Any]] = {}
IP_GEO_CACHE: dict[str, dict[str, Any]] = {}
ALLOWED_GPX_SPORT_TYPES = {
    "skating",
    "inline skating",
    "inline_skating",
    "inline-skating",
    "roller skating",
    "roller_skating",
    "roller-skating",
    "rollerblading",
    "inlineskating",
}
ROUTE_TYPE_OPTIONS = [
    "skating",
    "inline skating",
    "roller skating",
    "cycling",
    "running",
    "walking",
    "hiking",
    "mountain biking",
    "e-bike",
    "workout",
    "other",
]

ADMIN_LOGIN_WINDOW_SEC = env_int("ADMIN_LOGIN_WINDOW_SEC", 900)
ADMIN_LOGIN_MAX_ATTEMPTS = env_int("ADMIN_LOGIN_MAX_ATTEMPTS", 5)
ADMIN_LOGIN_LOCK_SEC = env_int("ADMIN_LOGIN_LOCK_SEC", 900)

SECURITY_HEADERS = {
    "Content-Security-Policy": "; ".join(
        [
            "default-src 'self'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'none'",
            "object-src 'none'",
            "script-src 'self' https://unpkg.com",
            "style-src 'self' 'unsafe-inline' https://unpkg.com",
            "img-src 'self' data: https://tile.openstreetmap.org https://*.tile.openstreetmap.org",
            "font-src 'self' data:",
            "connect-src 'self'",
            "manifest-src 'self'",
            "worker-src 'self' blob:",
        ]
    ),
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "accelerometer=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), microphone=(), payment=(), usb=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}


@app.on_event("startup")
def startup() -> None:
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE routes ADD COLUMN IF NOT EXISTS import_user_label VARCHAR(255)"))
        conn.execute(text("ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS request_ip VARCHAR(64)"))
        conn.execute(text("ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS request_method VARCHAR(16)"))
        conn.execute(text("ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS request_path VARCHAR(1024)"))
        conn.execute(text("ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS request_user_agent TEXT"))
        conn.execute(text("ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS request_referer TEXT"))
        conn.execute(text("ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS request_origin TEXT"))
        conn.execute(text("ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS request_forwarded_for TEXT"))
        conn.execute(text("ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS geo_country VARCHAR(128)"))
        conn.execute(text("ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS geo_region VARCHAR(128)"))
        conn.execute(text("ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS geo_city VARCHAR(128)"))
        conn.execute(text("ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS geo_latitude DOUBLE PRECISION"))
        conn.execute(text("ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS geo_longitude DOUBLE PRECISION"))
        conn.execute(text("ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS geo_org VARCHAR(255)"))
        conn.execute(text("ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS geo_asn VARCHAR(64)"))


@app.middleware("http")
async def apply_security_headers(request: Request, call_next):
    response = await call_next(request)
    for key, value in SECURITY_HEADERS.items():
        response.headers.setdefault(key, value)
    if SECURITY_ENABLE_HSTS:
        response.headers.setdefault("Strict-Transport-Security", f"max-age={SECURITY_HSTS_MAX_AGE}; includeSubDomains")
    return response


def _fmt_audit_ts(dt: datetime | None = None) -> str:
    cur = dt or datetime.now().astimezone()
    return cur.strftime("%d/%m/%Y - %H:%M")


def _ensure_csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return str(token)


def _csrf_ctx(request: Request) -> dict[str, str]:
    return {"csrf_token": _ensure_csrf_token(request)}


def _session_store(request: Request) -> dict[str, Any]:
    raw = request.scope.get("session")
    return raw if isinstance(raw, dict) else {}


def _has_authenticated_session(request: Request) -> bool:
    session = _session_store(request)
    return bool(session.get("local_admin") or (session.get("user_id") and session.get("connection_id")))


def _start_authenticated_session(request: Request) -> None:
    now = time.time()
    request.session["session_started_at"] = now
    request.session["session_last_seen_at"] = now


def _session_timeout_response(request: Request, detail: str):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": detail}, status_code=401)
    return RedirectResponse(f"/?error={quote(detail)}", status_code=303)


def _log_session_timeout(request: Request, reason: str) -> None:
    db = None
    try:
        db = SessionLocal()
        _write_audit_log(
            db,
            request,
            "session_timeout",
            error=reason,
            meta={"timeout_sec": SESSION_INACTIVITY_TIMEOUT_SEC, "path": str(request.url.path)},
        )
    except Exception:
        pass
    finally:
        try:
            if db is not None:
                db.close()
        except Exception:
            pass


def _touch_or_expire_session(request: Request) -> bool:
    session = _session_store(request)
    has_auth = bool(session.get("local_admin") or (session.get("user_id") and session.get("connection_id")))
    if not has_auth:
        return False
    now = time.time()
    started_raw = session.get("session_started_at")
    last_seen_raw = session.get("session_last_seen_at")
    try:
        started_at = float(started_raw)
        last_seen_at = float(last_seen_raw)
    except (TypeError, ValueError):
        reason = "Sessione scaduta. Esegui di nuovo il login."
        _log_session_timeout(request, reason)
        request.session.clear()
        return False
    if SESSION_INACTIVITY_TIMEOUT_SEC > 0 and now - last_seen_at > SESSION_INACTIVITY_TIMEOUT_SEC:
        reason = "Sessione scaduta per inattività. Esegui di nuovo il login."
        _log_session_timeout(request, reason)
        request.session.clear()
        return False
    request.session["session_started_at"] = started_at
    request.session["session_last_seen_at"] = now
    return True


def _get_local_admin(request: Request) -> dict[str, Any] | None:
    if not _touch_or_expire_session(request):
        return None
    local_admin = request.session.get("local_admin")
    return local_admin if isinstance(local_admin, dict) else None


def _is_same_origin_request(request: Request) -> bool:
    target = request.url
    for header_name in ("origin", "referer"):
        raw = request.headers.get(header_name)
        if not raw:
            continue
        try:
            src = urlparse(raw)
        except Exception:
            continue
        if not src.scheme or not src.netloc:
            continue
        return (
            src.scheme.lower() == (target.scheme or "").lower()
            and src.netloc.lower() == (target.netloc or "").lower()
        )
    return False


def _require_csrf(request: Request, token: str | None = None) -> None:
    expected = str(request.session.get("csrf_token") or "")
    provided = token or request.headers.get("x-csrf-token") or request.headers.get("x-xsrf-token") or ""
    if expected and provided and secrets.compare_digest(str(provided), expected):
        return
    if _is_same_origin_request(request):
        return
    if not expected or not provided or not secrets.compare_digest(str(provided), expected):
        raise HTTPException(status_code=403, detail="CSRF token non valido")


def _admin_login_key(request: Request, username: str) -> str:
    ip = (request.client.host if request.client else "") or "unknown"
    return f"{ip}:{(username or '').strip().lower()}"


def _admin_login_state(request: Request, username: str) -> tuple[bool, int]:
    now = time.time()
    key = _admin_login_key(request, username)
    row = FAILED_ADMIN_LOGINS.get(key)
    if not row:
        return False, 0
    locked_until = float(row.get("locked_until") or 0)
    if locked_until > now:
        return True, max(1, int(locked_until - now))
    attempts = [ts for ts in row.get("attempts", []) if now - float(ts) <= ADMIN_LOGIN_WINDOW_SEC]
    if attempts:
        row["attempts"] = attempts
        row["locked_until"] = 0
        FAILED_ADMIN_LOGINS[key] = row
    else:
        FAILED_ADMIN_LOGINS.pop(key, None)
    return False, 0


def _register_admin_login_failure(request: Request, username: str) -> int:
    now = time.time()
    key = _admin_login_key(request, username)
    row = FAILED_ADMIN_LOGINS.get(key) or {"attempts": [], "locked_until": 0}
    attempts = [ts for ts in row.get("attempts", []) if now - float(ts) <= ADMIN_LOGIN_WINDOW_SEC]
    attempts.append(now)
    locked_until = 0
    if len(attempts) >= ADMIN_LOGIN_MAX_ATTEMPTS:
        locked_until = now + ADMIN_LOGIN_LOCK_SEC
    FAILED_ADMIN_LOGINS[key] = {"attempts": attempts, "locked_until": locked_until}
    return max(1, int(locked_until - now)) if locked_until else 0


def _clear_admin_login_failures(request: Request, username: str) -> None:
    FAILED_ADMIN_LOGINS.pop(_admin_login_key(request, username), None)


def _actor_info(request: Request, db: Session) -> dict[str, str]:
    local_admin = _get_local_admin(request)
    if local_admin:
        name = local_admin.get("label") or "Admin"
        email = local_admin.get("username") or ADMIN_USERNAME
        return {
            "role": "Admin",
            "name": name,
            "email": email,
            "label": f"Admin | {name} | {email}",
        }
    user, conn = _get_session_user(request, db)
    profile = conn.raw_profile if conn and isinstance(conn.raw_profile, dict) else {}
    name = (
        user.display_name
        or profile.get("name")
        or " ".join(v for v in [profile.get("firstname"), profile.get("lastname")] if v).strip()
        or profile.get("display_name")
        or conn.label
        or "Utente"
    )
    email = (
        profile.get("email")
        or profile.get("Email")
        or profile.get("user_email")
        or profile.get("account_email")
        or conn.provider_username
        or "n.d."
    )
    return {
        "role": "Utente loggato",
        "name": name,
        "email": email,
        "label": f"Utente loggato | {name} | {email}",
    }


def _extract_request_ip(request: Request | None) -> tuple[str | None, str | None]:
    if not request:
        return None, None
    raw_forwarded = (request.headers.get("x-forwarded-for") or "").strip()
    if raw_forwarded:
        first = raw_forwarded.split(",")[0].strip()
        if first:
            return first, raw_forwarded
    client_host = request.client.host if request.client else None
    return (client_host or None), raw_forwarded or None


def _is_ip_lookup_candidate(value: str | None) -> bool:
    if not value:
        return False
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    return not (ip.is_loopback or ip.is_unspecified or ip.is_multicast)


def _fetch_ip_geodata(ip: str | None) -> dict[str, Any]:
    if not _is_ip_lookup_candidate(ip):
        return {}
    if ip in IP_GEO_CACHE:
        return IP_GEO_CACHE[ip]
    url = IP_GEOLOOKUP_URL.format(ip=ip)
    try:
        with urlopen(url, timeout=IP_GEOLOOKUP_TIMEOUT_SEC) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        raw = json.loads(body)
    except (URLError, TimeoutError, json.JSONDecodeError, OSError):
        IP_GEO_CACHE[ip] = {}
        return {}
    ok = bool(raw.get("success", True)) and not raw.get("error")
    if not ok:
        IP_GEO_CACHE[ip] = {}
        return {}
    asn = raw.get("connection", {}).get("asn") or raw.get("asn")
    org = raw.get("connection", {}).get("org") or raw.get("org") or raw.get("isp")
    out = {
        "country": raw.get("country") or raw.get("country_name"),
        "region": raw.get("region") or raw.get("region_name"),
        "city": raw.get("city"),
        "latitude": raw.get("latitude") or raw.get("lat"),
        "longitude": raw.get("longitude") or raw.get("lon"),
        "org": org,
        "asn": str(asn) if asn not in (None, "") else None,
    }
    IP_GEO_CACHE[ip] = out
    return out


def _request_audit_context(request: Request | None) -> dict[str, Any]:
    if not request:
        return {}
    ip, raw_forwarded = _extract_request_ip(request)
    geo = _fetch_ip_geodata(ip)
    return {
        "request_ip": ip,
        "request_method": request.method,
        "request_path": str(request.url.path),
        "request_user_agent": request.headers.get("user-agent"),
        "request_referer": request.headers.get("referer"),
        "request_origin": request.headers.get("origin"),
        "request_forwarded_for": raw_forwarded,
        "geo_country": geo.get("country"),
        "geo_region": geo.get("region"),
        "geo_city": geo.get("city"),
        "geo_latitude": geo.get("latitude"),
        "geo_longitude": geo.get("longitude"),
        "geo_org": geo.get("org"),
        "geo_asn": geo.get("asn"),
    }


def _write_audit_log(
    db: Session,
    request: Request,
    action: str,
    error: str | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    actor = _actor_info(request, db)
    req_ctx = _request_audit_context(request)
    row = AuditLog(
        action=action,
        actor_role=actor["role"],
        actor_name=actor["name"],
        actor_email=actor["email"],
        actor_label=actor["label"],
        occurred_at_label=_fmt_audit_ts(),
        request_ip=req_ctx.get("request_ip"),
        request_method=req_ctx.get("request_method"),
        request_path=req_ctx.get("request_path"),
        request_user_agent=req_ctx.get("request_user_agent"),
        request_referer=req_ctx.get("request_referer"),
        request_origin=req_ctx.get("request_origin"),
        request_forwarded_for=req_ctx.get("request_forwarded_for"),
        geo_country=req_ctx.get("geo_country"),
        geo_region=req_ctx.get("geo_region"),
        geo_city=req_ctx.get("geo_city"),
        geo_latitude=req_ctx.get("geo_latitude"),
        geo_longitude=req_ctx.get("geo_longitude"),
        geo_org=req_ctx.get("geo_org"),
        geo_asn=req_ctx.get("geo_asn"),
        error=error,
        meta=meta or None,
    )
    db.add(row)
    db.commit()


def _write_audit_log_for_actor(
    db: Session,
    action: str,
    actor_role: str,
    actor_name: str | None,
    actor_email: str | None,
    error: str | None = None,
    meta: dict[str, Any] | None = None,
    request: Request | None = None,
) -> None:
    req_ctx = _request_audit_context(request)
    row = AuditLog(
        action=action,
        actor_role=actor_role,
        actor_name=actor_name,
        actor_email=actor_email,
        actor_label=f"{actor_role} | {actor_name or 'n.d.'} | {actor_email or 'n.d.'}",
        occurred_at_label=_fmt_audit_ts(),
        request_ip=req_ctx.get("request_ip"),
        request_method=req_ctx.get("request_method"),
        request_path=req_ctx.get("request_path"),
        request_user_agent=req_ctx.get("request_user_agent"),
        request_referer=req_ctx.get("request_referer"),
        request_origin=req_ctx.get("request_origin"),
        request_forwarded_for=req_ctx.get("request_forwarded_for"),
        geo_country=req_ctx.get("geo_country"),
        geo_region=req_ctx.get("geo_region"),
        geo_city=req_ctx.get("geo_city"),
        geo_latitude=req_ctx.get("geo_latitude"),
        geo_longitude=req_ctx.get("geo_longitude"),
        geo_org=req_ctx.get("geo_org"),
        geo_asn=req_ctx.get("geo_asn"),
        error=error,
        meta=meta or None,
    )
    db.add(row)
    db.commit()


def _is_allowed_gpx_sport_type(value: str | None) -> bool:
    if not value:
        return False
    return value.strip().lower() in ALLOWED_GPX_SPORT_TYPES


def _serialize_audit_log(row: AuditLog) -> dict[str, Any]:
    return {
        "id": row.id,
        "action": row.action,
        "actor_role": row.actor_role,
        "actor_name": row.actor_name,
        "actor_email": row.actor_email,
        "actor_label": row.actor_label,
        "occurred_at": row.occurred_at.isoformat() if row.occurred_at else None,
        "occurred_at_label": row.occurred_at_label,
        "request_ip": row.request_ip,
        "request_method": row.request_method,
        "request_path": row.request_path,
        "request_user_agent": row.request_user_agent,
        "request_referer": row.request_referer,
        "request_origin": row.request_origin,
        "request_forwarded_for": row.request_forwarded_for,
        "geo_country": row.geo_country,
        "geo_region": row.geo_region,
        "geo_city": row.geo_city,
        "geo_latitude": row.geo_latitude,
        "geo_longitude": row.geo_longitude,
        "geo_org": row.geo_org,
        "geo_asn": row.geo_asn,
        "error": row.error,
        "meta": row.meta,
    }


def _audit_meta_text(meta: dict | list | None) -> str:
    if meta is None:
        return "n.d."
    try:
        return json.dumps(meta, ensure_ascii=False, indent=2)
    except TypeError:
        return str(meta)


def _request_debug_payload(request: Request) -> dict[str, Any]:
    keys = [
        "host",
        "x-forwarded-for",
        "x-forwarded-proto",
        "x-forwarded-host",
        "forwarded",
        "cf-connecting-ip",
        "x-real-ip",
        "user-agent",
        "referer",
        "origin",
    ]
    headers = {key: request.headers.get(key) or "" for key in keys}
    return {
        "client_host": request.client.host if request.client else None,
        "client_port": request.client.port if request.client else None,
        "method": request.method,
        "url": str(request.url),
        "base_url": str(request.base_url),
        "headers": headers,
        "all_headers": dict(request.headers.items()),
    }


def _route_type_choices(current: str | None) -> list[str]:
    vals: list[str] = []
    cur = (current or "").strip()
    if cur:
        vals.append(cur)
    for item in ROUTE_TYPE_OPTIONS:
        if item not in vals:
            vals.append(item)
    return vals


def _route_storage_source(route: Route) -> dict[str, str]:
    token_type = (route.connection.token_type or "").strip().lower() if route.connection else ""
    provider = (route.provider or "").strip().lower()
    if provider == "manual":
        return {"kind": "local", "label": "DB locale", "detail": "Import manuale GPX/JSON"}
    if provider == "adidas" and token_type == "export":
        return {"kind": "local", "label": "DB locale", "detail": "Export locale adidas"}
    return {"kind": "live", "label": "DB live", "detail": f"OAuth/API {route.provider}"}


def _user_import_consent_state(request: Request) -> str:
    return str(request.session.get("import_consent") or "pending")


def _is_import_allowed(request: Request) -> bool:
    return _user_import_consent_state(request) == "accepted"


def _provider_import_profile(provider: str, token_type: str | None = None) -> dict[str, Any]:
    baseline_fields = [
        "external_id",
        "name",
        "sport_type",
        "start_date_local",
        "distance_m",
        "elevation_gain_m",
        "average_speed_ms",
        "moving_time_sec",
        "summary_polyline_o_points",
        "raw_payload",
    ]
    common = {
        "adidas": {
            "summary": "Il modello dati di riferimento Street Skate usa la struttura adidas Running come baseline logica di import.",
            "importable_fields": [
                "id/activity_id -> external_id",
                "name/title -> name",
                "sport/type -> sport_type",
                "start_date_local/start_time -> start_date_local",
                "distance/distance_m -> distance_m",
                "elevation_gain/elevation_gain_m -> elevation_gain_m",
                "average_speed/avg_speed -> average_speed_ms",
                "moving_time/duration -> moving_time_sec",
                "summary_polyline oppure punti GPS/GPX",
                "raw_payload completo",
            ],
            "differences": [],
            "missing_fields": [],
            "warning": (
                "In modalità export locale vengono importati anche punti GPX e, se presenti nel sidecar JSON, misure aggiuntive come velocità puntuale, distanza cumulata, quota e classificazione iniziale."
                if token_type == "export"
                else None
            ),
        },
        "strava": {
            "summary": "Strava è compatibile con quasi tutti i campi base adidas, ma nel flusso attuale usa l'activity summary API.",
            "importable_fields": [
                "id -> external_id",
                "name -> name",
                "type/sport_type -> sport_type",
                "start_date_local -> start_date_local",
                "distance -> distance_m",
                "total_elevation_gain -> elevation_gain_m",
                "average_speed -> average_speed_ms",
                "moving_time -> moving_time_sec",
                "map.summary_polyline -> summary_polyline",
                "raw activity payload -> raw_payload",
            ],
            "differences": [
                {"field": "GPS dettaglio", "adidas": "GPX/punti o polyline", "provider": "nel flusso attuale arriva solo summary_polyline"},
                {"field": "Nome tipo sport", "adidas": "sport/type", "provider": "type oppure sport_type Strava"},
            ],
            "missing_fields": [
                "Nessun sidecar locale con quota puntuale, distanza cumulata o velocità puntuale nel flusso OAuth attuale.",
                "Nessuna classificazione iniziale da nome file perché l'import non parte da file locali.",
            ],
            "warning": "Se alcuni campi adidas non esistono in Strava, Street Skate importa solo il sottoinsieme compatibile e segnala implicitamente i mancanti come non disponibili.",
        },
        "mapmyrun": {
            "summary": "MapMyRun usa una struttura differente basata su workout, aggregates e time_series.",
            "importable_fields": [
                "workout id/self link -> external_id",
                "name -> name",
                "activity_type -> sport_type",
                "start_datetime -> start_date_local",
                "aggregates.distance_total -> distance_m",
                "aggregates.elevation_gain_total -> elevation_gain_m",
                "aggregates.speed_avg -> average_speed_ms",
                "aggregates.active_time_total -> moving_time_sec",
                "time_series.position -> punti GPS",
                "raw workout payload -> raw_payload",
            ],
            "differences": [
                {"field": "Geometria percorso", "adidas": "summary_polyline o GPX", "provider": "time_series.position con punti GPS"},
                {"field": "Metriche aggregate", "adidas": "campi top-level activity", "provider": "sotto aggregates"},
            ],
            "missing_fields": [
                "Nessuna summary_polyline nativa nel flusso attuale.",
                "Nessun sidecar adidas con dati locali aggiuntivi.",
                "Nessuna classificazione iniziale da nome file.",
            ],
            "warning": "La struttura dati è diversa da adidas: Street Skate converte i campi compatibili e usa i punti GPS MapMyRun al posto della polyline quando necessario.",
        },
        "suunto": {
            "summary": "Suunto Cloud API è disponibile tramite Partner Program ufficiale e usa OAuth browser-based piu una subscription key per chiamare le API cloud. La richiesta partnership per Street Skate risulta inviata il 19 agosto 2026 ed e in review fino a circa il 2 settembre 2026.",
            "importable_fields": [
                "JWT user/sub claim -> identita utente collegata",
                "id/workoutId/activityId -> external_id",
                "description/name/title -> name",
                "sport/activityType/type -> sport_type",
                "startTime/startDateLocal -> start_date_local",
                "distance/distance_m -> distance_m",
                "ascent/elevationGain -> elevation_gain_m",
                "speedAvg/average_speed -> average_speed_ms",
                "duration/moving_time -> moving_time_sec",
                "summary_polyline se disponibile, altrimenti payload workout",
                "raw workout payload -> raw_payload",
            ],
            "differences": [
                {"field": "Accesso API", "adidas": "non documentato ufficialmente nel progetto", "provider": "Partner Program + OAuth + Ocp-Apim-Subscription-Key"},
                {"field": "Dati attività", "adidas": "baseline logica con export GPX/JSON", "provider": "workouts e daily activities da Suunto Cloud API"},
            ],
            "missing_fields": [
                "Nel progetto Street Skate l'import Suunto non è ancora testato end-to-end con un account partner reale.",
                "La disponibilità di punti GPS, campioni cardio e lap dipende dal modello dispositivo e dal payload workout ricevuto.",
                "Sleep, POI e dati personali come peso o zone FC non risultano disponibili via Suunto Cloud API.",
            ],
            "warning": "Prima di usare Suunto in produzione servono approvazione partner, OAuth settings e subscription key validi. Al 19 agosto 2026 la richiesta risulta ricevuta da Suunto ed e in review per circa due settimane.",
        },
        "runkeeper": {
            "summary": "Runkeeper usa il vecchio ecosistema HealthGraph. Nel progetto viene trattato come provider legacy da verificare sul campo prima di qualsiasi uso reale.",
            "importable_fields": [
                "user uri/id -> identita utente collegata",
                "uri/id attività -> external_id",
                "notes/name/title -> name",
                "type/sport/activityType -> sport_type",
                "start_time -> start_date_local",
                "total_distance/distance -> distance_m",
                "climb/elevation_gain -> elevation_gain_m",
                "average_speed/avg_speed -> average_speed_ms",
                "duration/moving_time -> moving_time_sec",
                "summary_polyline se disponibile",
                "raw activity payload -> raw_payload",
            ],
            "differences": [
                {"field": "Stato API", "adidas": "baseline logica attuale del progetto", "provider": "provider legacy con documentazione storica HealthGraph"},
                {"field": "Schema attività", "adidas": "activity/export moderno del progetto", "provider": "resource-based API Runkeeper"},
            ],
            "missing_fields": [
                "Integrazione non testata end-to-end nel progetto Street Skate.",
                "Disponibilità di nuove app/API partner non verificata per nuove registrazioni.",
            ],
            "warning": "Prima di usarlo servono verifica reale delle credenziali developer Runkeeper e test dell'attuale disponibilità degli endpoint.",
        },
        "garmin": {
            "summary": "Garmin Connect richiede accesso al Garmin Connect Developer Program. Se il progetto viene approvato, l'Activity API può esporre file completi FIT/GPX/TCX e dettagli attività più ricchi del semplice CSV.",
            "importable_fields": [
                "activityId/id -> external_id",
                "activityName/name/title -> name",
                "activityType/sport/type -> sport_type",
                "startTimeLocal/startDateLocal -> start_date_local",
                "distanceInMeters/distance -> distance_m",
                "elevationGainInMeters/elevation_gain -> elevation_gain_m",
                "averageSpeedInMetersPerSecond/average_speed -> average_speed_ms",
                "durationInSeconds/moving_time -> moving_time_sec",
                "summaryPolyline oppure file FIT/GPX/TCX completi",
                "raw activity payload -> raw_payload",
            ],
            "differences": [
                {"field": "Export CSV", "adidas": "non usato come fonte principale", "provider": "spesso non contiene tutti i punti GPS e i dettagli puntuali"},
                {"field": "Dettaglio attività", "adidas": "GPX/JSON export o payload API", "provider": "Activity API con file FIT/GPX/TCX completi se il progetto viene approvato"},
            ],
            "missing_fields": [
                "Senza accesso al Garmin Connect Developer Program il login API reale non è attivabile nel progetto.",
                "Gli endpoint ufficiali concreti vengono rilasciati/abilitati nel portale Garmin dopo approvazione del progetto.",
            ],
            "warning": "Con Garmin il CSV esportato manualmente non basta per tutti i punti. Per i dati mancanti serve l'Activity API ufficiale e l'accesso ai file attività completi.",
        },
        "inline_route_tracking": {
            "summary": "Inline Route Tracking è predisposto nel progetto come provider futuro. Il pulsante di login rapido e il callback backend sono già riservati, ma il login reale resta in attesa delle API ufficiali di Daniele.",
            "importable_fields": [
                "routeId/activityId/id -> external_id",
                "routeName/activityName/name -> name",
                "activityType/sport/type -> sport_type",
                "startTimeLocal/startDateLocal/start_time -> start_date_local",
                "distanceInMeters/distance -> distance_m",
                "elevationGainInMeters/elevation_gain -> elevation_gain_m",
                "averageSpeedInMetersPerSecond/average_speed -> average_speed_ms",
                "durationInSeconds/moving_time -> moving_time_sec",
                "summaryPolyline oppure punti GPS se previsti dalle API future",
                "raw activity payload -> raw_payload",
            ],
            "differences": [
                {"field": "Login/Auth", "adidas": "flusso già modellato come baseline", "provider": "in attesa di endpoint ufficiali e schema callback"},
                {"field": "Dettaglio percorso", "adidas": "polyline o GPX/export", "provider": "da confermare quando saranno disponibili le API attività"},
            ],
            "missing_fields": [
                "Client ID, Client Secret, endpoint auth/token/user/activities e scope non ancora definiti ufficialmente.",
                "Schema dati reale delle attività non ancora confermato dall'app mobile.",
            ],
            "warning": "Fino alla consegna delle API ufficiali il provider resta predisposto ma non attivabile in produzione.",
        },
    }
    out = common.get(provider) or common["adidas"]
    return {
        "provider": provider,
        "baseline_fields": baseline_fields,
        **out,
    }


def _decode_polyline(polyline: str) -> list[tuple[float, float]]:
    if not polyline:
        return []
    idx = lat = lng = 0
    out: list[tuple[float, float]] = []
    while idx < len(polyline):
        for coord in ("lat", "lng"):
            shift = res = 0
            while True:
                b = ord(polyline[idx]) - 63
                idx += 1
                res |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(res >> 1) if res & 1 else res >> 1
            if coord == "lat":
                lat += delta
            else:
                lng += delta
        out.append((lat / 1e5, lng / 1e5))
    return out


def _bbox(coords: list[tuple[float, float]]) -> dict[str, float] | None:
    if not coords:
        return None
    lats = [c[0] for c in coords]
    lngs = [c[1] for c in coords]
    return {"south": min(lats), "west": min(lngs), "north": max(lats), "east": max(lngs)}


def _bbox_from_points(points: list[dict[str, Any]]) -> dict[str, float] | None:
    coords = [(float(p["lat"]), float(p["lng"])) for p in points if "lat" in p and "lng" in p]
    return _bbox(coords)


def _haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    r = 6371000
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _slope_color(slope_pct: float | None) -> str:
    if slope_pct is None:
        return "#cbd5e1"
    abs_slope = abs(float(slope_pct))
    if abs_slope < 2:
        return "#22c55e"
    if abs_slope < 6:
        return "#facc15"
    if abs_slope < 10:
        return "#fb923c"
    return "#ef4444"


def _slope_direction(slope_pct: float | None, flat_pct: float = 1.5) -> str:
    if slope_pct is None:
        return "piano"
    if float(slope_pct) > flat_pct:
        return "salita"
    if float(slope_pct) < -flat_pct:
        return "discesa"
    return "piano"


def _slope_intensity_label(slope_pct: float | None) -> str:
    if slope_pct is None:
        return "n.d."
    abs_slope = abs(float(slope_pct))
    if abs_slope < 2:
        return "piano"
    if abs_slope < 6:
        return "moderata"
    if abs_slope < 10:
        return "ripida"
    return "estrema"


def _build_point_profile(coords: list[tuple[float, float]], points: list[dict[str, Any]], distance_m: float, elev_m: float) -> dict[str, Any]:
    base_points: list[dict[str, Any]] = []
    if points:
        for p in points:
            if "lat" not in p or "lng" not in p:
                continue
            base_points.append(
                {
                    "lat": float(p["lat"]),
                    "lng": float(p["lng"]),
                    "elevation": float(p["elevation"]) if p.get("elevation") is not None else None,
                    "time": p.get("time"),
                    "segment_distance_m": p.get("segment_distance_m"),
                    "cumulative_distance_m": p.get("cumulative_distance_m"),
                    "slope_pct": p.get("slope_pct"),
                    "speed_kmh": p.get("speed_kmh"),
                }
            )
    elif coords:
        base_points = [{"lat": lat, "lng": lng, "elevation": None} for lat, lng in coords]
    base_points = smooth_elevations(base_points, SLOPE_SETTINGS["smoothing_window"])

    if len(base_points) < 2:
        fallback = round((elev_m / distance_m * 100), 2) if distance_m else 0
        return {
            "points": base_points,
            "colors": [],
            "legend": [
                {"label": "piano", "color": "#22c55e"},
                {"label": "moderata", "color": "#facc15"},
                {"label": "ripida", "color": "#fb923c"},
                {"label": "estrema", "color": "#ef4444"},
            ],
            "max_slope_pct": fallback,
            "max_slope_direction": "piano",
            "weighted_avg_slope_pct": fallback,
        }

    cumulative = 0.0
    colors: list[str] = []
    prof: list[dict[str, Any]] = []

    for i, p in enumerate(base_points):
        item = dict(p)
        if i == 0:
            item["segment_distance_m"] = item.get("segment_distance_m") or 0.0
            item["cumulative_distance_m"] = item.get("cumulative_distance_m") or 0.0
            item["slope_pct"] = item.get("slope_pct")
            item["color"] = _slope_color(None)
            prof.append(item)
            continue
        prev = base_points[i - 1]
        seg_m = float(item.get("segment_distance_m") or _haversine_m((prev["lat"], prev["lng"]), (p["lat"], p["lng"])))
        cumulative += seg_m
        slope = item.get("slope_pct")
        if slope is None and prev.get("elevation") is not None and p.get("elevation") is not None and seg_m > 0:
            slope = ((float(p["elevation"]) - float(prev["elevation"])) / seg_m) * 100
        color = _slope_color(slope)
        colors.append(color)
        item["segment_distance_m"] = round(seg_m, 2)
        item["cumulative_distance_m"] = round(float(item.get("cumulative_distance_m") or cumulative), 2)
        item["slope_pct"] = round(slope, 2) if slope is not None else None
        item["slope_abs_pct"] = round(abs(float(slope)), 2) if slope is not None else None
        item["slope_direction"] = _slope_direction(slope)
        item["slope_intensity"] = _slope_intensity_label(slope)
        item["color"] = color
        prof.append(item)

    fallback = (elev_m / distance_m * 100) if distance_m else 0
    max_slope_pct, weighted_avg_slope_pct, trend_runs = trend_metrics(
        prof[1:],
        min_run_distance_m=SLOPE_SETTINGS["min_run_distance_m"],
        max_slope_cap_pct=SLOPE_SETTINGS["max_cap_pct"],
    )
    if not max_slope_pct and not weighted_avg_slope_pct:
        max_slope_pct = round(max(fallback * 1.8, fallback), 2)
        weighted_avg_slope_pct = round(fallback, 2)
    max_direction = "piano"
    best_run = None
    for run in trend_runs:
        slope = run.get("slope_pct")
        if slope is None:
            continue
        if best_run is None or abs(float(slope)) > abs(float(best_run.get("slope_pct") or 0)):
            best_run = run
    if best_run and best_run.get("slope_pct") is not None:
        max_direction = _slope_direction(float(best_run["slope_pct"]))
    return {
        "points": prof,
        "colors": colors,
        "trend_runs": trend_runs,
        "legend": [
            {"label": "piano", "color": "#22c55e"},
            {"label": "moderata", "color": "#facc15"},
            {"label": "ripida", "color": "#fb923c"},
            {"label": "estrema", "color": "#ef4444"},
        ],
        "max_slope_pct": max_slope_pct,
        "max_slope_direction": max_direction,
        "weighted_avg_slope_pct": weighted_avg_slope_pct,
    }


def _surface_penalty(surface: str | None, highway: str | None, cycleway: str | None) -> int:
    surface = (surface or "unknown").lower()
    highway = (highway or "unknown").lower()
    cycleway = (cycleway or "").lower()
    good = {"asphalt", "paved", "concrete", "fine_gravel"}
    bad = {"cobblestone", "sett", "unpaved", "gravel", "ground", "dirt", "earth", "grass", "sand"}
    base = 10 if surface in good else 80 if surface in bad else 45
    if highway in {"cycleway", "footway", "pedestrian", "path"} or cycleway:
        base -= 12
    if highway in {"primary", "secondary", "trunk"}:
        base += 15
    return max(0, min(base, 100))


def _traffic_penalty(highway: str | None) -> int:
    return {
        "cycleway": 10,
        "footway": 12,
        "pedestrian": 12,
        "residential": 35,
        "tertiary": 50,
        "secondary": 70,
        "primary": 85,
        "trunk": 95,
    }.get((highway or "unknown").lower(), 45)


def _smoothness_penalty(smoothness: str | None) -> int:
    return {
        "excellent": 5,
        "good": 15,
        "intermediate": 35,
        "bad": 60,
        "very_bad": 75,
        "horrible": 85,
        "very_horrible": 95,
        "impassable": 100,
    }.get((smoothness or "unknown").lower(), 45)


def _local_enrichment_placeholder() -> dict[str, Any]:
    return {
        "source": "local-placeholder",
        "dominant_surface": None,
        "dominant_smoothness": None,
        "has_cobblestone": None,
        "weather_source": None,
        "weather_observed_at": None,
        "weather_lat": None,
        "weather_lng": None,
        "max_slope_pct": None,
        "max_slope_direction": None,
        "weighted_avg_slope_pct": None,
        "atmospheric_pressure_hpa": None,
        "temperature_c": None,
        "segment_max_speed_kmh": None,
        "segment_speed_source": None,
        "point_profile": {"points": [], "colors": [], "legend": []},
        "declared_difficulty": None,
    }


def _skate_difficulty(distance_m: float, elev_m: float, enrichment: dict[str, Any]) -> dict[str, Any]:
    avg_grade_pct = (elev_m / distance_m * 100) if distance_m else 0
    surface = enrichment.get("dominant_surface")
    smoothness = enrichment.get("dominant_smoothness")
    max_slope_pct = float(enrichment.get("max_slope_pct") or max(avg_grade_pct * 1.8, avg_grade_pct))
    max_slope_direction = enrichment.get("max_slope_direction") or "piano"
    weighted_avg_slope_pct = float(enrichment.get("weighted_avg_slope_pct") or avg_grade_pct)
    pressure_hpa = enrichment.get("atmospheric_pressure_hpa")
    temperature_c = enrichment.get("temperature_c")
    segment_max_speed_kmh = enrichment.get("segment_max_speed_kmh")

    surface_component = _surface_penalty(surface, None, None) * BASE_SKATE_WEIGHTS["surface"] / 100
    smoothness_component = _smoothness_penalty(smoothness) * BASE_SKATE_WEIGHTS["smoothness"] / 100
    max_slope_component = min(max_slope_pct / 20 * 100, 100) * BASE_SKATE_WEIGHTS["max_slope"] / 100
    weighted_slope_component = min(weighted_avg_slope_pct / 10 * 100, 100) * BASE_SKATE_WEIGHTS["weighted_slope"] / 100
    pressure_component = (
        min(abs(float(pressure_hpa) - 1013.25) / 25 * 100, 100) * BASE_SKATE_WEIGHTS["pressure"] / 100
        if pressure_hpa is not None
        else 50 * BASE_SKATE_WEIGHTS["pressure"] / 100
    )
    temperature_component = (
        min(abs(float(temperature_c) - 18) / 18 * 100, 100) * BASE_SKATE_WEIGHTS["temperature"] / 100
        if temperature_c is not None
        else 50 * BASE_SKATE_WEIGHTS["temperature"] / 100
    )
    segment_speed_component = (
        min(float(segment_max_speed_kmh) / 45 * 100, 100) * BASE_SKATE_WEIGHTS["segment_speed"] / 100
        if segment_max_speed_kmh is not None
        else 50 * BASE_SKATE_WEIGHTS["segment_speed"] / 100
    )
    score = round(
        min(
            surface_component
            + smoothness_component
            + max_slope_component
            + weighted_slope_component
            + pressure_component
            + temperature_component
            + segment_speed_component,
            100,
        )
    )
    label = "facile" if score < 35 else "media" if score < 70 else "difficile"
    declared = enrichment.get("declared_difficulty") if isinstance(enrichment.get("declared_difficulty"), dict) else None
    declared_range = None
    mismatch = None
    confidence = None
    if declared:
        declared_range = [int(declared.get("score_min", 0)), int(declared.get("score_max", 100))]
        mismatch = not (declared_range[0] <= score <= declared_range[1])
        confidence = 0.75 if declared.get("level") != "Unknown" else 0.25
    return {
        "score": score,
        "label": label,
        "declared_difficulty": declared,
        "classification_confidence": confidence,
        "classification_mismatch": mismatch,
        "breakdown": {
            "surface_score": round(surface_component, 2),
            "smoothness_score": round(smoothness_component, 2),
            "max_slope_score": round(max_slope_component, 2),
            "weighted_slope_score": round(weighted_slope_component, 2),
            "pressure_score": round(pressure_component, 2),
            "temperature_score": round(temperature_component, 2),
            "segment_speed_score": round(segment_speed_component, 2),
            "weights": BASE_SKATE_WEIGHTS,
            "declared_score_range": declared_range,
        },
        "factors": {
            "surface_type": surface or "non ancora arricchito da OSM",
            "smoothness_type": smoothness or "non ancora arricchito da OSM",
            "max_slope_pct": round(max_slope_pct, 2),
            "max_slope_direction": max_slope_direction,
            "weighted_avg_slope_pct": round(weighted_avg_slope_pct, 2),
            "has_cobblestone": enrichment.get("has_cobblestone"),
            "atmospheric_pressure_hpa": pressure_hpa or "da servizio meteo esterno da definire",
            "temperature_c": temperature_c or "da servizio meteo esterno da definire",
            "segment_max_speed_kmh": segment_max_speed_kmh or "da storico utenti sul tratto",
            "weather_source": enrichment.get("weather_source") or "non configurato",
            "segment_speed_source": enrichment.get("segment_speed_source") or "non configurato",
        },
    }


def _serialize_route(route: Route) -> dict[str, Any]:
    enrichment = dict(route.enrichment or {}) if isinstance(route.enrichment, dict) else {}
    difficulty = dict(route.difficulty or {}) if isinstance(route.difficulty, dict) else {}
    declared = difficulty.get("declared_difficulty")
    if not isinstance(declared, dict):
        declared = enrichment.get("declared_difficulty")
    if not isinstance(declared, dict):
        raw = route.raw_payload if isinstance(route.raw_payload, dict) else {}
        candidates = [
            route.name,
            raw.get("filename"),
            raw.get("name"),
            raw.get("external_id"),
            route.external_id,
        ]
        for candidate in candidates:
            if not candidate:
                continue
            declared = _declared_difficulty_from_filename(str(candidate))
            if declared:
                break
    if isinstance(declared, dict):
        enrichment["declared_difficulty"] = declared
        difficulty["declared_difficulty"] = declared
    return {
        "id": route.id,
        "external_id": route.external_id,
        "source": route.provider,
        "import_user_id": route.user_id,
        "import_user_label": route.import_user_label or (route.user.display_name if route.user else None),
        "name": route.name,
        "type": route.sport_type,
        "start_date_local": route.start_date_local,
        "distance_km": route.distance_km,
        "distance_m": route.distance_m,
        "elevation_gain_m": route.elevation_gain_m,
        "avg_grade_pct": route.avg_grade_pct,
        "average_speed_kmh": route.average_speed_kmh,
        "moving_time_sec": route.moving_time_sec,
        "imported_at": route.imported_at.isoformat() if route.imported_at else None,
        "map_polyline_available": route.map_polyline_available,
        "polyline_points_count": route.polyline_points_count,
        "bbox": route.bbox,
        "enrichment": enrichment,
        "difficulty": difficulty,
    }


def _provider_meta() -> dict[str, dict[str, Any]]:
    out = {
        k: {
            "label": v.cfg.label,
            "configured": v.cfg.configured,
            "oauth_configured": v.cfg.configured,
            "mode": "oauth" if v.cfg.configured else "unconfigured",
            "button_visible": PROVIDER_BUTTON_SETTINGS.get(k, {}).get("visible", True),
            "button_enabled": PROVIDER_BUTTON_SETTINGS.get(k, {}).get("enabled", v.cfg.configured),
        }
        for k, v in PROVIDERS.items()
    }
    if "adidas" in out and _adidas_export_enabled():
        out["adidas"]["export_available"] = True
        if not out["adidas"]["oauth_configured"]:
            out["adidas"]["mode"] = "export_only"
    for key, meta in out.items():
        meta["implementation"] = _provider_implementation_status(key, meta)
    return out


def _provider_implementation_status(provider: str, meta: dict[str, Any]) -> dict[str, str]:
    oauth_ready = bool(meta.get("oauth_configured"))
    if provider == "strava":
        return {
            "login": "Implemented",
            "auth": "Implemented",
            "settings": "Configured" if oauth_ready else "Missing",
        }
    if provider == "mapmyrun":
        return {
            "login": "Implemented",
            "auth": "Implemented",
            "settings": "Configured" if oauth_ready else "Missing",
        }
    if provider == "suunto":
        return {
            "login": "Review pending",
            "auth": "Partner review in progress",
            "settings": "Configured" if oauth_ready else "Missing",
        }
    if provider == "runkeeper":
        return {
            "login": "Prepared",
            "auth": "Legacy API",
            "settings": "Configured" if oauth_ready else "Missing",
        }
    if provider == "garmin":
        return {
            "login": "Blocked",
            "auth": "Migration in progress",
            "settings": "Configured" if oauth_ready else "Missing",
        }
    if provider == "inline_route_tracking":
        return {
            "login": "Prepared",
            "auth": "Awaiting API",
            "settings": "Configured" if oauth_ready else "Missing",
        }
    if provider == "adidas":
        return {
            "login": "Standby",
            "auth": "Standby",
            "settings": "Configured" if oauth_ready else "Missing",
        }
    return {
        "login": "Unknown",
        "auth": "Unknown",
        "settings": "Unknown",
    }


def _adidas_export_path() -> Path:
    return Path(ADIDAS_EXPORT_DIR)


def _adidas_export_enabled() -> bool:
    p = _adidas_export_path()
    return p.exists() and p.is_dir()


def _adidas_user_profile() -> dict[str, Any]:
    d = Path(ADIDAS_EXPORT_USER_DIR)
    if not d.exists() or not d.is_dir():
        return {}
    for p in sorted(d.glob("*.json")):
        if p.name in {"user_account.json", "user_preferences.json"}:
            continue
        try:
            body = json.loads(p.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        if isinstance(body, dict):
            return body
    return {}


def _adidas_export_connection(db: Session) -> AuthConnection:
    profile = _adidas_user_profile()
    pid = str(profile.get("uidt") or profile.get("id") or "local_export")
    stmt = select(AuthConnection).where(
        AuthConnection.provider == "adidas",
        AuthConnection.provider_user_id == pid,
    )
    conn = db.execute(stmt).scalar_one_or_none()
    email = profile.get("email")
    name = " ".join(v for v in [profile.get("first_name"), profile.get("last_name")] if v).strip() or email or "Utente adidas Running"
    if not conn:
        user = User(display_name=name)
        db.add(user)
        db.flush()
        conn = AuthConnection(
            user_id=user.id,
            provider="adidas",
            provider_user_id=pid,
            provider_username=email,
            label=name,
            access_token="export",
            refresh_token=None,
            token_type="export",
            scope="local_export",
            raw_profile=profile or {"source": "adidas_export"},
        )
        db.add(conn)
    else:
        conn.provider_username = email
        conn.label = name
        conn.access_token = "export"
        conn.token_type = "export"
        conn.scope = "local_export"
        conn.raw_profile = profile or {"source": "adidas_export"}
        conn.user.display_name = name
    db.commit()
    db.refresh(conn)
    return conn


def _adidas_export_bundles() -> list[dict[str, tuple[str, bytes]]]:
    bundles: dict[str, dict[str, tuple[str, bytes]]] = {}
    for p in sorted(_adidas_export_path().glob("*")):
        if p.suffix.lower() not in {".gpx", ".json"}:
            continue
        bundles.setdefault(p.stem, {})[p.suffix.lower()] = (p.name, p.read_bytes())
    return [bundle for bundle in bundles.values() if ".gpx" in bundle]


def _normalize_route_name(name: str | None) -> str:
    return " ".join((name or "").strip().lower().split())


def _is_same_route_candidate(existing: Route, candidate: dict[str, Any]) -> bool:
    existing_name = _normalize_route_name(existing.name)
    candidate_name = _normalize_route_name(candidate.get("name"))
    if not existing_name or not candidate_name or existing_name != candidate_name:
        return False
    existing_dist = float(existing.distance_m or 0)
    candidate_dist = float(candidate.get("distance_m") or 0)
    if not existing_dist or not candidate_dist:
        return False
    diff_ratio = abs(existing_dist - candidate_dist) / max(existing_dist, candidate_dist)
    if diff_ratio > (DUPLICATE_SETTINGS["distance_diff_pct"] / 100):
        return False
    existing_points = _route_points_from_enrichment(existing.enrichment)
    candidate_points = _route_points_from_enrichment(candidate.get("enrichment"))
    if existing_points and candidate_points:
        return _endpoints_match(existing_points, candidate_points)
    existing_start = (existing.start_date_local or "").strip()
    candidate_start = str(candidate.get("start_date_local") or "").strip()
    return bool(existing_start and candidate_start and existing_start == candidate_start)


def _find_duplicate_route(
    db: Session,
    candidate: dict[str, Any],
    provider: str | None = None,
    exclude_external_ids: set[str] | None = None,
) -> Route | None:
    stmt = select(Route)
    if provider:
        stmt = stmt.where(Route.provider == provider)
    routes = list(db.execute(stmt).scalars())
    excluded = exclude_external_ids or set()
    for route in routes:
        if route.external_id in excluded:
            continue
        if _is_same_route_candidate(route, candidate):
            return route
    return None


def _find_manual_duplicate_route(
    db: Session,
    candidate: dict[str, Any],
    exclude_external_ids: set[str] | None = None,
) -> Route | None:
    return _find_duplicate_route(db, candidate, provider=None, exclude_external_ids=exclude_external_ids)


async def _import_adidas_export(
    db: Session,
    user: User,
    conn: AuthConnection,
    on_progress: Any | None = None,
) -> tuple[list[Route], int, int, list[str], int]:
    items: list[dict[str, Any]] = []
    skipped_short = 0
    skipped_short_names: list[str] = []
    done = 0
    bundles = _adidas_export_bundles()
    total = len(bundles)
    for bundle in bundles:
        if ".gpx" not in bundle:
            continue
        gpx_name, gpx_raw = bundle[".gpx"]
        sidecar_raw = bundle.get(".json", (None, None))[1]
        parsed = parse_gpx_bytes(gpx_raw, gpx_name, sidecar_raw=sidecar_raw)
        if float(parsed.get("distance_m") or 0) < MIN_IMPORT_DISTANCE_M:
            skipped_short += 1
            skipped_short_names.append(parsed.get("name") or gpx_name)
            done += 1
            if on_progress:
                await on_progress(done, total, parsed.get("name") or gpx_name)
            continue
        items.append(await _enrich_route_data(db, _normalize_for_storage("adidas", parsed)))
        done += 1
        if on_progress:
            await on_progress(done, total, parsed.get("name") or gpx_name)
    return _store_routes(db, user, conn, items), len(items), skipped_short, skipped_short_names, total


async def _import_provider_api(
    db: Session,
    user: User,
    conn: AuthConnection,
    provider: str,
    page: int = 1,
    per_page: int = 50,
    on_progress: Any | None = None,
) -> tuple[list[Route], int, int, list[str], int]:
    adapter = PROVIDERS.get(provider)
    if not adapter:
        raise HTTPException(status_code=404, detail="Provider non supportato")
    if not adapter.cfg.activities_url:
        raise HTTPException(status_code=400, detail=f"Endpoint attività {adapter.cfg.label} non configurato")
    token = await _refresh_connection_token(adapter, conn, db)
    raw = await adapter.fetch_activities(token, conn, page, per_page)
    items: list[dict[str, Any]] = []
    skipped_short = 0
    skipped_short_names: list[str] = []
    imported_done = 0
    total = len(raw)
    for idx, entry in enumerate(raw, start=1):
        current = None
        if entry:
            norm = adapter.normalize_activity(entry)
            current = norm.get("name") or str(norm.get("external_id") or idx)
            if norm.get("external_id"):
                route_exists = db.execute(
                    select(Route.id).where(Route.provider == provider, Route.external_id == str(norm["external_id"]))
                ).scalar_one_or_none()
                if not route_exists and not _find_duplicate_route(db, _normalize_for_storage(provider, norm), provider=provider):
                    if float(norm.get("distance_m") or 0) < MIN_IMPORT_DISTANCE_M:
                        skipped_short += 1
                        skipped_short_names.append(norm.get("name") or str(norm.get("external_id")))
                    else:
                        items.append(await _enrich_route_data(db, _normalize_for_storage(provider, norm)))
                        imported_done += 1
        if on_progress:
            await on_progress(idx, total, imported_done, current)
    return _store_routes(db, user, conn, items), len(items), skipped_short, skipped_short_names, total


def _import_payload(provider: str, routes: list[Route], imported: int, skipped_short: int, skipped_short_names: list[str]) -> dict[str, Any]:
    return {
        "imported": imported,
        "skipped_short": skipped_short,
        "skipped_short_names": skipped_short_names,
        "total_cached": len(routes),
        "provider": provider,
        "routes": [_serialize_route(r) for r in routes],
    }


async def _run_adidas_import_job(job_id: str, user_id: int, conn_id: int) -> None:
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        conn = db.get(AuthConnection, conn_id)
        if not user or not conn:
            IMPORT_JOBS[job_id].update({"status": "error", "error": "Sessione import non valida"})
            return

        async def on_progress(done: int, total: int, name: str | None = None) -> None:
            IMPORT_JOBS[job_id].update(
                {
                    "status": "running",
                    "done": done,
                    "total": total,
                    "imported": done,
                    "current": name,
                    "updated_at": datetime.now(UTC).isoformat(),
                }
            )
            await asyncio.sleep(0)

        routes, imported, skipped_short, skipped_short_names, total = await _import_adidas_export(db, user, conn, on_progress=on_progress)
        IMPORT_JOBS[job_id].update(
            {
                "status": "done",
                "done": total,
                "total": total,
                "imported": imported,
                "current": None,
                "result": _import_payload("adidas", routes, imported, skipped_short, skipped_short_names),
                "updated_at": datetime.now(UTC).isoformat(),
            }
        )
    except Exception as exc:
        IMPORT_JOBS[job_id].update(
            {
                "status": "error",
                "error": str(exc),
                "updated_at": datetime.now(UTC).isoformat(),
            }
        )
    finally:
        db.close()


async def _run_provider_import_job(job_id: str, user_id: int, conn_id: int, provider: str, page: int = 1, per_page: int = 50) -> None:
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        conn = db.get(AuthConnection, conn_id)
        if not user or not conn or conn.provider != provider:
            IMPORT_JOBS[job_id].update({"status": "error", "error": "Sessione import non valida"})
            return

        async def on_progress(done: int, total: int, imported: int, name: str | None = None) -> None:
            IMPORT_JOBS[job_id].update(
                {
                    "status": "running",
                    "done": done,
                    "total": total,
                    "imported": imported,
                    "current": name,
                    "updated_at": datetime.now(UTC).isoformat(),
                }
            )
            await asyncio.sleep(0)

        routes, imported, skipped_short, skipped_short_names, total = await _import_provider_api(
            db, user, conn, provider, page=page, per_page=per_page, on_progress=on_progress
        )
        IMPORT_JOBS[job_id].update(
            {
                "status": "done",
                "done": total,
                "total": total,
                "imported": imported,
                "current": None,
                "result": _import_payload(provider, routes, imported, skipped_short, skipped_short_names),
                "updated_at": datetime.now(UTC).isoformat(),
            }
        )
    except Exception as exc:
        IMPORT_JOBS[job_id].update(
            {
                "status": "error",
                "error": str(exc),
                "updated_at": datetime.now(UTC).isoformat(),
            }
        )
    finally:
        db.close()


def _get_session_user(request: Request, db: Session) -> tuple[User | None, AuthConnection | None]:
    if not _touch_or_expire_session(request):
        return None, None
    uid = request.session.get("user_id")
    cid = request.session.get("connection_id")
    if not uid or not cid:
        return None, None
    user = db.get(User, uid)
    conn = db.get(AuthConnection, cid)
    if not user or not conn or conn.user_id != user.id:
        return None, None
    return user, conn


async def _refresh_connection_token(adapter: ProviderAdapter, conn: AuthConnection, db: Session) -> str:
    if conn.expires_at and conn.expires_at > datetime.now(UTC):
        return conn.access_token
    if not conn.refresh_token:
        raise HTTPException(status_code=401, detail=f"Token {conn.label or conn.provider} scaduto. Rifai il login.")
    data = await adapter.refresh_token(conn.refresh_token)
    tokens = adapter.connection_tokens(data, conn.refresh_token)
    conn.access_token = tokens["access_token"]
    conn.refresh_token = tokens["refresh_token"]
    conn.token_type = tokens["token_type"]
    conn.scope = tokens["scope"]
    conn.expires_at = tokens["expires_at"]
    db.commit()
    db.refresh(conn)
    return conn.access_token


def _upsert_connection(
    db: Session,
    adapter: ProviderAdapter,
    provider: str,
    token_data: dict[str, Any],
    profile: dict[str, Any],
) -> AuthConnection:
    provider_user_id, provider_username, label = adapter.connection_identity(token_data, profile)
    stmt = select(AuthConnection).where(
        AuthConnection.provider == provider, AuthConnection.provider_user_id == provider_user_id
    )
    conn = db.execute(stmt).scalar_one_or_none()
    user: User
    if not conn:
        user = User(display_name=label)
        db.add(user)
        db.flush()
        conn = AuthConnection(user_id=user.id, provider=provider, provider_user_id=provider_user_id)
        db.add(conn)
    else:
        user = conn.user
    tokens = adapter.connection_tokens(token_data, conn.refresh_token)
    conn.provider_username = provider_username
    conn.label = label
    conn.access_token = tokens["access_token"]
    conn.refresh_token = tokens["refresh_token"]
    conn.token_type = tokens["token_type"]
    conn.scope = tokens["scope"]
    conn.expires_at = tokens["expires_at"]
    conn.raw_profile = profile or token_data.get("athlete") or {}
    user.display_name = label
    db.commit()
    db.refresh(conn)
    return conn


def _normalize_for_storage(provider: str, raw: dict[str, Any]) -> dict[str, Any]:
    distance_m = float(raw.get("distance_m") or 0)
    elevation_gain_m = float(raw.get("elevation_gain_m") or 0)
    average_speed_ms = float(raw.get("average_speed_ms") or 0)
    summary_polyline = raw.get("summary_polyline") or ""
    points = raw.get("points") or []
    metrics = raw.get("metrics") or {}
    coords = _decode_polyline(summary_polyline)
    bbox = _bbox(coords) if coords else _bbox_from_points(points)
    enrichment = _local_enrichment_placeholder()
    raw_payload = raw.get("raw_payload") or raw
    if isinstance(raw_payload, dict) and isinstance(raw_payload.get("declared_difficulty"), dict):
        enrichment["declared_difficulty"] = raw_payload.get("declared_difficulty")
    point_profile = _build_point_profile(coords, points, distance_m, elevation_gain_m)
    enrichment["point_profile"] = point_profile
    enrichment["max_slope_pct"] = point_profile["max_slope_pct"]
    enrichment["max_slope_direction"] = point_profile.get("max_slope_direction")
    enrichment["weighted_avg_slope_pct"] = point_profile["weighted_avg_slope_pct"]
    enrichment["temperature_c"] = metrics.get("temperature_c")
    enrichment["atmospheric_pressure_hpa"] = metrics.get("atmospheric_pressure_hpa")
    enrichment["segment_max_speed_kmh"] = metrics.get("segment_max_speed_kmh")
    if enrichment["temperature_c"] is not None or enrichment["atmospheric_pressure_hpa"] is not None:
        enrichment["weather_source"] = "gpx-embedded"
    if enrichment["segment_max_speed_kmh"] is not None:
        enrichment["segment_speed_source"] = "gpx-derived"
    return {
        "provider": provider,
        "external_id": str(raw["external_id"]),
        "name": raw.get("name"),
        "sport_type": raw.get("sport_type"),
        "start_date_local": raw.get("start_date_local"),
        "distance_m": distance_m,
        "distance_km": round(distance_m / 1000, 2) if distance_m else 0,
        "elevation_gain_m": round(elevation_gain_m, 1),
        "avg_grade_pct": round((elevation_gain_m / distance_m * 100), 2) if distance_m else 0,
        "average_speed_kmh": round(average_speed_ms * 3.6, 2),
        "moving_time_sec": raw.get("moving_time_sec"),
        "summary_polyline": summary_polyline,
        "map_polyline_available": bool(summary_polyline or points),
        "polyline_points_count": len(coords) if coords else len(points),
        "bbox": bbox,
        "enrichment": enrichment,
        "difficulty": _skate_difficulty(distance_m, elevation_gain_m, enrichment),
        "raw_payload": raw_payload,
    }


async def _enrich_route_data(db: Session, norm: dict[str, Any]) -> dict[str, Any]:
    enrichment = norm.get("enrichment") or _local_enrichment_placeholder()
    norm["enrichment"] = enrichment
    profile_points = (enrichment.get("point_profile") or {}).get("points") or []
    if profile_points and not enrichment.get("dominant_surface"):
        try:
            osm = await fetch_surface_profile(OVERPASS_API_URL, profile_points)
            enrichment["dominant_surface"] = osm.get("dominant_surface")
            enrichment["dominant_smoothness"] = osm.get("dominant_smoothness")
            enrichment["has_cobblestone"] = bool(osm.get("has_cobblestone"))
            enrichment["surface_samples"] = osm.get("samples", [])
            enrichment["surface_source"] = "overpass"
        except Exception:
            enrichment["surface_source"] = "overpass_unavailable"
    if profile_points and (enrichment.get("temperature_c") is None or enrichment.get("atmospheric_pressure_hpa") is None):
        try:
            weather = await fetch_weather_snapshot(
                OPEN_METEO_ARCHIVE_URL,
                OPEN_METEO_FORECAST_URL,
                norm.get("start_date_local"),
                profile_points,
                norm.get("bbox"),
            )
            if weather:
                if enrichment.get("temperature_c") is None:
                    enrichment["temperature_c"] = weather.get("temperature_c")
                if enrichment.get("atmospheric_pressure_hpa") is None:
                    enrichment["atmospheric_pressure_hpa"] = weather.get("atmospheric_pressure_hpa")
                enrichment["weather_source"] = weather.get("weather_source")
                enrichment["weather_observed_at"] = weather.get("weather_observed_at")
                enrichment["weather_lat"] = weather.get("weather_lat")
                enrichment["weather_lng"] = weather.get("weather_lng")
        except Exception:
            enrichment["weather_source"] = enrichment.get("weather_source") or "open-meteo_unavailable"
    seg_idx = _route_segment_speed_index(profile_points)
    enrichment["segment_speed_index"] = seg_idx
    enrichment["segment_max_speed_kmh"] = _historical_segment_max_speed(db, seg_idx)
    norm["difficulty"] = _skate_difficulty(norm["distance_m"], norm["elevation_gain_m"], enrichment)
    return norm


def _store_routes(db: Session, user: User, conn: AuthConnection, items: list[dict[str, Any]]) -> list[Route]:
    ext_ids = [item["external_id"] for item in items]
    existing = {}
    if ext_ids:
        stmt = select(Route).where(Route.provider == conn.provider, Route.external_id.in_(ext_ids))
        existing = {r.external_id: r for r in db.execute(stmt).scalars()}
    saved: list[Route] = []
    excluded_ext_ids = set(ext_ids)
    for item in items:
        if item["external_id"] not in existing:
            dup = (
                _find_manual_duplicate_route(db, item, exclude_external_ids=excluded_ext_ids)
                if conn.provider == "manual"
                else _find_duplicate_route(db, item, provider=conn.provider, exclude_external_ids=excluded_ext_ids)
            )
            if dup:
                continue
        route = existing.get(item["external_id"])
        if not route:
            route = Route(
                user_id=user.id,
                connection_id=conn.id,
                provider=conn.provider,
                external_id=item["external_id"],
            )
            db.add(route)
        route.user_id = user.id
        route.connection_id = conn.id
        route.import_user_label = user.display_name or conn.label or conn.provider_username or conn.provider
        route.name = item["name"]
        route.sport_type = item["sport_type"]
        route.start_date_local = item["start_date_local"]
        route.distance_km = item["distance_km"]
        route.distance_m = item["distance_m"]
        route.elevation_gain_m = item["elevation_gain_m"]
        route.avg_grade_pct = item["avg_grade_pct"]
        route.average_speed_kmh = item["average_speed_kmh"]
        route.moving_time_sec = item["moving_time_sec"]
        route.summary_polyline = item["summary_polyline"]
        route.map_polyline_available = item["map_polyline_available"]
        route.polyline_points_count = item["polyline_points_count"]
        route.bbox = item["bbox"]
        route.enrichment = item["enrichment"]
        route.difficulty = item["difficulty"]
        route.raw_payload = item["raw_payload"]
        saved.append(route)
    db.commit()
    stmt = select(Route).where(Route.user_id == user.id).order_by(Route.start_date_local.desc().nullslast(), Route.id.desc())
    return list(db.execute(stmt).scalars())


def _admin_route_users(db: Session) -> list[dict[str, Any]]:
    rows = list(
        db.execute(
            select(User.id, User.display_name)
            .join(Route, Route.user_id == User.id)
            .distinct()
            .order_by(User.display_name.asc().nullslast(), User.id.asc())
        )
    )
    return [{"id": str(uid), "label": label or f"Utente {uid}"} for uid, label in rows]


def _user_routes(db: Session, user_id: int) -> list[Route]:
    stmt = select(Route).where(Route.user_id == user_id).order_by(Route.start_date_local.desc().nullslast(), Route.id.desc())
    return list(db.execute(stmt).scalars())


def _all_routes(db: Session) -> list[Route]:
    stmt = select(Route).order_by(Route.start_date_local.desc().nullslast(), Route.id.desc())
    return list(db.execute(stmt).scalars())


def _current_provider_context(conn: AuthConnection | None) -> dict[str, Any]:
    if not conn:
        return {"provider_key": None, "provider_label": None}
    return {"provider_key": conn.provider, "provider_label": conn.label or conn.provider}


def _is_admin(request: Request) -> bool:
    return bool(_get_local_admin(request))


def _get_or_create_admin_entities(db: Session) -> tuple[User, AuthConnection]:
    stmt = select(AuthConnection).where(AuthConnection.provider == "manual", AuthConnection.provider_user_id == "local_admin")
    conn = db.execute(stmt).scalar_one_or_none()
    if conn:
        return conn.user, conn
    user = User(display_name="Admin locale")
    db.add(user)
    db.flush()
    conn = AuthConnection(
        user_id=user.id,
        provider="manual",
        provider_user_id="local_admin",
        provider_username=ADMIN_USERNAME,
        label="Admin locale",
        access_token="manual",
        refresh_token=None,
        token_type="manual",
        scope="manual",
        raw_profile={"source": "local_admin"},
    )
    db.add(conn)
    db.commit()
    db.refresh(conn)
    return user, conn


def _segment_signature(a: dict[str, Any], b: dict[str, Any], precision: int = 4) -> str:
    p1 = (round(float(a["lat"]), precision), round(float(a["lng"]), precision))
    p2 = (round(float(b["lat"]), precision), round(float(b["lng"]), precision))
    lo, hi = sorted([p1, p2])
    return f"{lo[0]}:{lo[1]}|{hi[0]}:{hi[1]}"


def _route_segment_speed_index(points: list[dict[str, Any]]) -> dict[str, float]:
    out: dict[str, float] = {}
    if len(points) < 2:
        return out
    for i in range(1, len(points)):
        a = points[i - 1]
        b = points[i]
        speed = b.get("speed_kmh")
        if speed is None:
            continue
        sig = _segment_signature(a, b)
        out[sig] = max(float(speed), out.get(sig, 0.0))
    return out


def _historical_segment_max_speed(db: Session, segment_index: dict[str, float]) -> float | None:
    if not segment_index:
        return None
    stmt = select(Route.enrichment)
    hist = 0.0
    for enrichment in db.execute(stmt).scalars():
        if not isinstance(enrichment, dict):
            continue
        idx = enrichment.get("segment_speed_index") or {}
        if not isinstance(idx, dict):
            continue
        for sig in segment_index:
            val = idx.get(sig)
            if val is not None:
                hist = max(hist, float(val))
    current = max(segment_index.values()) if segment_index else 0.0
    best = max(hist, current)
    return round(best, 2) if best else None


def _route_points_from_enrichment(enrichment: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(enrichment, dict):
        return []
    prof = enrichment.get("point_profile") or {}
    pts = prof.get("points") or []
    return pts if isinstance(pts, list) else []


def _endpoints_match(
    a_points: list[dict[str, Any]],
    b_points: list[dict[str, Any]],
    tol_m: float | None = None,
    allow_reverse: bool | None = None,
) -> bool:
    tol_m = DUPLICATE_SETTINGS["endpoint_tolerance_m"] if tol_m is None else tol_m
    allow_reverse = DUPLICATE_SETTINGS["allow_reverse_match"] if allow_reverse is None else allow_reverse
    if len(a_points) < 2 or len(b_points) < 2:
        return False
    a0 = (float(a_points[0]["lat"]), float(a_points[0]["lng"]))
    a1 = (float(a_points[-1]["lat"]), float(a_points[-1]["lng"]))
    b0 = (float(b_points[0]["lat"]), float(b_points[0]["lng"]))
    b1 = (float(b_points[-1]["lat"]), float(b_points[-1]["lng"]))
    direct = _haversine_m(a0, b0) <= tol_m and _haversine_m(a1, b1) <= tol_m
    reverse = allow_reverse and _haversine_m(a0, b1) <= tol_m and _haversine_m(a1, b0) <= tol_m
    return direct or reverse


def _find_manual_duplicate(db: Session, user: User, filename: str, candidate: dict[str, Any]) -> tuple[str | None, Route | None]:
    stmt = select(Route)
    routes = list(db.execute(stmt).scalars())
    cand_points = _route_points_from_enrichment(candidate.get("enrichment"))
    cand_dist = float(candidate.get("distance_m") or 0)
    cand_name = filename.lower().strip()
    for route in routes:
        if _is_same_route_candidate(route, candidate):
            return "name", route
    for route in routes:
        raw = route.raw_payload if isinstance(route.raw_payload, dict) else {}
        existing_name = str(raw.get("filename") or "").lower().strip()
        if existing_name and existing_name == cand_name:
            return "filename", route
    for route in routes:
        existing_dist = float(route.distance_m or 0)
        if not cand_dist or not existing_dist:
            continue
        diff_ratio = abs(existing_dist - cand_dist) / max(existing_dist, cand_dist)
        if diff_ratio > (DUPLICATE_SETTINGS["distance_diff_pct"] / 100):
            continue
        existing_points = _route_points_from_enrichment(route.enrichment)
        if _endpoints_match(existing_points, cand_points):
            return "content", route
    return None, None


@app.get("/", response_class=HTMLResponse)
def root(request: Request):
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "app_name": APP_NAME,
            "asset_version": ASSET_VERSION,
            "providers": _provider_meta(),
            "error": request.query_params.get("error"),
            "fresh_login": request.query_params.get("fresh") == "1",
            "admin_username": ADMIN_USERNAME,
            **_csrf_ctx(request),
        },
    )


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return root(request)


@app.post("/login/admin")
def login_admin(request: Request, username: str = Form(...), password: str = Form(...), csrf_token: str = Form(...), db: Session = Depends(get_db)):
    _require_csrf(request, csrf_token)
    locked, retry_after = _admin_login_state(request, username)
    if locked:
        mins = max(1, math.ceil(retry_after / 60))
        msg = f"Troppi tentativi admin falliti. Riprova tra circa {mins} minuto/i"
        _write_audit_log_for_actor(
            db,
            "login_admin_blocked",
            "Admin",
            "Admin locale",
            username,
            error=msg,
            meta={"result": "blocked", "retry_after_sec": retry_after},
            request=request,
        )
        return RedirectResponse(f"/?error={quote(msg)}", status_code=303)
    if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
        _clear_admin_login_failures(request, username)
        request.session.clear()
        request.session["local_admin"] = {"provider": "admin", "label": "Admin locale", "username": username}
        _start_authenticated_session(request)
        _write_audit_log_for_actor(db, "login_admin", "Admin", "Admin locale", username, meta={"result": "success"}, request=request)
        return RedirectResponse("/dashboard", status_code=303)
    retry_after = _register_admin_login_failure(request, username)
    meta = {"result": "failed"}
    error = "Credenziali admin non valide"
    if retry_after:
        mins = max(1, math.ceil(retry_after / 60))
        error = f"Troppi tentativi admin falliti. Riprova tra circa {mins} minuto/i"
        meta = {"result": "blocked", "retry_after_sec": retry_after}
    _write_audit_log_for_actor(
        db,
        "login_admin",
        "Admin",
        "Admin locale",
        username,
        error=error,
        meta=meta,
        request=request,
    )
    return RedirectResponse(f"/?error={quote(error)}", status_code=303)


@app.post("/consent/import")
def import_consent_submit(request: Request, action: str = Form(...), csrf_token: str = Form(...), db: Session = Depends(get_db)):
    _require_csrf(request, csrf_token)
    if _is_admin(request):
        return RedirectResponse("/dashboard", status_code=303)
    user, conn = _get_session_user(request, db)
    if not user or not conn:
        return RedirectResponse("/", status_code=303)
    if action not in {"accept", "decline"}:
        return RedirectResponse("/dashboard", status_code=303)
    request.session["import_consent"] = "accepted" if action == "accept" else "declined"
    request.session["import_autostart"] = action == "accept"
    _write_audit_log(
        db,
        request,
        "import_consent_choice",
        meta={"provider": conn.provider, "choice": request.session["import_consent"]},
    )
    return RedirectResponse("/dashboard", status_code=303)


@app.get("/consent/import")
def import_consent_legacy_redirect():
    return RedirectResponse("/dashboard", status_code=303)


@app.get("/auth/{provider}")
def oauth_start(request: Request, provider: str, db: Session = Depends(get_db)):
    adapter = PROVIDERS.get(provider)
    if not adapter:
        raise HTTPException(status_code=404, detail="Provider non supportato")
    if not adapter.cfg.configured:
        _write_audit_log_for_actor(
            db,
            f"oauth_start_{provider}",
            "Anonimo",
            provider,
            None,
            error=f"Provider {adapter.cfg.label} non configurato",
            request=request,
        )
        return RedirectResponse(f"/?error=Provider {adapter.cfg.label} non configurato", status_code=303)
    state = secrets.token_urlsafe(24)
    request.session[f"oauth_state_{provider}"] = state
    request.session["oauth_fresh_login"] = request.query_params.get("fresh") == "1"
    _write_audit_log_for_actor(db, f"oauth_start_{provider}", "Anonimo", provider, None, meta={"result": "redirect"}, request=request)
    return RedirectResponse(adapter.auth_redirect(state))


@app.get("/auth/adidas/export")
def adidas_export_login(request: Request, db: Session = Depends(get_db)):
    if not _adidas_export_enabled():
        return RedirectResponse("/?error=Export locale adidas non disponibile", status_code=303)
    conn = _adidas_export_connection(db)
    request.session.clear()
    request.session["user_id"] = conn.user_id
    request.session["connection_id"] = conn.id
    request.session["import_consent"] = "pending"
    _start_authenticated_session(request)
    _write_audit_log(db, request, "login_export_adidas", meta={"provider": "adidas", "result": "success"})
    return RedirectResponse("/dashboard", status_code=303)


@app.get("/auth/{provider}/callback")
async def oauth_callback(
    request: Request,
    provider: str,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    adapter = PROVIDERS.get(provider)
    if not adapter:
        raise HTTPException(status_code=404, detail="Provider non supportato")
    if error:
        _write_audit_log_for_actor(db, f"oauth_callback_{provider}", "Anonimo", provider, None, error=str(error), request=request)
        return RedirectResponse(f"/?error=Accesso {adapter.cfg.label} annullato: {error}", status_code=303)
    expected_state = request.session.get(f"oauth_state_{provider}")
    if not state or state != expected_state:
        _write_audit_log_for_actor(db, f"oauth_callback_{provider}", "Anonimo", provider, None, error="Stato OAuth non valido", request=request)
        return RedirectResponse("/?error=Stato OAuth non valido", status_code=303)
    if not code:
        _write_audit_log_for_actor(db, f"oauth_callback_{provider}", "Anonimo", provider, None, error="Codice OAuth mancante", request=request)
        return RedirectResponse("/?error=Codice OAuth mancante", status_code=303)
    try:
        token_data = await adapter.exchange_code(code)
        token = token_data.get("access_token")
        if not token:
            _write_audit_log_for_actor(db, f"oauth_callback_{provider}", "Anonimo", provider, None, error="Token mancante", request=request)
            return RedirectResponse(f"/?error=Token {adapter.cfg.label} mancante", status_code=303)
        profile = token_data.get("athlete") if provider == "strava" else await adapter.fetch_profile(token)
        conn = _upsert_connection(db, adapter, provider, token_data, profile)
    except Exception as exc:
        _write_audit_log_for_actor(db, f"oauth_callback_{provider}", "Anonimo", provider, None, error=str(exc), request=request)
        return RedirectResponse(f"/?error=Errore OAuth {adapter.cfg.label}: {exc}", status_code=303)
    request.session.clear()
    request.session["user_id"] = conn.user_id
    request.session["connection_id"] = conn.id
    request.session["import_consent"] = "pending"
    _start_authenticated_session(request)
    _write_audit_log(
        db,
        request,
        f"login_oauth_{provider}",
        meta={"provider": provider, "result": "success"},
    )
    return RedirectResponse("/dashboard", status_code=303)


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    local_admin = _get_local_admin(request)
    if local_admin:
        ctx = {"provider_key": "admin", "provider_label": local_admin["label"], "is_admin": True, "import_allowed": True, "read_only": False}
        routes = [_serialize_route(r) for r in _all_routes(db)]
        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "app_name": APP_NAME,
                "asset_version": ASSET_VERSION,
                "user": ctx,
                "gpx_status": request.query_params.get("gpx_status"),
                "initial_routes": routes,
                "initial_users": _admin_route_users(db),
                **_csrf_ctx(request),
            },
        )
    user, conn = _get_session_user(request, db)
    if not user or not conn:
        return RedirectResponse("/", status_code=303)
    ctx = _current_provider_context(conn)
    ctx["is_admin"] = False
    ctx["read_only"] = True
    ctx["import_consent"] = "read_only"
    ctx["import_allowed"] = False
    ctx["import_autostart"] = bool(request.session.pop("import_autostart", False))
    routes = [_serialize_route(r) for r in _all_routes(db)]
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "app_name": APP_NAME,
            "asset_version": ASSET_VERSION,
            "user": ctx,
            "gpx_status": request.query_params.get("gpx_status"),
            "initial_routes": routes,
            "initial_users": _admin_route_users(db),
            "import_profile": _provider_import_profile(conn.provider, conn.token_type),
            **_csrf_ctx(request),
        },
    )


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db)):
    local_admin = _get_local_admin(request)
    if not local_admin:
        return RedirectResponse("/dashboard", status_code=303)
    ctx = {"provider_key": "admin", "provider_label": local_admin["label"], "is_admin": True}
    providers = _provider_meta()
    return templates.TemplateResponse(
        "settings.html",
        {"request": request, "app_name": APP_NAME, "asset_version": ASSET_VERSION, "user": ctx, "providers": providers, **_csrf_ctx(request)},
    )


@app.get("/admin/route-types", response_class=HTMLResponse)
def admin_route_types(request: Request, q: str | None = None, db: Session = Depends(get_db)):
    local_admin = _get_local_admin(request)
    if not local_admin:
        return RedirectResponse("/dashboard", status_code=303)
    ctx = {"provider_key": "admin", "provider_label": local_admin["label"], "is_admin": True}
    query = (q or "").strip().lower()
    routes = list(db.execute(select(Route).order_by(Route.start_date_local.desc().nullslast(), Route.id.desc())).scalars())
    if query:
        routes = [
            route for route in routes
            if query in (route.name or "").lower()
            or query in (route.import_user_label or "").lower()
            or query in (route.sport_type or "").lower()
            or query in str(route.id)
        ]
    rows = [
        {
            "id": route.id,
            "name": route.name or "Percorso senza nome",
            "sport_type": route.sport_type or "",
            "import_user_label": route.import_user_label or (route.user.display_name if route.user else "n.d."),
            "start_date_local": route.start_date_local or "n.d.",
            "type_choices": _route_type_choices(route.sport_type),
            "storage_source": _route_storage_source(route),
        }
        for route in routes
    ]
    return templates.TemplateResponse(
        "admin_route_types.html",
        {
            "request": request,
            "app_name": APP_NAME,
            "asset_version": ASSET_VERSION,
            "user": ctx,
            "rows": rows,
            "q": q or "",
            "status": request.query_params.get("status"),
            **_csrf_ctx(request),
        },
    )


@app.get("/admin/logs", response_class=HTMLResponse)
def admin_logs(request: Request, q: str | None = None, limit: int = 500, db: Session = Depends(get_db)):
    local_admin = _get_local_admin(request)
    if not local_admin:
        return RedirectResponse("/dashboard", status_code=303)
    ctx = {"provider_key": "admin", "provider_label": local_admin["label"], "is_admin": True}
    size = min(max(int(limit or 200), 20), 2000)
    query = (q or "").strip().lower()
    logs = list(db.execute(select(AuditLog).order_by(AuditLog.occurred_at.desc(), AuditLog.id.desc()).limit(size)).scalars())
    if query:
        logs = [
            row for row in logs
            if query in (row.action or "").lower()
            or query in (row.actor_label or "").lower()
            or query in (row.error or "").lower()
            or query in _audit_meta_text(row.meta).lower()
            or query in str(row.id)
        ]
    rows = [
        {
            "id": row.id,
            "occurred_at_label": row.occurred_at_label,
            "action": row.action,
            "actor_role": row.actor_role,
            "actor_label": row.actor_label,
            "request_ip": row.request_ip or "",
            "request_method": row.request_method or "",
            "request_path": row.request_path or "",
            "request_user_agent": row.request_user_agent or "",
            "request_forwarded_for": row.request_forwarded_for or "",
            "geo_label": " | ".join(v for v in [row.geo_country, row.geo_region, row.geo_city] if v) or "",
            "geo_org": row.geo_org or "",
            "geo_asn": row.geo_asn or "",
            "geo_coords": (
                f"{row.geo_latitude}, {row.geo_longitude}"
                if row.geo_latitude is not None and row.geo_longitude is not None
                else ""
            ),
            "error": row.error or "",
            "meta_text": _audit_meta_text(row.meta),
        }
        for row in logs
    ]
    return templates.TemplateResponse(
        "admin_logs.html",
        {
            "request": request,
            "app_name": APP_NAME,
            "asset_version": ASSET_VERSION,
            "user": ctx,
            "rows": rows,
            "q": q or "",
            "limit": size,
            **_csrf_ctx(request),
        },
    )


@app.get("/admin/request-debug", response_class=HTMLResponse)
def admin_request_debug(request: Request):
    local_admin = _get_local_admin(request)
    if not local_admin:
        return RedirectResponse("/dashboard", status_code=303)
    ctx = {"provider_key": "admin", "provider_label": local_admin["label"], "is_admin": True}
    payload = _request_debug_payload(request)
    return templates.TemplateResponse(
        "admin_request_debug.html",
        {
            "request": request,
            "app_name": APP_NAME,
            "asset_version": ASSET_VERSION,
            "user": ctx,
            "payload": payload,
            **_csrf_ctx(request),
        },
    )


@app.post("/admin/route-types/{route_id}")
def admin_update_route_type(route_id: int, request: Request, sport_type: str = Form(...), csrf_token: str = Form(...), db: Session = Depends(get_db)):
    _require_csrf(request, csrf_token)
    if not _is_admin(request):
        return RedirectResponse("/dashboard", status_code=303)
    route = db.get(Route, route_id)
    if not route:
        return RedirectResponse(f"/admin/route-types?status={quote('Percorso non trovato')}", status_code=303)
    old_type = route.sport_type or ""
    new_type = sport_type.strip()
    if not new_type:
        return RedirectResponse(f"/admin/route-types?status={quote('Tipologia non valida')}", status_code=303)
    route.sport_type = new_type
    db.commit()
    _write_audit_log(
        db,
        request,
        "update_route_type",
        meta={"route_id": route.id, "route_name": route.name, "old_type": old_type, "new_type": new_type},
    )
    return RedirectResponse(
        f"/admin/route-types?status={quote(f'Tipologia aggiornata per percorso {route.id}')}",
        status_code=303,
    )


@app.get("/routes/{route_id}", response_class=HTMLResponse)
def route_detail(route_id: int, request: Request, db: Session = Depends(get_db)):
    local_admin = _get_local_admin(request)
    if local_admin:
        user_ctx = {"provider_key": "admin", "provider_label": local_admin["label"], "is_admin": True}
    else:
        user, conn = _get_session_user(request, db)
        if not user or not conn:
            return RedirectResponse("/", status_code=303)
        user_ctx = _current_provider_context(conn)
        user_ctx["is_admin"] = False
    route = db.get(Route, route_id)
    if not route:
        raise HTTPException(status_code=404, detail="Percorso non trovato")
    return templates.TemplateResponse(
        "route_detail.html",
        {
            "request": request,
            "app_name": APP_NAME,
            "asset_version": ASSET_VERSION,
            "user": user_ctx,
            "route": _serialize_route(route),
            "viewer_settings": VIEWER_SETTINGS,
            **_csrf_ctx(request),
        },
    )


@app.get("/api/routes")
def api_routes(request: Request, db: Session = Depends(get_db)):
    local_admin = _get_local_admin(request)
    if local_admin:
        routes = [_serialize_route(r) for r in _all_routes(db)]
        return JSONResponse({"routes": routes, "provider": "admin", "users": _admin_route_users(db)})
    user, conn = _get_session_user(request, db)
    if not user or not conn:
        raise HTTPException(status_code=401, detail="Sessione non autenticata")
    routes = [_serialize_route(r) for r in _all_routes(db)]
    return JSONResponse({"routes": routes, "provider": conn.provider, "users": _admin_route_users(db)})


@app.delete("/api/routes/{route_id}")
def api_delete_route(route_id: int, request: Request, db: Session = Depends(get_db)):
    _require_csrf(request)
    local_admin = _get_local_admin(request)
    if not local_admin:
        raise HTTPException(status_code=403, detail="Archivio globale in sola lettura per i login provider")
    route = db.get(Route, route_id)
    if not route:
        raise HTTPException(status_code=404, detail="Percorso non trovato")
    route_name = route.name
    db.delete(route)
    db.commit()
    routes = [_serialize_route(r) for r in _all_routes(db)]
    _write_audit_log(db, request, "delete_route", meta={"route_id": route_id, "route_name": route_name, "total_cached": len(routes)})
    payload: dict[str, Any] = {"deleted": route_id, "total_cached": len(routes), "routes": routes}
    payload["users"] = _admin_route_users(db)
    return JSONResponse(payload)


@app.post("/api/import/{provider}")
async def api_import_provider(
    request: Request,
    provider: str,
    page: int = 1,
    per_page: int = 50,
    db: Session = Depends(get_db),
):
    _require_csrf(request)
    user, conn = _get_session_user(request, db)
    if not user or not conn:
        raise HTTPException(status_code=401, detail="Sessione non autenticata")
    raise HTTPException(status_code=403, detail="Archivio globale in sola lettura per i login provider")
    if conn.provider != provider:
        raise HTTPException(status_code=400, detail="Il provider della sessione non coincide con quello richiesto")
    adapter = PROVIDERS.get(provider)
    if not adapter:
        raise HTTPException(status_code=404, detail="Provider non supportato")
    if provider == "adidas" and conn.token_type == "export":
        routes, imported, skipped_short, skipped_short_names, _ = await _import_adidas_export(db, user, conn)
        _write_audit_log(
            db,
            request,
            "import_provider_adidas_export",
            meta={
                "imported": imported,
                "skipped_short": skipped_short,
                "total_cached": len(routes),
            },
        )
        return JSONResponse(_import_payload(provider, routes, imported, skipped_short, skipped_short_names))
    if not adapter.cfg.activities_url:
        raise HTTPException(status_code=400, detail=f"Endpoint attività {adapter.cfg.label} non configurato")
    token = await _refresh_connection_token(adapter, conn, db)
    try:
        raw = await adapter.fetch_activities(token, conn, page, per_page)
    except Exception as exc:
        _write_audit_log(db, request, f"import_provider_{provider}", error=str(exc), meta={"page": page, "per_page": per_page})
        raise HTTPException(status_code=400, detail=f"Errore import {adapter.cfg.label}: {exc}") from exc
    items = []
    skipped_short = 0
    skipped_short_names: list[str] = []
    for entry in raw:
        if not entry:
            continue
        norm = adapter.normalize_activity(entry)
        if not norm.get("external_id"):
            continue
        route_exists = db.execute(
            select(Route.id).where(Route.provider == provider, Route.external_id == str(norm["external_id"]))
        ).scalar_one_or_none()
        if not route_exists and _find_duplicate_route(db, _normalize_for_storage(provider, norm), provider=provider):
            continue
        if float(norm.get("distance_m") or 0) < MIN_IMPORT_DISTANCE_M:
            skipped_short += 1
            skipped_short_names.append(norm.get("name") or str(norm.get("external_id")))
            continue
        items.append(await _enrich_route_data(db, _normalize_for_storage(provider, norm)))
    routes = _store_routes(db, user, conn, items)
    _write_audit_log(
        db,
        request,
        f"import_provider_{provider}",
        meta={
            "page": page,
            "per_page": per_page,
            "imported": len(items),
            "skipped_short": skipped_short,
            "total_cached": len(routes),
        },
    )
    return JSONResponse(
        _import_payload(provider, routes, len(items), skipped_short, skipped_short_names)
    )


@app.post("/api/import/{provider}/start")
async def api_import_provider_start(request: Request, provider: str, db: Session = Depends(get_db)):
    _require_csrf(request)
    user, conn = _get_session_user(request, db)
    if not user or not conn:
        raise HTTPException(status_code=401, detail="Sessione non autenticata")
    raise HTTPException(status_code=403, detail="Archivio globale in sola lettura per i login provider")
    if conn.provider != provider:
        raise HTTPException(status_code=400, detail="Il provider della sessione non coincide con quello richiesto")
    total = len(_adidas_export_bundles()) if provider == "adidas" and conn.token_type == "export" else 0
    job_id = uuid.uuid4().hex
    IMPORT_JOBS[job_id] = {
        "job_id": job_id,
        "provider": provider,
        "status": "running",
        "done": 0,
        "total": total,
        "imported": 0,
        "current": None,
        "result": None,
        "error": None,
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
    }
    if provider == "adidas" and conn.token_type == "export":
        asyncio.create_task(_run_adidas_import_job(job_id, user.id, conn.id))
    else:
        asyncio.create_task(_run_provider_import_job(job_id, user.id, conn.id, provider))
    return JSONResponse({"mode": "async", "job_id": job_id, "done": 0, "imported": 0, "total": total})


@app.get("/api/import-jobs/{job_id}")
def api_import_job_status(job_id: str):
    job = IMPORT_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job import non trovato")
    return JSONResponse(job)


@app.post("/api/manual-import/gpx")
async def api_import_gpx(request: Request, csrf_token: str = Form(...), files: list[UploadFile] = File(...), db: Session = Depends(get_db)):
    _require_csrf(request, csrf_token)
    if not _is_admin(request):
        raise HTTPException(status_code=403, detail="Accesso admin richiesto")
    user, conn = _get_or_create_admin_entities(db)
    imported_items: list[dict[str, Any]] = []
    skipped_filename = 0
    skipped_content = 0
    skipped_short = 0
    skipped_wrong_type = 0
    skipped_short_names: list[str] = []
    skipped_wrong_type_names: list[str] = []
    bundles: dict[str, dict[str, tuple[str, bytes]]] = {}
    for f in files:
        if not f.filename:
            continue
        filename = f.filename
        ext = Path(filename).suffix.lower()
        if ext not in {".gpx", ".json"}:
            continue
        stem = Path(filename).stem
        bundles.setdefault(stem, {})[ext] = (filename, await f.read())
    processed_bundles = 0
    for bundle in bundles.values():
        if ".gpx" not in bundle:
            continue
        processed_bundles += 1
        gpx_name, gpx_raw = bundle[".gpx"]
        sidecar_raw = bundle.get(".json", (None, None))[1]
        parsed = parse_gpx_bytes(gpx_raw, gpx_name, sidecar_raw=sidecar_raw)
        sport_type = str(parsed.get("sport_type") or "").strip()
        if not _is_allowed_gpx_sport_type(sport_type):
            skipped_wrong_type += 1
            skipped_wrong_type_names.append(f"{gpx_name} [{sport_type or 'unknown'}]")
            continue
        if float(parsed.get("distance_m") or 0) < MIN_IMPORT_DISTANCE_M:
            skipped_short += 1
            skipped_short_names.append(parsed.get("name") or gpx_name)
            continue
        norm = _normalize_for_storage("manual", parsed)
        dup_kind, _ = _find_manual_duplicate(db, user, gpx_name, norm)
        if dup_kind == "filename":
            skipped_filename += 1
            continue
        if dup_kind == "content":
            skipped_content += 1
            continue
        norm = await _enrich_route_data(db, norm)
        norm["external_id"] = f'gpx:{gpx_name}:{int(datetime.now(UTC).timestamp())}:{len(imported_items)}'
        imported_items.append(norm)
    routes = _store_routes(db, user, conn, imported_items)
    _write_audit_log(
        db,
        request,
        "import_gpx",
        meta={
            "files_received": len(files),
            "processed_bundles": processed_bundles,
            "imported": len(imported_items),
            "skipped_filename": skipped_filename,
            "skipped_content": skipped_content,
            "skipped_short": skipped_short,
            "skipped_wrong_type": skipped_wrong_type,
            "skipped_wrong_type_names": skipped_wrong_type_names[:10],
            "total_cached": len(routes),
        },
    )
    return JSONResponse(
        {
            "imported": len(imported_items),
            "files_received": len(files),
            "processed_bundles": processed_bundles,
            "skipped_filename": skipped_filename,
            "skipped_content": skipped_content,
            "skipped_short": skipped_short,
            "skipped_short_names": skipped_short_names,
            "skipped_wrong_type": skipped_wrong_type,
            "skipped_wrong_type_names": skipped_wrong_type_names,
            "total_cached": len(routes),
            "provider": "manual",
            "routes": [_serialize_route(r) for r in routes],
        }
    )


@app.post("/dashboard/import-gpx")
async def dashboard_import_gpx(request: Request, csrf_token: str = Form(...), files: list[UploadFile] = File(...), db: Session = Depends(get_db)):
    _require_csrf(request, csrf_token)
    if not _is_admin(request):
        return RedirectResponse(f"/dashboard?gpx_status={quote('Accesso admin richiesto')}", status_code=303)
    try:
        payload = await api_import_gpx(request, csrf_token=csrf_token, files=files, db=db)
        data = payload.body.decode("utf-8")
        body = json.loads(data)
        short_names = body.get("skipped_short_names", [])
        short_msg = f", distanza<{int(MIN_IMPORT_DISTANCE_M)}m={body.get('skipped_short', 0)}"
        if short_names:
            short_msg += f" ({', '.join(short_names[:3])})"
        wrong_type_names = body.get("skipped_wrong_type_names", [])
        wrong_type_msg = f", tipo_attivita_non_valido={body.get('skipped_wrong_type', 0)}"
        if wrong_type_names:
            wrong_type_msg += f" ({', '.join(wrong_type_names[:3])})"
        msg = (
            f'Import completato: {body.get("imported", 0)} percorsi da {body.get("processed_bundles", 0)} bundle GPX/JSON, '
            f'scartati nome={body.get("skipped_filename", 0)}, contenuto={body.get("skipped_content", 0)}{short_msg}{wrong_type_msg}, '
            f'totale cache {body.get("total_cached", 0)}'
        )
        return RedirectResponse(f"/dashboard?gpx_status={quote(msg)}", status_code=303)
    except Exception as exc:
        _write_audit_log(db, request, "import_gpx", error=str(exc))
        return RedirectResponse(f"/dashboard?gpx_status={quote(f'Errore import GPX: {exc}')}", status_code=303)


@app.post("/logout")
async def logout(request: Request, csrf_token: str = Form(...), db: Session = Depends(get_db)):
    _require_csrf(request, csrf_token)
    revoke_error = None
    if not _is_admin(request):
        user, conn = _get_session_user(request, db)
        if user and conn and conn.token_type != "export":
            adapter = PROVIDERS.get(conn.provider)
            if adapter:
                try:
                    await adapter.revoke_session(conn)
                except Exception as exc:
                    revoke_error = str(exc)
    _write_audit_log(db, request, "logout", error=revoke_error if revoke_error else None)
    request.session.clear()
    return RedirectResponse("/?fresh=1", status_code=303)


@app.get("/api/status")
def api_status():
    return JSONResponse(
        {
            "status": "ok",
            "providers": _provider_meta(),
            "difficulty_weights": BASE_SKATE_WEIGHTS,
            "difficulty_weights_locked": True,
            "duplicate_settings": DUPLICATE_SETTINGS,
            "slope_settings": SLOPE_SETTINGS,
            "viewer_settings": VIEWER_SETTINGS,
        }
    )


@app.get("/api/admin/audit-logs")
def api_admin_audit_logs(request: Request, limit: int = 200, db: Session = Depends(get_db)):
    if not _is_admin(request):
        raise HTTPException(status_code=403, detail="Accesso admin richiesto")
    limit = max(1, min(int(limit), 1000))
    stmt = select(AuditLog).order_by(AuditLog.occurred_at.desc(), AuditLog.id.desc()).limit(limit)
    logs = [_serialize_audit_log(row) for row in db.execute(stmt).scalars()]
    return JSONResponse({"logs": logs, "count": len(logs), "limit": limit})


@app.post("/api/admin/provider-button-settings")
def api_admin_provider_button_settings(request: Request, payload: dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    _require_csrf(request)
    if not _is_admin(request):
        raise HTTPException(status_code=403, detail="Accesso admin richiesto")
    if set(payload.keys()) != set(PROVIDERS.keys()):
        raise HTTPException(status_code=400, detail="Provider non valido")
    next_settings: dict[str, dict[str, bool]] = {}
    for key in PROVIDERS:
        row = payload.get(key)
        if not isinstance(row, dict):
            raise HTTPException(status_code=400, detail=f"Configurazione provider {key} non valida")
        next_settings[key] = {
            "visible": bool(row.get("visible")),
            "enabled": bool(row.get("enabled")),
        }
    PROVIDER_BUTTON_SETTINGS.clear()
    PROVIDER_BUTTON_SETTINGS.update(next_settings)
    _write_audit_log(db, request, "save_provider_button_settings", meta={"provider_button_settings": next_settings})
    return JSONResponse({"status": "ok", "providers": _provider_meta()})


@app.post("/api/admin/difficulty-weights")
def api_admin_difficulty_weights(request: Request, payload: dict[str, int] = Body(...), db: Session = Depends(get_db)):
    _require_csrf(request)
    if not _is_admin(request):
        raise HTTPException(status_code=403, detail="Accesso admin richiesto")
    raise HTTPException(status_code=400, detail="I pesi difficoltà sono fissi nell'algoritmo base")


@app.post("/api/admin/duplicate-settings")
def api_admin_duplicate_settings(request: Request, payload: dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    _require_csrf(request)
    if not _is_admin(request):
        raise HTTPException(status_code=403, detail="Accesso admin richiesto")
    keys = {"distance_diff_pct", "endpoint_tolerance_m", "allow_reverse_match"}
    if set(payload.keys()) != keys:
        raise HTTPException(status_code=400, detail="Payload deduplica non valido")
    distance_diff_pct = float(payload["distance_diff_pct"])
    endpoint_tolerance_m = float(payload["endpoint_tolerance_m"])
    allow_reverse_match = bool(payload["allow_reverse_match"])
    if distance_diff_pct < 0 or distance_diff_pct > 100:
        raise HTTPException(status_code=400, detail="distance_diff_pct non valido")
    if endpoint_tolerance_m < 0 or endpoint_tolerance_m > 5000:
        raise HTTPException(status_code=400, detail="endpoint_tolerance_m non valido")
    DUPLICATE_SETTINGS.update(
        {
            "distance_diff_pct": distance_diff_pct,
            "endpoint_tolerance_m": endpoint_tolerance_m,
            "allow_reverse_match": allow_reverse_match,
        }
    )
    _write_audit_log(db, request, "save_duplicate_settings", meta={"duplicate_settings": DUPLICATE_SETTINGS.copy()})
    return JSONResponse({"status": "ok", "duplicate_settings": DUPLICATE_SETTINGS})


@app.post("/api/admin/viewer-settings")
def api_admin_viewer_settings(request: Request, payload: dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    _require_csrf(request)
    if not _is_admin(request):
        raise HTTPException(status_code=403, detail="Accesso admin richiesto")
    keys = {"show_direction_arrows"}
    if set(payload.keys()) != keys:
        raise HTTPException(status_code=400, detail="Payload viewer non valido")
    VIEWER_SETTINGS["show_direction_arrows"] = bool(payload["show_direction_arrows"])
    _write_audit_log(db, request, "save_viewer_settings", meta={"viewer_settings": VIEWER_SETTINGS.copy()})
    return JSONResponse({"status": "ok", "viewer_settings": VIEWER_SETTINGS})


@app.post("/api/admin/slope-settings")
def api_admin_slope_settings(request: Request, payload: dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    _require_csrf(request)
    if not _is_admin(request):
        raise HTTPException(status_code=403, detail="Accesso admin richiesto")
    keys = {"smoothing_window", "min_run_distance_m", "max_cap_pct"}
    if set(payload.keys()) != keys:
        raise HTTPException(status_code=400, detail="Payload pendenze non valido")
    smoothing_window = int(payload["smoothing_window"])
    min_run_distance_m = float(payload["min_run_distance_m"])
    max_cap_pct = float(payload["max_cap_pct"])
    if smoothing_window < 1 or smoothing_window > 21:
        raise HTTPException(status_code=400, detail="smoothing_window non valido")
    if min_run_distance_m < 1 or min_run_distance_m > 500:
        raise HTTPException(status_code=400, detail="min_run_distance_m non valido")
    if max_cap_pct < 1 or max_cap_pct > 100:
        raise HTTPException(status_code=400, detail="max_cap_pct non valido")
    SLOPE_SETTINGS.update(
        {
            "smoothing_window": smoothing_window,
            "min_run_distance_m": min_run_distance_m,
            "max_cap_pct": max_cap_pct,
        }
    )
    _write_audit_log(db, request, "save_slope_settings", meta={"slope_settings": SLOPE_SETTINGS.copy()})
    return JSONResponse({"status": "ok", "slope_settings": SLOPE_SETTINGS})
