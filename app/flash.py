from fastapi import Request


def flash(request: Request, message: str, category: str = "info") -> None:
    request.session.setdefault("flashes", []).append({"message": message, "category": category})


def pop_flashes(request: Request) -> list[dict]:
    flashes = request.session.get("flashes", [])
    request.session["flashes"] = []
    return flashes
