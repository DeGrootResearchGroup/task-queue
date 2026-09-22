from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings
from app.db import Base, engine
from app.rate_limit import limiter
from app.routers import archive, dashboard, owner_auth, public, quick_session, settings as settings_router
from app.state_machine import TransitionError
from app.templating import templates

APP_DIR = Path(__file__).parent
settings = get_settings()

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Personal Request Queue")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    https_only=settings.cookie_secure,
    same_site="lax",
)
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")


@app.exception_handler(TransitionError)
async def transition_error_handler(request: Request, exc: TransitionError):
    return templates.TemplateResponse(
        request, "error.html", {"message": str(exc)}, status_code=409
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code in (302, 303, 307, 308) and "Location" in (exc.headers or {}):
        return RedirectResponse(url=exc.headers["Location"], status_code=exc.status_code)
    return templates.TemplateResponse(
        request, "error.html", {"message": exc.detail}, status_code=exc.status_code
    )


app.include_router(public.router)
app.include_router(owner_auth.router)
app.include_router(dashboard.router)
app.include_router(quick_session.router)
app.include_router(archive.router)
app.include_router(settings_router.router)
