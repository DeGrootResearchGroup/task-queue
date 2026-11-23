#!/usr/bin/env python3
"""Generate an intake token for the machine-facing endpoint.

Prints two things: the raw token, which goes to whatever calls
`POST /intake/requests`, and the `INTAKE_TOKEN_HASH` line for the server's
`.env`. Only the hash is ever stored on the box.

The raw token is shown once and is not recoverable afterwards — there is
nothing on the server to recover it from, which is the point. Rotating is
just running this again and replacing both halves.

Usage:
    uv run scripts/generate_intake_token.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.security import generate_intake_token, hash_intake_token  # noqa: E402


def main() -> int:
    token = generate_intake_token()
    digest = hash_intake_token(token)

    print("\nRaw token — give this to the caller, e.g. the scheduled sweep.")
    print("Store it somewhere that keeps secrets (macOS Keychain, a password")
    print("manager). It is shown ONCE: nothing on the server can recover it,")
    print("and if you lose it you generate a new one and update both halves.\n")
    print(f"    {token}\n")

    print("Server half — add this line to .env on the box, then restart:\n")
    print(f"INTAKE_TOKEN_HASH={digest}")
    # Unlike OWNER_PASSWORD_HASH, whose bcrypt digest contains literal `$`
    # characters that Docker Compose's env_file loader eats (see
    # scripts/hash_password.py), a SHA-256 hex digest is only 0-9 and a-f.
    # Saying so here stops the escaping advice over there being applied to
    # this line, where doubling the characters would simply corrupt it.
    print("\nNo escaping needed: a hex digest has no `$` in it, so this line")
    print("goes into .env verbatim — unlike OWNER_PASSWORD_HASH, which needs")
    print("its `$` doubled for docker compose.")
    print("\nLeaving INTAKE_TOKEN_HASH blank disables the endpoint entirely:")
    print("the route is not registered and the path does not exist.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
