from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app.config import get_settings
from app.deps import get_or_create_csrf_token, verify_csrf
from app.flash import flash
from app.security import verify_password
from app.templating import render

router = APIRouter()


@router.get("/login")
def login_form(request: Request):
    if request.session.get("owner_authenticated"):
        return RedirectResponse(url="/dashboard", status_code=303)
    csrf_token = get_or_create_csrf_token(request)
    return render(request, "owner/login.html", {"csrf_token": csrf_token})


@router.post("/login", dependencies=[Depends(verify_csrf)])
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    settings = get_settings()
    valid = username.strip() == settings.owner_username and verify_password(
        password, settings.owner_password_hash
    )
    if not valid:
        flash(request, "Incorrect username or password.", "error")
        return RedirectResponse(url="/login", status_code=303)

    request.session["owner_authenticated"] = True
    return RedirectResponse(url="/dashboard", status_code=303)


@router.post("/logout", dependencies=[Depends(verify_csrf)])
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)
