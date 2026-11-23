# Personal Request Queue

A lightweight personal service-desk / reverse task-list application. See
[`personal_request_queue_spec.md`](personal_request_queue_spec.md) for the full
product specification this implements.

It also holds the Owner's own dated obligations — things nobody submitted, but
that still have a deadline and a consequence. Those arrive through
[the server-side insert path](#the-owners-own-obligations) rather than the
public form, are marked with a different [source](#sources), and can be
[published to a calendar](#calendar-feed) or
[generated on a cycle](#standing-commitments).

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

## The Owner's own obligations

### Sources

Every request records where it came from, so self-generated admin stays
separable from the service desk the queue was built to be:

| Source | Meaning |
|---|---|
| `form` | someone else filled in the public submission form |
| `email` | derived from the Owner's mailbox by a triage sweep |
| `manual` | added by the Owner directly |

The owner dashboard filters on this (`/dashboard?source=email`) and badges
anything that isn't `form`. The public submission route always sets `form`;
nothing else may claim it.

`requester_name` and `requester_email` are nullable, because an `email` or
`manual` request has no second party. Requester-facing notifications no-op when
there is nobody to notify.

### Adding one

```bash
uv run scripts/add_request.py \
    --title "Reconcile October PCard statement" \
    --description "Match receipts, code the lines, submit for approval." \
    --class long --source email \
    --date 2026-11-25 \
    --date-reason "Stated deadline Nov 28; recorded 3 business days earlier because an approval step follows." \
    --link "message://%3C1234@uwo.ca%3E" --link-label "Originating email"
```

Three things differ from a public submission:

- **Nothing is notified.** The owner-facing "new request" email is scheduled by
  the public router, never by `app/intake.py`, so this path cannot fire it. That
  is deliberate: this feeds an inbox-clearing workflow, and mailing the Inbox for
  every captured item would defeat the point.
- **Links are not restricted to `http(s)`.** A captured email keeps the
  `message://` URL pointing back at the message it came from.
- **It lands at `submitted`**, which is already the review step — `Accept` and
  `Decline` act on that status, so captured items appear on the dashboard
  alongside everything else awaiting review. Quick Actions too: a Quick Action
  only reaches `submitted` through this path, and accepting one puts it at the
  bottom of the Quick queue.

`--date` is the date to *work to*. Where a second party must act afterwards,
that is the stated deadline backed off; `--date-reason` is where the stated
deadline and the adjustment are recorded, and it is required whenever a date is
given.

### Calendar feed

Enable it from **Settings → Calendar feed**, then subscribe (not import) from
Apple Calendar. The feed publishes every open request that carries a desired
completion date as an all-day marker.

- Read-only by construction: a subscribed feed has no write path at all.
- Entries are `TRANSP:TRANSPARENT`, so they never consume availability.
- A secret path segment is the whole credential, since a calendar client cannot
  log in. Treat the URL as a password; replacing it from Settings revokes every
  existing subscription, and the feed ships disabled.

The model has no timed event — a request carries a date and nothing finer — so
the feed holds deadline markers only. Anything that is genuinely "be in this
room at 2pm" still belongs in the calendar directly.

### Standing commitments

Obligations that arrive on a cycle are recorded once, and a request is generated
shortly before each occurrence falls due, so the notice announcing it becomes a
confirmation rather than the trigger.

Record, edit and deactivate them under **Settings → Standing commitments**.
Deactivating matters as much as adding: an obligation that lapses stops
generating work while the requests it already produced keep the rule that
explains them.

The same thing from the command line, for seeding a fresh instance:

```bash
# Monthly, second-last business day, recorded 3 business days earlier
# because an approval follows.
uv run scripts/add_recurring.py \
    --title "Reconcile PCard statement" \
    --description "Match receipts, code the lines, submit for approval." \
    --class long \
    --business-days-before-month-end 1 \
    --lead-business-days 3 \
    --date-reason "An approval step follows submission."
```

A schedule is **which months** (`--months 4 8 12`, or omitted for every month)
plus **which day** — either `--day-of-month` (clamped to short months) or
`--business-days-before-month-end` (`0` = last business day, `1` = second-last).
That covers monthly, annual and per-term cycles without a frequency enum. Both
routes in share one validator (`app/schemas.validate_commitment`), so they can't
drift on what a valid schedule is.

A schedule that can never produce an occurrence is rejected at the point it is
recorded, by either route — a commitment that silently never fires is the
failure mode this whole feature exists to prevent.

Generate what is due, from cron:

```bash
uv run scripts/generate_recurring.py
```

```bash
uv run scripts/generate_recurring.py --list --dry-run
```

Each commitment produces at most one request per run and
`last_generated_stated_date` advances as it does, so running it more often than
daily is harmless. An occurrence whose stated date has already passed is skipped
rather than generated late; one whose *effective* date has passed but whose
stated date has not is still generated — late, but not missed. Business days are
Monday to Friday, with no holiday calendar.

Settings shows each commitment's next occurrence alongside it.

**A known limit.** A commitment is anchored to a calendar day, so an obligation
whose real deadline tracks a moving event — "before the exam period", whose
dates shift each term — can only be approximated by a fixed day in the right
month. Set it early enough to be safe and treat the generated request as the
prompt to check the actual date.

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

## Deployment

See [`DEPLOY.md`](DEPLOY.md) for deploying to a Linux box behind Caddy with
Docker Compose (currently set up for `tasks.chrisdegroot.ca`).
