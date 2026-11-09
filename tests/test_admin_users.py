import time
import unittest

from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from app.db import Base
from app.main import _hash_password, _verify_password, admin_registrations, admin_reset_user_password, templates
from app.models import AuditLog, AuthConnection, User


class AdminUserTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, autoflush=False)()
        self.local = User(username="utente", display_name="Utente locale", password_hash=_hash_password("Vecchia1!"))
        self.oauth = User(display_name="Utente Strava")
        self.db.add_all([self.local, self.oauth]); self.db.flush()
        self.db.add(AuthConnection(user_id=self.oauth.id, provider="strava", provider_user_id="123", access_token="segreto-oauth"))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def request(self, admin=True):
        now = time.time()
        session = {"csrf_token": "token", "session_started_at": now, "session_last_seen_at": now}
        if admin:
            session["local_admin"] = {"label": "Admin", "username": "admin"}
        return Request({"type": "http", "method": "POST", "path": "/admin/users/1/password", "headers": [], "session": session})

    def reset(self, user, request=None, password="Nuova123!", confirm="Nuova123!", csrf="token"):
        return admin_reset_user_password(user.id, request or self.request(), password, confirm, csrf, self.db)

    def test_admin_can_reset_local_password_with_audit(self):
        response = self.reset(self.local)
        self.assertEqual(response.status_code, 303)
        self.assertTrue(response.headers["location"].startswith("/admin/registrations?account_status="))
        self.db.expire_all()
        self.assertTrue(_verify_password("Nuova123!", self.local.password_hash))
        self.assertTrue(self.local.must_change_password)
        audit = self.db.scalars(select(AuditLog)).one()
        self.assertEqual(audit.meta, {"target_user_id": self.local.id})
        self.assertNotIn("Nuova123!", str(audit.meta))

    def test_rejects_unauthorized_csrf_oauth_and_invalid_password(self):
        original = self.local.password_hash
        for user, request, csrf in [(self.local, self.request(False), "token"), (self.local, self.request(), "bad"), (self.oauth, self.request(), "token")]:
            with self.assertRaises(HTTPException):
                self.reset(user, request=request, csrf=csrf)
        self.assertEqual(self.reset(self.local, password="debole", confirm="debole").status_code, 303)
        self.assertEqual(self.reset(self.local, confirm="Diversa1!").status_code, 303)
        self.assertEqual(self.local.password_hash, original)

    def test_registrations_page_hides_passwords_and_oauth_tokens(self):
        request = self.request()
        request.scope["query_string"] = b""
        html = admin_registrations(request, state=None, db=self.db).body.decode()
        self.assertIn("Utente locale", html)
        self.assertIn("Utente Strava", html)
        self.assertIn("strava", html)
        self.assertEqual(html.count('name="new_password"'), 1)
        self.assertNotIn(self.local.password_hash, html)
        self.assertNotIn("segreto-oauth", html)
        dashboard = templates.env.get_template("dashboard.html").render(
            app_name="Test", asset_version="1", csrf_token="token", user={"is_admin": True, "provider_key": "admin"}, initial_routes=[], initial_users=[])
        self.assertNotIn("Utenti registrati", dashboard)
        self.assertIn('href="/admin/registrations">Utenti</a>', dashboard)
        self.assertNotIn('id="importRoutes"', dashboard)
        self.assertNotIn('id="reloadRoutes"', dashboard)
        self.assertIn('data-provider="admin"', dashboard)


if __name__ == "__main__":
    unittest.main()
