"""Verifies the slowapi wiring pattern used for Owner login actually blocks
requests once the limit is exceeded.

The real /login route's limit is fixed at import time from
LOGIN_RATE_LIMIT (see tests/conftest.py, which sets it to 1000/minute so
the many `login()` helper calls across the rest of the suite don't trip
it) — so it can't be lowered for a single test here without reloading
modules. Instead this builds a tiny standalone app using the exact same
`app.rate_limit.limiter` instance, decorator, and exception handler as
app/routers/owner_auth.py, with a strict limit, to prove the underlying
mechanism behaves as intended.
"""

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.rate_limit import limiter


def _make_limited_app(limit: str) -> FastAPI:
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    @app.post("/login")
    @limiter.limit(limit)
    def login(request: Request):
        return {"ok": True}

    return app


def test_exceeding_the_limit_returns_429():
    app = _make_limited_app("2/minute")
    client = TestClient(app)

    assert client.post("/login").status_code == 200
    assert client.post("/login").status_code == 200
    assert client.post("/login").status_code == 429
