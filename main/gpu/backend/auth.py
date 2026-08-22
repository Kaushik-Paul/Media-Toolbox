"""Reliable signed-cookie authentication for the GPU Space UI."""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import time
from dataclasses import dataclass

from fastapi import Request
from fastapi.responses import HTMLResponse, Response

from core.branding import FAVICON_HEAD


AUTH_COOKIE = "media-toolbox-auth"
SESSION_MAX_AGE_SECONDS = 30 * 24 * 60 * 60


def _login_page(*, error: str = "") -> str:
    error_html = (
        f'<div class="error" role="alert">{html.escape(error)}</div>' if error else ""
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light dark">
  {FAVICON_HEAD}
  <title>Media AI Toolbox login</title>
  <style>
    :root {{ color-scheme: light dark; font-family: Inter, system-ui, sans-serif; }}
    body {{ margin: 0; min-height: 100vh; display: grid; place-items: center;
      background: #f8fafc; color: #0f172a; }}
    main {{ width: min(24rem, calc(100% - 2rem)); box-sizing: border-box;
      padding: 2rem; border: 1px solid #cbd5e1; border-radius: 1rem;
      background: #fff; box-shadow: 0 18px 45px rgba(15, 23, 42, .12); }}
    h1 {{ margin: 0 0 .35rem; font-size: 1.65rem; }}
    p {{ margin: 0 0 1.35rem; color: #64748b; }}
    label {{ display: block; margin: .85rem 0 .35rem; font-weight: 650; }}
    input {{ width: 100%; box-sizing: border-box; padding: .75rem .85rem;
      border: 1px solid #94a3b8; border-radius: .65rem; background: transparent;
      color: inherit; font: inherit; }}
    input:focus {{ outline: 3px solid rgba(79, 70, 229, .25); border-color: #4f46e5; }}
    button {{ width: 100%; margin-top: 1.2rem; padding: .78rem 1rem; border: 0;
      border-radius: .65rem; background: #4f46e5; color: #fff; font: inherit;
      font-weight: 750; cursor: pointer; }}
    button:hover {{ background: #4338ca; }}
    .error {{ margin-bottom: .8rem; padding: .7rem .8rem; border-radius: .55rem;
      background: rgba(239, 68, 68, .12); color: #b91c1c; }}
    @media (prefers-color-scheme: dark) {{
      body {{ background: #020617; color: #f8fafc; }}
      main {{ background: #0f172a; border-color: #334155;
        box-shadow: 0 18px 45px rgba(0, 0, 0, .35); }}
      p {{ color: #94a3b8; }} input {{ border-color: #475569; }}
      .error {{ color: #fca5a5; }}
    }}
  </style>
</head>
<body>
  <main>
    <h1>Media AI Toolbox</h1>
    <p>Sign in to use GPU and FFmpeg tools.</p>
    {error_html}
    <form action="/login" method="post">
      <label for="username">Username</label>
      <input id="username" name="username" type="text" autocomplete="username" required autofocus>
      <label for="password">Password</label>
      <input id="password" name="password" type="password" autocomplete="current-password" required>
      <button type="submit">Sign in</button>
    </form>
  </main>
</body>
</html>"""


@dataclass(frozen=True)
class SignedCookieAuth:
    """Authenticate one configured user without server-side session storage."""

    username: str
    password: str
    max_age: int = SESSION_MAX_AGE_SECONDS

    def _signature(self, timestamp: int) -> str:
        payload = f"{self.username}:{timestamp}".encode("utf-8")
        digest = hmac.new(self.password.encode("utf-8"), payload, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    def issue(self) -> str:
        timestamp = int(time.time())
        return f"{timestamp}.{self._signature(timestamp)}"

    def valid(self, token: str | None) -> bool:
        if not token:
            return False
        timestamp_text, separator, signature = token.partition(".")
        if separator != "." or not timestamp_text.isdigit() or not signature:
            return False
        timestamp = int(timestamp_text)
        age = int(time.time()) - timestamp
        if age < -300 or age > self.max_age:
            return False
        return hmac.compare_digest(signature, self._signature(timestamp))

    def user_for(self, request: Request) -> str | None:
        return self.username if self.valid(request.cookies.get(AUTH_COOKIE)) else None

    def credentials_match(self, username: str, password: str) -> bool:
        return hmac.compare_digest(username.strip(), self.username) and hmac.compare_digest(
            password, self.password
        )

    def set_cookie(self, response: Response) -> None:
        response.set_cookie(
            AUTH_COOKIE,
            self.issue(),
            max_age=self.max_age,
            path="/",
            secure=True,
            httponly=True,
            samesite="none",
        )
        # CHIPS keeps authentication working when the Space is opened inside
        # Hugging Face's cross-origin App iframe. Older browsers safely ignore
        # the additional attribute and use the SameSite=None cookie.
        name, value = response.raw_headers[-1]
        if name.lower() == b"set-cookie":
            response.raw_headers[-1] = (name, value + b"; Partitioned")

    @staticmethod
    def clear_cookie(response: Response) -> None:
        response.delete_cookie(AUTH_COOKIE, path="/", secure=True, httponly=True,
                               samesite="none")


class LoginLandingMiddleware:
    """Serve login HTML before Gradio can emit its broken auth loading shell."""

    def __init__(self, app, auth: SignedCookieAuth):
        self.app = app
        self.auth = auth

    async def __call__(self, scope, receive, send):
        if (
            scope.get("type") == "http"
            and scope.get("method") == "GET"
            and scope.get("path", "").rstrip("/") == ""
        ):
            request = Request(scope)
            if not self.auth.valid(request.cookies.get(AUTH_COOKIE)):
                response = HTMLResponse(_login_page())
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def login_response(error: str = "", status_code: int = 200) -> HTMLResponse:
    return HTMLResponse(_login_page(error=error), status_code=status_code)
