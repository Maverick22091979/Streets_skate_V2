import base64
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import httpx

from app.config import env_value
from app.models import AuthConnection


def _to_dt(expires_at: int | float | None = None, expires_in: int | float | None = None) -> datetime | None:
    if expires_at:
        return datetime.fromtimestamp(float(expires_at), tz=UTC)
    if expires_in:
        return datetime.fromtimestamp(time.time() + float(expires_in), tz=UTC)
    return None


@dataclass
class ProviderConfig:
    key: str
    label: str
    client_id: str
    client_secret: str
    redirect_uri: str
    auth_url: str
    token_url: str
    scope: str = ""
    user_url: str = ""
    activities_url: str = ""
    revoke_url: str = ""
    extra: dict[str, Any] | None = None

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret and self.auth_url and self.token_url)


class ProviderAdapter:
    def __init__(self, cfg: ProviderConfig):
        self.cfg = cfg

    def auth_params(self, state: str) -> dict[str, Any]:
        p = {
            "client_id": self.cfg.client_id,
            "redirect_uri": self.cfg.redirect_uri,
            "response_type": "code",
            "state": state,
        }
        if self.cfg.scope:
            p["scope"] = self.cfg.scope
        return p

    def auth_redirect(self, state: str) -> str:
        return f"{self.cfg.auth_url}?{urlencode(self.auth_params(state))}"

    def token_headers(self) -> dict[str, str]:
        return {}

    def api_headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def token_payload(self, code: str) -> dict[str, Any]:
        return {
            "client_id": self.cfg.client_id,
            "client_secret": self.cfg.client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": self.cfg.redirect_uri,
        }

    def refresh_payload(self, refresh_token: str) -> dict[str, Any]:
        return {
            "client_id": self.cfg.client_id,
            "client_secret": self.cfg.client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }

    async def exchange_code(self, code: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(self.cfg.token_url, data=self.token_payload(code), headers=self.token_headers())
        r.raise_for_status()
        return r.json()

    async def refresh_token(self, refresh_token: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(self.cfg.token_url, data=self.refresh_payload(refresh_token), headers=self.token_headers())
        r.raise_for_status()
        return r.json()

    async def fetch_profile(self, token: str) -> dict[str, Any]:
        if not self.cfg.user_url:
            return {}
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(self.cfg.user_url, headers=self.api_headers(token))
        r.raise_for_status()
        return r.json()

    async def fetch_activities(self, token: str, connection: AuthConnection, page: int, per_page: int) -> list[dict[str, Any]]:
        raise NotImplementedError

    async def revoke_session(self, connection: AuthConnection) -> None:
        return None

    def connection_identity(self, token_data: dict[str, Any], profile: dict[str, Any]) -> tuple[str, str | None, str | None]:
        raise NotImplementedError

    def connection_tokens(self, token_data: dict[str, Any], current_refresh_token: str | None = None) -> dict[str, Any]:
        return {
            "access_token": token_data.get("access_token"),
            "refresh_token": token_data.get("refresh_token", current_refresh_token),
            "token_type": token_data.get("token_type"),
            "scope": token_data.get("scope"),
            "expires_at": _to_dt(token_data.get("expires_at"), token_data.get("expires_in")),
        }

    def normalize_activity(self, activity: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError


class StravaAdapter(ProviderAdapter):
    def auth_params(self, state: str) -> dict[str, Any]:
        p = super().auth_params(state)
        p["approval_prompt"] = env_value("STRAVA_APPROVAL_PROMPT", "force")
        return p

    async def fetch_activities(self, token: str, connection: AuthConnection, page: int, per_page: int) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(
                self.cfg.activities_url,
                headers=self.api_headers(token),
                params={"page": page, "per_page": min(max(per_page, 1), 100)},
            )
        r.raise_for_status()
        return r.json()

    def connection_identity(self, token_data: dict[str, Any], profile: dict[str, Any]) -> tuple[str, str | None, str | None]:
        athlete = token_data.get("athlete") or profile or {}
        pid = athlete.get("id")
        if pid is None:
            raise ValueError("Strava athlete id mancante")
        name = " ".join(v for v in [athlete.get("firstname"), athlete.get("lastname")] if v).strip() or athlete.get("username")
        return str(pid), athlete.get("username"), name or "Utente Strava"

    def normalize_activity(self, activity: dict[str, Any]) -> dict[str, Any]:
        return {
            "external_id": str(activity.get("id")),
            "name": activity.get("name"),
            "sport_type": activity.get("type") or activity.get("sport_type"),
            "start_date_local": activity.get("start_date_local"),
            "distance_m": float(activity.get("distance") or 0),
            "elevation_gain_m": float(activity.get("total_elevation_gain") or 0),
            "average_speed_ms": float(activity.get("average_speed") or 0),
            "moving_time_sec": activity.get("moving_time"),
            "summary_polyline": ((activity.get("map") or {}).get("summary_polyline") or ""),
            "raw_payload": activity,
        }

    async def revoke_session(self, connection: AuthConnection) -> None:
        if not self.cfg.revoke_url:
            return
        token = connection.refresh_token or connection.access_token
        if not token:
            return
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(
                self.cfg.revoke_url,
                data={"token": token, "token_type_hint": "refresh_token" if connection.refresh_token else "access_token"},
                auth=(self.cfg.client_id, self.cfg.client_secret),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        r.raise_for_status()


class MapMyRunAdapter(ProviderAdapter):
    def token_headers(self) -> dict[str, str]:
        return {"Api-Key": self.cfg.client_id}

    def api_headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}", "Api-Key": self.cfg.client_id}

    async def fetch_activities(self, token: str, connection: AuthConnection, page: int, per_page: int) -> list[dict[str, Any]]:
        params = {
            "user": connection.provider_user_id,
            "field_set": "time_series",
            "page": page,
            "limit": min(max(per_page, 1), 100),
        }
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(self.cfg.activities_url, headers=self.api_headers(token), params=params)
        r.raise_for_status()
        body = r.json()
        return body.get("_embedded", {}).get("workouts", [])

    def connection_identity(self, token_data: dict[str, Any], profile: dict[str, Any]) -> tuple[str, str | None, str | None]:
        pid = profile.get("id") or profile.get("_links", {}).get("self", [{}])[0].get("id")
        if pid is None:
            raise ValueError("MapMyRun user id mancante")
        username = profile.get("username")
        name = profile.get("display_name") or profile.get("first_name") or username or "Utente MapMyRun"
        return str(pid), username, name

    def normalize_activity(self, activity: dict[str, Any]) -> dict[str, Any]:
        agg = activity.get("aggregates") or {}
        pos = ((activity.get("time_series") or {}).get("position") or [])
        pts = [item[1] for item in pos if isinstance(item, list) and len(item) > 1 and isinstance(item[1], dict)]
        return {
            "external_id": str(activity.get("_links", {}).get("self", [{}])[0].get("id") or activity.get("id")),
            "name": activity.get("name"),
            "sport_type": activity.get("activity_type"),
            "start_date_local": activity.get("start_datetime"),
            "distance_m": float(agg.get("distance_total") or 0),
            "elevation_gain_m": float(agg.get("elevation_gain_total") or 0),
            "average_speed_ms": float(agg.get("speed_avg") or 0),
            "moving_time_sec": int(agg.get("active_time_total") or 0) if agg.get("active_time_total") is not None else None,
            "summary_polyline": "",
            "points": pts,
            "raw_payload": activity,
        }

    async def revoke_session(self, connection: AuthConnection) -> None:
        if not self.cfg.revoke_url or not connection.provider_user_id or not self.cfg.client_id:
            return
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.delete(
                self.cfg.revoke_url,
                headers=self.api_headers(connection.access_token),
                params={"user_id": connection.provider_user_id, "client_id": self.cfg.client_id},
            )
        if r.status_code not in {200, 202, 204}:
            r.raise_for_status()


class AdidasAdapter(ProviderAdapter):
    async def fetch_activities(self, token: str, connection: AuthConnection, page: int, per_page: int) -> list[dict[str, Any]]:
        params = {"page": page, "limit": min(max(per_page, 1), 100)}
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(self.cfg.activities_url, headers=self.api_headers(token), params=params)
        r.raise_for_status()
        body = r.json()
        if isinstance(body, list):
            return body
        for k in ("activities", "items", "data"):
            if isinstance(body.get(k), list):
                return body[k]
        return []

    def connection_identity(self, token_data: dict[str, Any], profile: dict[str, Any]) -> tuple[str, str | None, str | None]:
        pid = profile.get("id") or profile.get("user_id") or profile.get("uuid")
        if pid is None:
            raise ValueError("adidas Running user id mancante")
        username = profile.get("username") or profile.get("email")
        name = profile.get("name") or profile.get("full_name") or username or "Utente adidas Running"
        return str(pid), username, name

    def normalize_activity(self, activity: dict[str, Any]) -> dict[str, Any]:
        return {
            "external_id": str(activity.get("id") or activity.get("activity_id")),
            "name": activity.get("name") or activity.get("title"),
            "sport_type": activity.get("sport") or activity.get("type"),
            "start_date_local": activity.get("start_date_local") or activity.get("start_time"),
            "distance_m": float(activity.get("distance") or activity.get("distance_m") or 0),
            "elevation_gain_m": float(activity.get("elevation_gain") or activity.get("elevation_gain_m") or 0),
            "average_speed_ms": float(activity.get("average_speed") or activity.get("avg_speed") or 0),
            "moving_time_sec": activity.get("moving_time") or activity.get("duration"),
            "summary_polyline": activity.get("summary_polyline") or "",
            "raw_payload": activity,
        }


def _decode_jwt_payload(token: str | None) -> dict[str, Any]:
    raw = (token or "").strip()
    parts = raw.split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1]
    padding = "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload + padding).decode("utf-8")
        body = json.loads(decoded)
        return body if isinstance(body, dict) else {}
    except Exception:
        return {}


class SuuntoAdapter(ProviderAdapter):
    def token_headers(self) -> dict[str, str]:
        auth = base64.b64encode(f"{self.cfg.client_id}:{self.cfg.client_secret}".encode("utf-8")).decode("ascii")
        return {
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/x-www-form-urlencoded",
        }

    def token_payload(self, code: str) -> dict[str, Any]:
        return {
            "grant_type": "authorization_code",
            "redirect_uri": self.cfg.redirect_uri,
            "code": code,
        }

    def refresh_payload(self, refresh_token: str) -> dict[str, Any]:
        return {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }

    def api_headers(self, token: str) -> dict[str, str]:
        headers = super().api_headers(token)
        sub_key = (self.cfg.extra or {}).get("subscription_key")
        if sub_key:
            headers["Ocp-Apim-Subscription-Key"] = str(sub_key)
        return headers

    async def fetch_profile(self, token: str) -> dict[str, Any]:
        return _decode_jwt_payload(token)

    async def fetch_activities(self, token: str, connection: AuthConnection, page: int, per_page: int) -> list[dict[str, Any]]:
        params = {"offset": max(page - 1, 0) * min(max(per_page, 1), 100), "limit": min(max(per_page, 1), 100)}
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(self.cfg.activities_url, headers=self.api_headers(token), params=params)
        r.raise_for_status()
        body = r.json()
        if isinstance(body, list):
            return body
        for k in ("workouts", "items", "data"):
            if isinstance(body.get(k), list):
                return body[k]
        return []

    def connection_identity(self, token_data: dict[str, Any], profile: dict[str, Any]) -> tuple[str, str | None, str | None]:
        claims = profile or _decode_jwt_payload(token_data.get("access_token"))
        username = claims.get("user") or claims.get("username") or claims.get("sub")
        pid = claims.get("sub") or username
        if pid is None:
            raise ValueError("Suunto user id mancante")
        return str(pid), str(username) if username else None, str(username or "Utente Suunto")

    def normalize_activity(self, activity: dict[str, Any]) -> dict[str, Any]:
        return {
            "external_id": str(activity.get("id") or activity.get("workoutId") or activity.get("activityId") or ""),
            "name": activity.get("description") or activity.get("name") or activity.get("title"),
            "sport_type": activity.get("sport") or activity.get("activityType") or activity.get("type"),
            "start_date_local": activity.get("startTime") or activity.get("startDateLocal") or activity.get("start_time"),
            "distance_m": float(activity.get("distance") or activity.get("distance_m") or 0),
            "elevation_gain_m": float(activity.get("ascent") or activity.get("elevationGain") or activity.get("elevation_gain_m") or 0),
            "average_speed_ms": float(activity.get("speedAvg") or activity.get("average_speed") or activity.get("avg_speed") or 0),
            "moving_time_sec": activity.get("duration") or activity.get("moving_time") or activity.get("durationInSeconds"),
            "summary_polyline": activity.get("summaryPolyline") or activity.get("summary_polyline") or "",
            "raw_payload": activity,
        }


class RunkeeperAdapter(ProviderAdapter):
    async def fetch_activities(self, token: str, connection: AuthConnection, page: int, per_page: int) -> list[dict[str, Any]]:
        params = {"page": page, "limit": min(max(per_page, 1), 100)}
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(self.cfg.activities_url, headers=self.api_headers(token), params=params)
        r.raise_for_status()
        body = r.json()
        if isinstance(body, list):
            return body
        for k in ("items", "activities", "data"):
            if isinstance(body.get(k), list):
                return body[k]
        return []

    def connection_identity(self, token_data: dict[str, Any], profile: dict[str, Any]) -> tuple[str, str | None, str | None]:
        pid = profile.get("userID") or profile.get("id") or profile.get("uri")
        if pid is None:
            raise ValueError("Runkeeper user id mancante")
        username = profile.get("name") or profile.get("username")
        return str(pid), str(username) if username else None, str(username or "Utente Runkeeper")

    def normalize_activity(self, activity: dict[str, Any]) -> dict[str, Any]:
        return {
            "external_id": str(activity.get("uri") or activity.get("id") or ""),
            "name": activity.get("notes") or activity.get("name") or activity.get("title"),
            "sport_type": activity.get("type") or activity.get("sport") or activity.get("activityType"),
            "start_date_local": activity.get("start_time") or activity.get("startTime"),
            "distance_m": float(activity.get("total_distance") or activity.get("distance") or 0),
            "elevation_gain_m": float(activity.get("climb") or activity.get("elevation_gain") or 0),
            "average_speed_ms": float(activity.get("average_speed") or activity.get("avg_speed") or 0),
            "moving_time_sec": activity.get("duration") or activity.get("moving_time"),
            "summary_polyline": activity.get("summary_polyline") or "",
            "raw_payload": activity,
        }


class GarminAdapter(ProviderAdapter):
    async def fetch_activities(self, token: str, connection: AuthConnection, page: int, per_page: int) -> list[dict[str, Any]]:
        params = {"page": page, "limit": min(max(per_page, 1), 100)}
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(self.cfg.activities_url, headers=self.api_headers(token), params=params)
        r.raise_for_status()
        body = r.json()
        if isinstance(body, list):
            return body
        for k in ("activities", "items", "data"):
            if isinstance(body.get(k), list):
                return body[k]
        return []

    def connection_identity(self, token_data: dict[str, Any], profile: dict[str, Any]) -> tuple[str, str | None, str | None]:
        pid = profile.get("userId") or profile.get("id") or profile.get("uuid")
        if pid is None:
            raise ValueError("Garmin user id mancante")
        username = profile.get("username") or profile.get("email")
        name = profile.get("displayName") or profile.get("fullName") or username or "Utente Garmin"
        return str(pid), username, name

    def normalize_activity(self, activity: dict[str, Any]) -> dict[str, Any]:
        return {
            "external_id": str(activity.get("activityId") or activity.get("id")),
            "name": activity.get("activityName") or activity.get("name") or activity.get("title"),
            "sport_type": activity.get("activityType") or activity.get("sport") or activity.get("type"),
            "start_date_local": activity.get("startTimeLocal") or activity.get("startDateLocal") or activity.get("start_time"),
            "distance_m": float(activity.get("distanceInMeters") or activity.get("distance") or activity.get("distance_m") or 0),
            "elevation_gain_m": float(activity.get("elevationGainInMeters") or activity.get("elevation_gain") or activity.get("elevation_gain_m") or 0),
            "average_speed_ms": float(activity.get("averageSpeedInMetersPerSecond") or activity.get("average_speed") or activity.get("avg_speed") or 0),
            "moving_time_sec": activity.get("durationInSeconds") or activity.get("moving_time") or activity.get("duration"),
            "summary_polyline": activity.get("summaryPolyline") or activity.get("summary_polyline") or "",
            "raw_payload": activity,
        }


class InlineRouteTrackingAdapter(ProviderAdapter):
    async def fetch_activities(self, token: str, connection: AuthConnection, page: int, per_page: int) -> list[dict[str, Any]]:
        params = {"page": page, "limit": min(max(per_page, 1), 100)}
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(self.cfg.activities_url, headers=self.api_headers(token), params=params)
        r.raise_for_status()
        body = r.json()
        if isinstance(body, list):
            return body
        for k in ("activities", "items", "data", "routes"):
            if isinstance(body.get(k), list):
                return body[k]
        return []

    def connection_identity(self, token_data: dict[str, Any], profile: dict[str, Any]) -> tuple[str, str | None, str | None]:
        pid = profile.get("userId") or profile.get("id") or profile.get("uuid")
        if pid is None:
            raise ValueError("Inline Route Tracking user id mancante")
        username = profile.get("username") or profile.get("email")
        name = profile.get("displayName") or profile.get("fullName") or username or "Utente Inline Route Tracking"
        return str(pid), username, name

    def normalize_activity(self, activity: dict[str, Any]) -> dict[str, Any]:
        return {
            "external_id": str(activity.get("routeId") or activity.get("activityId") or activity.get("id")),
            "name": activity.get("routeName") or activity.get("activityName") or activity.get("name") or activity.get("title"),
            "sport_type": activity.get("activityType") or activity.get("sport") or activity.get("type"),
            "start_date_local": activity.get("startTimeLocal") or activity.get("startDateLocal") or activity.get("start_time"),
            "distance_m": float(activity.get("distanceInMeters") or activity.get("distance") or activity.get("distance_m") or 0),
            "elevation_gain_m": float(activity.get("elevationGainInMeters") or activity.get("elevation_gain") or activity.get("elevation_gain_m") or 0),
            "average_speed_ms": float(activity.get("averageSpeedInMetersPerSecond") or activity.get("average_speed") or activity.get("avg_speed") or 0),
            "moving_time_sec": activity.get("durationInSeconds") or activity.get("moving_time") or activity.get("duration"),
            "summary_polyline": activity.get("summaryPolyline") or activity.get("summary_polyline") or "",
            "raw_payload": activity,
        }


def build_provider_registry(base_url: str) -> dict[str, ProviderAdapter]:
    cfgs = {
        "strava": ProviderConfig(
            key="strava",
            label="Strava",
            client_id=env_value("STRAVA_CLIENT_ID", ""),
            client_secret=env_value("STRAVA_CLIENT_SECRET", ""),
            redirect_uri=env_value("STRAVA_REDIRECT_URI", f"{base_url}/auth/strava/callback"),
            auth_url=env_value("STRAVA_AUTHORIZE_URL", "https://www.strava.com/oauth/authorize"),
            token_url=env_value("STRAVA_TOKEN_URL", "https://www.strava.com/oauth/token"),
            scope=env_value("STRAVA_SCOPE", "read,activity:read_all,profile:read_all"),
            user_url=env_value("STRAVA_USER_URL", "https://www.strava.com/api/v3/athlete"),
            activities_url=env_value("STRAVA_ACTIVITIES_URL", "https://www.strava.com/api/v3/athlete/activities"),
            revoke_url=env_value("STRAVA_REVOKE_URL", "https://www.strava.com/oauth/revoke"),
        ),
        "adidas": ProviderConfig(
            key="adidas",
            label="adidas Running",
            client_id=env_value("ADIDAS_CLIENT_ID", ""),
            client_secret=env_value("ADIDAS_CLIENT_SECRET", ""),
            redirect_uri=env_value("ADIDAS_REDIRECT_URI", f"{base_url}/auth/adidas/callback"),
            auth_url=env_value("ADIDAS_AUTH_URL", ""),
            token_url=env_value("ADIDAS_TOKEN_URL", ""),
            scope=env_value("ADIDAS_SCOPE", ""),
            user_url=env_value("ADIDAS_USER_URL", ""),
            activities_url=env_value("ADIDAS_ACTIVITIES_URL", ""),
            revoke_url=env_value("ADIDAS_REVOKE_URL", ""),
        ),
        "mapmyrun": ProviderConfig(
            key="mapmyrun",
            label="MapMyRun",
            client_id=env_value("MAPMYRUN_CLIENT_ID", ""),
            client_secret=env_value("MAPMYRUN_CLIENT_SECRET", ""),
            redirect_uri=env_value("MAPMYRUN_REDIRECT_URI", f"{base_url}/auth/mapmyrun/callback"),
            auth_url=env_value("MAPMYRUN_AUTH_URL", "https://www.mapmyfitness.com/oauth2/authorize/"),
            token_url=env_value("MAPMYRUN_TOKEN_URL", "https://api.mapmyfitness.com/v7.1/oauth2/access_token/"),
            scope=env_value("MAPMYRUN_SCOPE", ""),
            user_url=env_value("MAPMYRUN_USER_URL", "https://api.mapmyfitness.com/v7.1/user/self/"),
            activities_url=env_value("MAPMYRUN_ACTIVITIES_URL", "https://api.mapmyfitness.com/v7.1/workout/"),
            revoke_url=env_value("MAPMYRUN_REVOKE_URL", "https://api.mapmyfitness.com/v7.1/oauth2/connection/"),
        ),
        "suunto": ProviderConfig(
            key="suunto",
            label="Suunto",
            client_id=env_value("SUUNTO_CLIENT_ID", ""),
            client_secret=env_value("SUUNTO_CLIENT_SECRET", ""),
            redirect_uri=env_value("SUUNTO_REDIRECT_URI", f"{base_url}/auth/suunto/callback"),
            auth_url=env_value("SUUNTO_AUTH_URL", "https://cloudapi-oauth.suunto.com/oauth/authorize"),
            token_url=env_value("SUUNTO_TOKEN_URL", "https://cloudapi-oauth.suunto.com/oauth/token"),
            scope=env_value("SUUNTO_SCOPE", "workout"),
            user_url=env_value("SUUNTO_USER_URL", ""),
            activities_url=env_value("SUUNTO_ACTIVITIES_URL", "https://cloudapi.suunto.com/v2/workouts"),
            revoke_url=env_value("SUUNTO_REVOKE_URL", ""),
            extra={"subscription_key": env_value("SUUNTO_SUBSCRIPTION_KEY", "")},
        ),
        "runkeeper": ProviderConfig(
            key="runkeeper",
            label="Runkeeper",
            client_id=env_value("RUNKEEPER_CLIENT_ID", ""),
            client_secret=env_value("RUNKEEPER_CLIENT_SECRET", ""),
            redirect_uri=env_value("RUNKEEPER_REDIRECT_URI", f"{base_url}/auth/runkeeper/callback"),
            auth_url=env_value("RUNKEEPER_AUTH_URL", "https://runkeeper.com/apps/authorize"),
            token_url=env_value("RUNKEEPER_TOKEN_URL", "https://runkeeper.com/apps/token"),
            scope=env_value("RUNKEEPER_SCOPE", "profile activity"),
            user_url=env_value("RUNKEEPER_USER_URL", "https://api.runkeeper.com/user"),
            activities_url=env_value("RUNKEEPER_ACTIVITIES_URL", "https://api.runkeeper.com/fitnessActivities"),
            revoke_url=env_value("RUNKEEPER_REVOKE_URL", ""),
        ),
        "garmin": ProviderConfig(
            key="garmin",
            label="Garmin Connect",
            client_id=env_value("GARMIN_CLIENT_ID", ""),
            client_secret=env_value("GARMIN_CLIENT_SECRET", ""),
            redirect_uri=env_value("GARMIN_REDIRECT_URI", f"{base_url}/auth/garmin/callback"),
            auth_url=env_value("GARMIN_AUTH_URL", ""),
            token_url=env_value("GARMIN_TOKEN_URL", ""),
            scope=env_value("GARMIN_SCOPE", ""),
            user_url=env_value("GARMIN_USER_URL", ""),
            activities_url=env_value("GARMIN_ACTIVITIES_URL", ""),
            revoke_url=env_value("GARMIN_REVOKE_URL", ""),
        ),
        "inline_route_tracking": ProviderConfig(
            key="inline_route_tracking",
            label="Inline Route Tracking",
            client_id=env_value("INLINE_ROUTE_TRACKING_CLIENT_ID", ""),
            client_secret=env_value("INLINE_ROUTE_TRACKING_CLIENT_SECRET", ""),
            redirect_uri=env_value("INLINE_ROUTE_TRACKING_REDIRECT_URI", f"{base_url}/auth/inline_route_tracking/callback"),
            auth_url=env_value("INLINE_ROUTE_TRACKING_AUTH_URL", ""),
            token_url=env_value("INLINE_ROUTE_TRACKING_TOKEN_URL", ""),
            scope=env_value("INLINE_ROUTE_TRACKING_SCOPE", ""),
            user_url=env_value("INLINE_ROUTE_TRACKING_USER_URL", ""),
            activities_url=env_value("INLINE_ROUTE_TRACKING_ACTIVITIES_URL", ""),
            revoke_url=env_value("INLINE_ROUTE_TRACKING_REVOKE_URL", ""),
        ),
    }
    return {
        "strava": StravaAdapter(cfgs["strava"]),
        "adidas": AdidasAdapter(cfgs["adidas"]),
        "mapmyrun": MapMyRunAdapter(cfgs["mapmyrun"]),
        "suunto": SuuntoAdapter(cfgs["suunto"]),
        "runkeeper": RunkeeperAdapter(cfgs["runkeeper"]),
        "garmin": GarminAdapter(cfgs["garmin"]),
        "inline_route_tracking": InlineRouteTrackingAdapter(cfgs["inline_route_tracking"]),
    }
