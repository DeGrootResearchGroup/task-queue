from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.flash import pop_flashes

APP_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=APP_DIR / "templates")


def render(request: Request, name: str, context: dict | None = None, status_code: int = 200):
    ctx = dict(context or {})
    ctx["flashes"] = pop_flashes(request)
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)
