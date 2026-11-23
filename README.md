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

Or over HTTP, for an unattended sweep — see
[the intake endpoint](#the-intake-endpoint).

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

### The intake endpoint

`POST /intake/requests` is the machine-facing version of the script above, for
a mail sweep that runs without anyone on the box.

It exists only when `INTAKE_TOKEN_HASH` is set. Leave it blank — the default —
and the route is never registered: the path does not exist and nothing appears
in the OpenAPI schema.

```bash
uv run scripts/generate_intake_token.py
```

That prints a raw token for the caller and an `INTAKE_TOKEN_HASH=` line for the
server's `.env`. **Only the SHA-256 digest is stored**, so a leaked `.env`, a
copied database or a restored backup yields nothing that can be presented here.
The raw token is shown once and cannot be recovered; rotating is just running
the script again (see [`DEPLOY.md`](DEPLOY.md)).

This is the opposite choice from `calendar_token` and a request's access token,
which are stored in the clear — deliberately, because those have to be
*recoverable*: one is displayed so it can be pasted into a calendar client, the
other goes into a requester's tracking URL. An intake token is only ever
verified, so it never needs reading back. SHA-256 rather than bcrypt because a
256-bit random token has nothing to brute-force, so a slow KDF would only cost
CPU per request — and bcrypt silently truncates at 72 bytes.

```bash
curl -X POST https://tasks.example.com/intake/requests \
  -H "Authorization: Bearer $INTAKE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"items": [
        {"title": "Approve Kuali program form",
         "description": "Approve in Kuali; a student is held up until this clears.",
         "request_class": "quick",
         "source": "email",
         "desired_completion_date": "2026-11-26",
         "desired_date_reason": "Stated Nov 30; 2 business days earlier, the Faculty office acts after.",
         "links": [{"url": "message://%3Ckuali-99@uwo.ca%3E", "label": "Originating email"}],
         "dedupe_key": "<kuali-99@uwo.ca>"}
      ]}'
```

A bare JSON list works in place of `{"items": [...]}`. Every field above except
`title`, `description` and `request_class` is optional; `source` defaults to
`email` and may only be `email` or `manual`.

**What a leaked token buys.** Items always land at `submitted`, and `source`
can never be `form`. There is no field for a status, a queue position or a
requester, and unknown fields are refused rather than ignored — so the worst
case is junk in "Awaiting review", which is reviewed before anything happens to
it. The token goes in the `Authorization` header, compared in constant time; a
wrong or missing one gets the same bare `404` as a path that does not exist, so
a probe learns nothing. The endpoint is rate-limited (`INTAKE_RATE_LIMIT`),
capped at 50 items and 256 KB per request, and notifies nobody.

**Idempotency.** Give each item a `dedupe_key` and a repeat is a no-op. The RFC
message-id of the originating mail is the natural value. This is what makes a
sweep safe to re-run, and safe to run twice over the same mailbox:

```json
{"index": 0, "status": "duplicate", "request_id": 7, "public_number": "REQ-0007",
 "dedupe_key": "<kuali-99@uwo.ca>"}
```

Two consequences worth knowing:

- A key you have already sent stays used **whatever became of the request**. If
  you declined or cancelled it, a later sweep reports `duplicate` and does not
  put it back. That is the intent — a decision you have made should stick — but
  it does mean a request deliberately removed cannot be re-captured under the
  same key.
- One email that genuinely warrants two tasks needs two distinct keys. Qualify
  them, e.g. `<id@host>#register` and `<id@host>#attend`.

Items with no `dedupe_key` are never deduplicated. Nothing infers a key from
content — two identical items without keys are both stored.

**The response** is `200` with a per-item result, so one bad row does not sink
the batch:

```json
{"results": [{"index": 0, "status": "created", "request_id": 1, "public_number": "REQ-0001", "dedupe_key": "<a@uwo.ca>"},
             {"index": 1, "status": "rejected", "error": "title: String should have at least 1 character"}],
 "created": 1, "duplicate": 0, "rejected": 1}
```

`status` is `created`, `duplicate` or `rejected`. Whole-batch failures — bad
JSON, too many items, an oversized body, a bad token — are non-200 instead.

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
explains them. Deactivating also cancels whatever occurrence is already open,
so a stale item can't outlive the rule that made it.

Cancelling works at two levels, because "no longer applies" happens at two:

| | What it means |
|---|---|
| Deactivate the commitment | the obligation itself has ended |
| Cancel one request | this cycle didn't apply — nothing to reconcile, no exams to book |

A cancelled request gets its own terminal status, not `done`. See
[Cancelling a request](#cancelling-a-request).

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

## Cancelling a request

**Cancel — no longer needed**, on any active request's page, closes it without
claiming it was done. A `cancelled` status was added rather than reusing one of
the three terminal states, because none of them is true for "the occasion went
away":

- `done` asserts the work happened. A month with no PCard transactions to
  reconcile is not a month the reconciliation was performed, and recording it as
  one makes the archive lie about what was done.
- `declined` is refusing a request somebody else made. It carries a reason
  addressed to that person, and it is only reachable from `submitted`, so an
  accepted request could not use it anyway.
- `withdrawn` is the requester retracting their own request from the tracking
  page.

Cancel is reachable from any active status, unlike Decline, because the occasion
for a request can disappear at any point before it is finished. The reason is
optional — "no transactions this month" is a routine non-event, and demanding a
sentence about it every cycle is how an honest state stops being used.

`cancelled` filters separately from `done` in the Archive, which is the point.
The requester of a `form` request is emailed and their tracking page says the
request was not completed and not declined; cancelling one of your own
obligations mails nobody.

Cancelling an occurrence does not touch its standing commitment: the series
stays active, the generator does not re-create the cancelled occurrence, and the
next cycle is produced as normal.

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
