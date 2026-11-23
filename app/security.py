import hashlib
import hmac
import secrets

import bcrypt


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def generate_intake_token() -> str:
    """A bearer token for the machine-facing intake endpoint."""
    return secrets.token_urlsafe(32)


def hash_intake_token(token: str) -> str:
    """SHA-256 hex digest of an intake token.

    SHA-256 rather than bcrypt, which is deliberate and the opposite of the
    choice made for the Owner password:

    - An Owner password is low-entropy and human-chosen, so it needs a slow
      KDF to make guessing it expensive. `generate_intake_token` produces
      256 bits of randomness; there is nothing there to guess, so a slow
      KDF buys no security and costs CPU on every single request.
    - bcrypt silently truncates its input at 72 bytes, which is a trap
      waiting for a longer token.

    The token is stored only as this digest, unlike `calendar_token` and
    `Request.access_token`, which are kept in the clear because they have
    to be recoverable — one is displayed to be pasted into a calendar
    client, the other goes into a requester's tracking URL. An intake token
    is generated once, shown once and thereafter only verified, so a leaked
    .env, a copied SQLite file or a restored backup yields nothing usable.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def intake_token_matches(presented: str, expected_hash: str) -> bool:
    """Constant-time check of a presented token against a stored digest."""
    if not presented or not expected_hash:
        return False
    # Normalised because the stored value is pasted into .env by hand, and a
    # hex digest means the same thing whatever its case or surrounding
    # whitespace.
    expected = expected_hash.strip().lower()
    return hmac.compare_digest(hash_intake_token(presented), expected)


def generate_access_token() -> str:
    """~256 bits of randomness, URL-safe, per spec §28.2."""
    return secrets.token_urlsafe(32)


def generate_calendar_token() -> str:
    """Secret path segment for the .ics feed.

    Same strength as an access token: a subscribed calendar client can't
    log in, so the URL is the entire credential and has to be unguessable.
    """
    return secrets.token_urlsafe(32)


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_tokens_match(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    return hmac.compare_digest(a, b)
