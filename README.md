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
a mail sweep that runs without anyone on the box, and
`GET /intake/requests/status` reads the resulting states back.

One token covers both: it **grants read as well as write**. Holding it means
being able to add items to "Awaiting review" *and* to see which messages were
captured and what became of them. The read payload is kept narrow for that
reason — see below.

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

**What a leaked token buys.** On the write side, items always land at
`submitted`, and `source` can never be `form`. There is no field for a status, a queue position or a
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

**Reading state back.** `GET /intake/requests/status` reports what became of
each captured item, so a mail-side job can reflect it onto the message it came
from — `dedupe_key` is the RFC message-id, so it is already the join key.

```bash
curl -G https://tasks.example.com/intake/requests/status \
  -H "Authorization: Bearer $INTAKE_TOKEN" \
  --data-urlencode "since=2026-11-23T09:00:00Z"
```

```json
{"items": [{"dedupe_key": "<kuali-99@uwo.ca>", "public_number": "REQ-0007",
            "status": "queued", "disposition": "active",
            "updated_at": "2026-11-23T14:02:11Z"}],
 "count": 1, "has_more": false, "next_cursor": null}
```

`disposition` is the coarse state, derived here so a client never encodes this
app's state machine: `awaiting_review` (submitted), `active` (queued,
in_progress, needs_information), `closed` (done, declined, withdrawn,
cancelled). Adding a status later changes this mapping, not the caller. `status`
is also returned, for anything that wants the detail.

**The payload is deliberately narrow** — no title and no description. The same
token now reads as well as writes, so a leaked one exposes which messages were
captured and what became of them, not what the obligations say.

Only rows with a `dedupe_key` are reported; a public form submission has no
message to reconcile against. Rows in a terminal state keep appearing, since
that is exactly what a mail job files on.

- `since` filters on `updated_at` and is **inclusive**. `updated_at` resolves
  only to the second, so an exclusive filter would silently drop anything
  updated in the same second as your high-water mark. A row may therefore be
  reported twice across polls; setting a flag again is a no-op.
- Timestamps are UTC, suffixed `Z`. An offset-aware `since` is converted.
- `limit` defaults to 200, maximum 500. When `has_more` is true, pass
  `next_cursor` back as `cursor` for the next page — it is opaque, and keyed on
  `(updated_at, id)` so paging terminates even when every row shares a second.

**The response** to a write is `200` with a per-item result, so one bad row does
not sink the batch:

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

**Decline's reason follows the same rule, conditionally.** It is required only
when the request has a `requester_email`: that is who the reason is written for,
and it goes into the notification they receive. One of your own captured items
has nobody on the other end, so the reason is optional there, as it is for
Cancel — declining is an expected outcome of triage, and a justification nobody
will read is friction on a path that needs to stay fast. A reason given is
always stored either way. The condition is the presence of a requester, not the
`source`: the two agree today, but only the first is the actual reason.

`cancelled` filters separately from `done` in the Archive, which is the point.
The requester of a `form` request is emailed and their tracking page says the
request was not completed and not declined; cancelling one of your own
obligations mails nobody.

Cancelling an occurrence does not touch its standing commitment: the series
stays active, the generator does not re-create the cancelled occurrence, and the
next cycle is produced as normal.

## Fixing a date

**Set a date** / **Change date**, in the *Desired completion date* block on any
active request's page, writes `desired_completion_date` by hand. The capture
gate finds no date far more often than it finds a wrong one — the deadline is in
an attachment, or in a sentence the gate is deliberately too narrow to count —
and `/intake/requests/correct` only helps while the sweep still has that mail in
front of it. Being told a date in a corridor, or noticing one a week later, had
no route in at all.

The rules are the ones every other write path already enforces:

- **A date carries a reason.** Required alongside the date, because the recorded
  date is so often not the one anybody stated: an effective date computed back
  from a stated one, or a working day chosen next to a weekend deadline. The
  reason shows under the date here and on the requester's tracking page.
- **Clearing the date clears the reason.** A reason left behind explains a date
  that is no longer there.
- **Active requests only**, the same refusal `/intake/requests/correct` makes for
  the same reason: re-dating something already `done`, `declined`, `cancelled` or
  `withdrawn` reopens a decision rather than records a fact.
- **It is not a transition.** The status is untouched, so this is not in
  `state_machine`, and a request awaiting review is still awaiting review after
  being dated.
- **Nothing is emailed.** The date and its reason are already on the tracking
  page, so a `form` requester sees the change without a mail claiming more
  significance than a correction has.

Each change is recorded on the request's timeline as `date_changed`, carrying
the old date, the new one and the reason — a date that moved with nothing on the
timeline is indistinguishable from a bug, and this field drives both the
calendar feed and the dashboard's urgency colours.

Event start and end times are **not** editable here. An accepted event has been
written to Exchange and carries its `calendar_uid`, and the sweep writes each
event once; a changed time would need a re-write path to reach Outlook rather
than leaving a stale entry behind. `/intake/requests/correct` remains the way in
for those.

## Walking the review queue

A request's page links straight to the next one awaiting review, so a triage
pass does not go back through the dashboard between decisions. After Accept,
Decline, Request Information or Put on hold — all of which land back on the same
page — the link reads **Next awaiting review**; before the decision the same
link is a **Skip**, styled quietly so it does not compete with Accept. The badge
counts the *other* requests awaiting review, so it reads the same before and
after a decision.

It follows the dashboard's own Awaiting review order (`created_at`, `id`
breaking the ties the twice-daily sweep produces), wraps from the last item back
to the oldest still submitted so a Skip does not dead-end, and disappears
entirely once nothing else is waiting.

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

A new member of `RequestStatus` or `EventType` is the exception and needs no
DDL: SQLAlchemy renders an `Enum` on SQLite as a plain `VARCHAR` with no `CHECK`
constraint, so the value is simply storable. See the note in
`8282938591ea_add_cancelled_status.py`.

## Deployment

See [`DEPLOY.md`](DEPLOY.md) for deploying to a Linux box behind Caddy with
Docker Compose (currently set up for `tasks.chrisdegroot.ca`).
