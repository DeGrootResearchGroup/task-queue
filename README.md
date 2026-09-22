# Personal Request Queue

A lightweight personal service-desk / reverse task-list application. See
[`personal_request_queue_spec.md`](personal_request_queue_spec.md) for the full
product specification this implements.

## Stack

- Python 3.13, FastAPI, SQLAlchemy + Alembic, SQLite
- Jinja2 templates, HTMX-free server-rendered pages, SortableJS for drag-and-drop
- `uv` for dependency management

## Setup

```bash
uv sync
cp .env.example .env
```

Edit `.env`:

- Set `SECRET_KEY` to a long random string (`python -c "import secrets; print(secrets.token_urlsafe(48))"`).
- Set `OWNER_PASSWORD_HASH` by running:

  ```bash
  uv run scripts/hash_password.py
  ```

  and pasting the printed line into `.env`.

Apply the database schema:

```bash
uv run alembic upgrade head
```

Run the app:

```bash
uv run uvicorn app.main:app --reload
```

- Public submission form: <http://127.0.0.1:8000/>
- Owner login: <http://127.0.0.1:8000/login>

## Tests

```bash
uv run pytest
```

## Schema changes

After changing `app/models.py`, generate a migration:

```bash
uv run alembic revision --autogenerate -m "describe the change"
uv run alembic upgrade head
```
