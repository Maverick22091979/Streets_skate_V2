import unittest
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

from app.main import _require_csrf


def request(origin=None, host="streetskate.create-to-live.com", scheme="http"):
    headers = [(b"host", host.encode())]
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    return Request({"type": "http", "method": "POST", "path": "/login/admin", "scheme": scheme,
                    "headers": headers, "server": ("internal", 5000), "session": {"csrf_token": "attuale"}})


class CsrfProxyTests(unittest.TestCase):
    @patch("app.main.BASE_URL", "https://streetskate.create-to-live.com")
    def test_public_https_origin_matches_internal_http_after_session_change(self):
        _require_csrf(request("https://streetskate.create-to-live.com"), "precedente")

    @patch("app.main.BASE_URL", "https://streetskate.create-to-live.com")
    def test_foreign_origin_and_spoofed_host_stay_forbidden(self):
        for req in (request("https://malicious.example"),
                    request("https://streetskate.create-to-live.com", host="malicious.example"),
                    request(None)):
            with self.assertRaises(HTTPException):
                _require_csrf(req, "precedente")

    def test_valid_token_works_without_origin(self):
        _require_csrf(request(), "attuale")


if __name__ == "__main__": unittest.main()
