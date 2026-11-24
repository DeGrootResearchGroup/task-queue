from datetime import date
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.flash import pop_flashes
from app.models import class_label

APP_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=APP_DIR / "templates")

# Registered as a global rather than passed per-view: the class label is
# needed in three templates reached from different routers, and threading
# it through every context dict is how the inline two-class conditionals
# got copied around in the first place.
templates.env.globals["class_label"] = class_label

# Callable, not a value: a global evaluated at import time would freeze the
# date for the life of the process, and this server is long-running. Any
# template comparing a stored date against "now" has to call it.
templates.env.globals["today"] = date.today


def render(request: Request, name: str, context: dict | None = None, status_code: int = 200):
    ctx = dict(context or {})
    ctx["flashes"] = pop_flashes(request)
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)
