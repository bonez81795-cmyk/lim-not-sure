"""Small security helpers: CSRF tokens, rate limits, safe redirects."""
import hmac
import secrets
import threading
import time
from urllib.parse import urlparse

from flask import abort, current_app, request, session


def csrf_token():
    token = session.get("csrf")
    if not token:
        token = session["csrf"] = secrets.token_urlsafe(24)
    return token


def check_csrf():
    """Reject a form post that did not come from one of our own pages."""
    sent = request.form.get("csrf") or request.headers.get("X-CSRF-Token", "")
    want = session.get("csrf", "")
    if not want or not hmac.compare_digest(sent.encode(), want.encode()):
        abort(400, "Your session expired. Reload the page and try again.")


def safe_next(target, default="/dashboard"):
    """Only follow a redirect target that stays on this site."""
    if not target:
        return default
    parts = urlparse(target)
    if parts.scheme or parts.netloc or not target.startswith("/") or target.startswith(("//", "/\\")):
        return default
    return target


class Limiter:
    """Allow at most `limit` hits per key in a sliding window. In memory, so it
    resets on restart and covers a single worker, which is how this app runs."""

    def __init__(self, limit, window_seconds):
        self.limit = limit
        self.window = window_seconds
        self.hits = {}
        self.lock = threading.Lock()

    def allow(self, key):
        now = time.monotonic()
        with self.lock:
            if len(self.hits) > 5000:
                self.hits = {k: v for k, v in self.hits.items() if v and now - v[-1] < self.window}
            recent = [t for t in self.hits.get(key, []) if now - t < self.window]
            if len(recent) >= self.limit:
                self.hits[key] = recent
                return False
            recent.append(now)
            self.hits[key] = recent
            return True

    def reset(self, key):
        with self.lock:
            self.hits.pop(key, None)


def limiter(name, limit, window_seconds):
    """One Limiter per name for this app, created on first use."""
    store = current_app.extensions.setdefault("jobping_limiters", {})
    if name not in store:
        store[name] = Limiter(limit, window_seconds)
    return store[name]


def client_ip():
    return request.remote_addr or "unknown"


def new_token(nbytes=24):
    return secrets.token_urlsafe(nbytes)
