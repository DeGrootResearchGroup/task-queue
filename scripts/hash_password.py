#!/usr/bin/env python3
"""Hash an Owner password for use as OWNER_PASSWORD_HASH in .env.

Usage:
    uv run scripts/hash_password.py
"""

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.security import hash_password  # noqa: E402


def main() -> None:
    password = getpass.getpass("New Owner password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Passwords did not match.", file=sys.stderr)
        raise SystemExit(1)
    if not password:
        print("Password cannot be empty.", file=sys.stderr)
        raise SystemExit(1)

    digest = hash_password(password)

    print("\nRunning with `uv run uvicorn ...` directly? Add this to your .env:\n")
    print(f"OWNER_PASSWORD_HASH={digest}")

    # bcrypt hashes contain literal `$` characters (e.g. $2b$12$...). Docker
    # Compose's `env_file:` loader treats `$word` in a value as a variable
    # reference and silently blanks out anything that isn't a real env var —
    # so a plain hash gets silently corrupted with no error at startup, and
    # Owner login just stops working. Doubling each `$` escapes it.
    compose_safe = digest.replace("$", "$$")
    print("\nRunning with `docker compose` instead? Escape it for env_file parsing:\n")
    print(f"OWNER_PASSWORD_HASH={compose_safe}")


if __name__ == "__main__":
    main()
