"""One-shot messages carried across a redirect in the signed session cookie.

Both functions here go out of their way to mutate `request.session` through
`__setitem__` / `__delitem__` rather than in place. Starlette's session is a
dict subclass that tracks its own dirty flag, and only a response whose
session is marked modified gets a new `Set-Cookie`:

    def setdefault(self, key, default=None):
        if key not in self:
            self.mark_modified()
        return super().setdefault(key, default)

So `session.setdefault("flashes", []).append(msg)` persists the first time
and silently stops working afterwards — once the key exists, setdefault
marks nothing, and appending to the nested list the middleware already
holds is invisible to it. The cookie is never rewritten and the message is
dropped. Since `pop_flashes` runs on every rendered page, the key existed
from the first page view onwards, which made that the normal case rather
than an edge case.
"""

from fastapi import Request


def flash(request: Request, message: str, category: str = "info") -> None:
    # Rebuilt and reassigned, never appended to in place: the assignment is
    # what marks the session modified and gets the cookie written out.
    flashes = list(request.session.get("flashes", []))
    flashes.append({"message": message, "category": category})
    request.session["flashes"] = flashes


def pop_flashes(request: Request) -> list[dict]:
    flashes = request.session.get("flashes")
    if not flashes:
        # Deliberately no write when there is nothing to clear, so an
        # ordinary page view doesn't mark the session modified and re-issue
        # the cookie on every response.
        return []
    del request.session["flashes"]
    return list(flashes)
