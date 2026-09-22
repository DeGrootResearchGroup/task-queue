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
    print("\nAdd this to your .env file:\n")
    print(f"OWNER_PASSWORD_HASH={hash_password(password)}")


if __name__ == "__main__":
    main()
