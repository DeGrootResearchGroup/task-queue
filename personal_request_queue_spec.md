# Personal Request Queue
## Software Requirements & Product Specification

**Version:** 0.2  
**Status:** Initial implementation specification  
**Purpose:** Reverse task-list / personal service-desk application

---

# 1. Overview

## 1.1 Purpose

Personal Request Queue is a lightweight web application for managing asynchronous requests made to a single person ("the Owner").

The application is inspired by the reverse task-list concept described by Cal Newport in *Slow Productivity*. Instead of requests arriving through email, Slack, or other communication channels and requiring the Owner to convert them into personal tasks, requesters submit complete, actionable requests directly into a public-facing request system.

The system is intentionally **not** a general-purpose project-management platform or Jira replacement.

It is designed around three principles:

1. **The requester bears the burden of clearly defining the request.**
2. **Submitting a substantial request does not imply that the Owner has accepted it.**
3. **Once accepted, requesters can transparently see the status of their request without contacting the Owner for updates.**

The application should be simple enough that submitting a request takes approximately 1–2 minutes and managing requests imposes minimal administrative overhead on the Owner.

---

# 2. Users

There are two user types.

## 2.1 Owner

There is initially one Owner.

The Owner:

- reviews submitted requests;
- accepts or declines Long Actions;
- processes Quick Actions;
- requests additional information;
- controls the Long Action queue;
- changes request status;
- sees all requests;
- accesses an authenticated administrative interface.

The architecture does not need to support multiple Owners in version 1.

## 2.2 Requester

A Requester is anyone who submits a request.

Examples include:

- graduate students;
- undergraduate students;
- research staff;
- collaborators;
- university staff;
- administrators.

Requesters do **not** create accounts.

After submission, the requester receives an unguessable private URL through which they can view and interact with their request.

Possession of this URL provides access to that request.

---

# 3. Scope

## 3.1 Included

The application manages **asynchronous requests for work by the Owner**.

Examples:

- review a manuscript;
- review a thesis chapter;
- review figures;
- provide feedback;
- approve a purchase;
- sign a document;
- investigate a technical issue;
- analyze results;
- provide a specific piece of information;
- complete another clearly defined task.

## 3.2 Excluded

Meeting scheduling is explicitly outside the scope of the system.

The submission interface should direct users who need a meeting to the Owner's existing meeting process, such as:

- regularly scheduled graduate-student meetings;
- an external booking link;
- other normal scheduling processes.

The application should not become a meeting-request queue.

---

# 4. Request Classes

There are two request classes.

## 4.1 Quick Action

A Quick Action is a request that the Owner could reasonably complete in **10 minutes or less after opening the request**.

The estimate includes:

- reading the request;
- opening and understanding relevant material;
- performing the requested action;
- providing the result.

Examples:

- sign a form;
- approve a purchase;
- provide a project/account code;
- review a short paragraph;
- confirm a factual detail.

Quick Actions are considered **accepted immediately upon submission**.

They do not require Owner triage before entering the queue.

## 4.2 Long Action

A Long Action requires more than approximately 10 minutes of Owner time.

Examples:

- review a manuscript;
- review a thesis chapter;
- analyze experimental results;
- investigate a technical problem;
- review a proposal;
- provide substantial written feedback.

Long Actions are **not accepted merely because they have been submitted**.

The Owner must explicitly accept a Long Action before it enters the work queue.

---

# 5. Core Principle: Complete-in-One-Sitting Requests

A central design requirement is that requests should contain everything the Owner needs to complete them without additional communication.

The submission interface must clearly communicate this expectation.

Suggested language:

> Please make your request self-contained. Before I can accept a request into my work queue, I need everything required to complete it in one sitting: clear instructions, relevant documents or links, and any context needed to make decisions.
>
> If I need additional information, the request will be returned to you for clarification before work can continue.

The form should reinforce this principle through its structure and through a mandatory confirmation before submission.

---

# 6. Submission Form

The submission form is publicly accessible.

No account or login is required.

## 6.1 Requester information

Required:

**Name**

**Email address**

Email should be stored even if version 1 does not send email notifications.

## 6.2 Request title

Required.

A short description of the request.

Example:

> Review manuscript introduction

Helper text should encourage descriptive titles.

## 6.3 Requested action

Required.

Prompt:

> What exactly do you need me to do?

Helper text should emphasize describing the required **action or decision**, rather than merely naming a topic.

Example:

Bad:

> Manuscript

Better:

> Review Sections 2–3 and tell me whether the argument and methodology are clear.

This should be a multiline field.

---

# 7. Supporting Material

## 7.1 Relevant links

The requester can provide one or more links.

Each link may optionally have a descriptive label.

Example:

- Manuscript draft — [URL]
- Results spreadsheet — [URL]
- Figures — [URL]

Version 1 should **not support file uploads**.

This avoids requiring the application to store potentially confidential documents, student information, manuscripts, research data, etc.

Requesters should instead link to existing storage systems.

The form should remind users to ensure that the Owner already has permission to access linked material.

## 7.2 Additional context

Optional multiline field.

Prompt:

> Include anything I need to know that isn't obvious from the materials above: background, constraints, decisions already made, specific questions, etc.

---

# 8. Desired Completion Date

The requester may optionally specify a:

**Desired completion date**

The terminology "deadline" should generally be avoided.

If a desired completion date is specified, the requester must also answer:

**Why this date?**

Examples:

- Submission deadline is October 15.
- I need the feedback before our October 8 meeting.
- Co-authors are meeting October 12.

The date is information used by the Owner for prioritization. It does not automatically determine queue priority.

---

# 9. Request Size Selection

The requester must answer:

> Could I reasonably complete this request in 10 minutes or less once I open it?

Options:

**Yes — Quick Action**

**No — Long Action**

Helper text should state that this includes time required to read or review supporting material.

The Owner may later move a misclassified Quick Action into Long Actions.

---

# 10. Submission Confirmation

Immediately before submission, the requester must confirm:

> To the best of my knowledge, the Owner has everything needed to complete this request in one sitting.

This confirmation is required.

Additional non-interactive reminders may state:

- The request clearly describes what needs to be done.
- All required documents and links have been provided.
- Linked material is accessible.
- Necessary context has been provided.

---

# 11. Quick Action Workflow

Quick Actions are accepted automatically.

Primary workflow:

`Queued → In Progress → Done`

The Owner may generally move directly from:

`Queued → Done`

without using In Progress.

Additional states:

`Needs Information`

`Withdrawn`

The Owner may also move a Quick Action to Long Actions if it is not actually quick.

## 11.1 Requester display

A queued Quick Action should show:

- status: Queued;
- number of currently pending Quick Actions.

It should **not show a queue position**.

Quick Actions are intended to be batch processed rather than strictly prioritized.

---

# 12. Long Action Workflow

Primary workflow:

`Submitted → Queued → In Progress → Done`

## 12.1 Submitted

A Long Action initially enters **Submitted**.

This means:

- the Owner has received it;
- the Owner has not yet accepted it;
- it is not yet part of the Owner's work queue.

The Owner can:

- Accept & Queue;
- Request Information;
- Decline.

## 12.2 Queued

Clicking **Accept & Queue** changes:

`Submitted → Queued`

This transition has explicit semantic meaning:

> The Owner has reviewed the request, understands it, believes sufficient information has been provided, and accepts it as work they intend to complete.

A newly accepted Long Action should default to the **bottom of the Long Action queue**.

The Owner can subsequently reorder it.

## 12.3 In Progress

The Owner explicitly changes:

`Queued → In Progress`

when actively working on the request.

## 12.4 Done

The Owner changes:

`In Progress → Done`

when the requested work has been completed.

Direct `Queued → Done` may be supported where appropriate.

The completion timestamp should be recorded.

---

# 13. Long Action Queue

Queued Long Actions form an ordered queue.

Each queued request has a numerical position:

1, 2, 3, ...

The Owner must be able to **drag and drop requests to reorder the queue**.

The queue order is the system's representation of current priority.

Version 1 should not implement separate High/Medium/Low priority fields.

Queue ordering is controlled exclusively by the Owner.

Reordering should update positions immediately.

Requesters should **not receive notifications merely because queue position changes**.

Queue position represents current ordering, not a contractual completion promise.

Requester-facing language should state:

> Queue positions can change as priorities and external deadlines change.

---

# 14. Long Action Visibility Before Acceptance

While a Long Action is Submitted, the requester does **not** receive a position among submitted requests.

Instead, they see aggregate workload information.

Example:

> Your request has been received and is awaiting review.
>
> 3 Long Actions are currently awaiting review.
>
> 6 Long Actions have been accepted and are currently queued.
>
> If your request is accepted, its queue position will appear here.

This prevents submission order from implying an acceptance or FIFO commitment.

---

# 15. Long Action Visibility After Acceptance

Once Queued, the requester sees:

- status;
- current queue position;
- total number of queued Long Actions.

Example:

> **Queued**
>
> Your request has been accepted.
>
> **Position: 4 of 7**
>
> Queue positions can change as priorities and external deadlines change.

Historical queue positions should not be displayed.

---

# 16. Needs Information

`Needs Information` is a temporary paused state rather than a normal stage in the linear workflow.

It may be entered from:

- Submitted;
- Queued;
- In Progress;
- Quick Action Queued;
- Quick Action In Progress.

When the Owner selects **Request Information**, they must enter a message explaining what information is needed.

The requester page should prominently display this message and provide a response field.

## 16.1 Previous state

The system must record the state from which Needs Information was entered.

When the requester responds, the request should return to that previous state.

Examples:

`Submitted → Needs Information → Submitted`

`Queued → Needs Information → Queued`

`In Progress → Needs Information → In Progress`

## 16.2 Queue position while waiting

A Long Action in Needs Information should not occupy an active queue position indefinitely.

When it enters Needs Information:

- remove it from the active queue;
- remember its previous position.

When sufficient information is received:

- restore it to its previous state;
- restore it as close as practical to its previous queue position.

---

# 17. Declined Requests

The Owner may decline a Long Action while it is Submitted.

A short explanation should be supported.

The requester sees:

- status: Declined;
- Owner's explanation.

Declined requests remain accessible through their private URL.

---

# 18. Withdrawn Requests

A requester may withdraw their own active request.

The requester-facing interface should provide:

**Withdraw request**

Withdrawal:

- sets status to Withdrawn;
- removes the request from any active queue;
- preserves the request history.

---

# 19. Requester-Provided Additional Information

Requesters should not be able to silently edit the original request after submission.

Instead, provide:

**Add information**

This creates a timestamped update appended to the request.

The original request remains unchanged.

All post-submission changes must be visible in the activity history.

---

# 20. Requester Tracking Page

Each request receives an unguessable private tracking URL.

Example structure:

`/r/<cryptographically-random-token>`

The public-facing request ID may separately be something human-readable such as:

`REQ-0042`

Sequential request IDs must **not** provide access to requests.

The tracking page should contain:

1. Request ID and title
2. Current status
3. Relevant queue information
4. Action required from requester, if applicable
5. Original request
6. Supporting links
7. Desired completion date and reason
8. Additional information provided later
9. Activity history
10. Requester actions

Requester actions should be limited primarily to:

- Respond to information request
- Add information
- Withdraw request

The application should not implement a general-purpose comment thread in version 1.

---

# 21. Activity History

Each request should maintain a timestamped history.

Example:

- Sep 30 — Completed
- Sep 27 — Work started
- Sep 24 — Additional information received
- Sep 23 — Information requested
- Sep 22 — Accepted into queue
- Sep 22 — Request submitted

Queue reorder events should **not** appear in the requester-visible history.

Internally, they may be logged if useful.

---

# 22. Desired-Date Indicators

The Owner's interface should visually indicate proximity to the requester's desired completion date.

Use a progression such as:

**Green → Yellow → Red**

The exact thresholds should be configurable.

The actual date must always be displayed alongside colour.

Colour must not be the sole indicator because of accessibility considerations.

Desired dates **must not automatically reorder the Long Action queue**.

They provide information to the Owner, who retains control of prioritization.

---

# 23. Owner Administrative Interface

The Owner interface requires authentication.

The primary dashboard should prominently display:

1. Submitted Long Actions
2. Queued Long Actions
3. Quick Actions

Secondary sections may include:

- In Progress
- Needs Information
- Completed/archive
- Declined
- Withdrawn

---

# 24. Submitted Long Actions

The dashboard should show the number of Long Actions awaiting review.

Each entry should display at minimum:

- title;
- requester;
- submission date;
- desired completion date, if any;
- desired-date urgency indicator.

Opening a Submitted request presents:

- full request;
- supporting links;
- requested completion date and justification.

Primary actions:

**Accept & Queue**

**Request Information**

**Decline**

Accept & Queue should place the request at the bottom of the Long Action queue by default.

---

# 25. Queued Long Actions

This should be a prominent ordered list.

Each item should show:

- drag handle;
- numerical queue position;
- title;
- requester;
- desired completion date;
- urgency indicator.

Drag-and-drop should allow immediate manual reprioritization.

Primary actions on a queued request:

- Start Work
- Request Information
- Open Request

---

# 26. Quick Actions

Quick Actions should support efficient batch processing.

The dashboard should show the number currently pending.

Quick Actions do not require explicit prioritization.

A dedicated **Quick Action Mode** should be provided.

After clicking Done, the next Quick Action should appear automatically.

---

# 27. Status Actions

The UI should prefer contextual action buttons rather than a generic status dropdown.

### Submitted Long Action

- Accept & Queue
- Request Information
- Decline

### Queued Long Action

- Start Work
- Request Information

### In Progress

- Complete
- Request Information
- Return to Queue

### Quick Action

- Done
- Need Information
- Move to Long Actions

---

# 28. Authentication and Privacy

## 28.1 Owner

The Owner administrative interface requires secure authentication.

Version 1 may use:

- securely hashed password;
- secure server-side or signed session;
- secure HTTP-only cookies.

Institutional SSO is not required for version 1.

## 28.2 Requesters

Requesters do not authenticate with usernames/passwords.

Each request receives a cryptographically secure random access token.

The token should provide sufficient entropy to make guessing infeasible; approximately 256 bits of randomness is recommended.

The token must never be derived from the sequential request ID.

---

# 29. Public Exposure and Anti-Spam

The submission form is Internet-public.

Version 1 should implement lightweight anti-spam controls including:

- IP-based rate limiting;
- hidden honeypot field;
- minimum plausible form-completion time;
- server-side validation;
- reasonable field-length limits.

CAPTCHA should not initially be required.

If automated spam becomes a problem, support for a service such as Cloudflare Turnstile may be added later.

---

# 30. Security Requirements

At minimum:

- HTTPS only;
- CSRF protection where appropriate;
- secure session handling;
- output escaping;
- server-side validation;
- SQL injection protection through parameterized queries/ORM;
- cryptographically secure private tokens;
- rate limiting;
- no publicly enumerable request pages;
- no sensitive information included in URLs except random access tokens.

Supporting links submitted by requesters should be treated as untrusted input.

---

# 31. Hosting Architecture

The intended deployment environment is an existing Linux server with good uptime.

Recommended architecture:

```text
Internet
   │
 HTTPS
   │
Caddy
   │
localhost application server
   │
FastAPI application
   │
SQLite
```

Caddy is recommended as the reverse proxy and HTTPS termination layer.

The application itself should not expose its application-server port directly to the Internet.

A custom hostname/subdomain should point to the server, for example:

`requests.example.ca`

The Owner's existing GitHub Pages website may link to this application but does not host the application itself.

---

# 32. Recommended Technical Stack

These choices are recommendations rather than immutable product requirements.

### Backend

Python + FastAPI

### Database

SQLite

Use an ORM such as SQLAlchemy so migration to PostgreSQL remains straightforward if ever required.

### Frontend

Server-rendered HTML with lightweight interactivity.

Suitable technologies include:

- Jinja templates;
- HTMX;
- minimal JavaScript;
- a lightweight CSS framework.

A large SPA framework is not required.

### Deployment

Docker / Docker Compose is recommended.

Caddy may run either:

- as a host service; or
- as part of the Docker Compose stack.

---

# 33. Data Model

## Request

- id
- public_request_number
- private_access_token_hash or equivalent
- requester_name
- requester_email
- title
- description
- additional_context
- request_class (`quick`, `long`)
- status
- previous_status
- desired_completion_date
- desired_date_reason
- queue_position
- previous_queue_position
- created_at
- updated_at
- started_at
- completed_at
- withdrawn_at
- declined_at

## RequestLink

- id
- request_id
- label
- url
- created_at

## RequestUpdate

- id
- request_id
- author/type
- content
- created_at

## InformationRequest

- id
- request_id
- Owner question
- requester response
- requested_at
- responded_at

## RequestEvent

- id
- request_id
- event_type
- metadata
- created_at

---

# 34. Request State Model

## Quick Actions

```text
Queued ──────────────→ Done
   │
   ├──→ In Progress ─→ Done
   │
   ├──→ Needs Information
   │         │
   │         └──→ previous state
   │
   ├──→ Move to Long Action
   │
   └──→ Withdrawn
```

## Long Actions

```text
Submitted ──→ Queued ──→ In Progress ──→ Done
    │            │             │
    └────────────┴─────────────┤
                 │             │
                 ▼             ▼
           Needs Information
                 │
                 └──→ previous state

Submitted ──→ Declined

Any active requester-owned request ──→ Withdrawn
```

Invalid state transitions should be prevented server-side.

---

# 35. Notifications

Outbound email notifications are **not required for version 1**.

After submission, the application must display the requester's private tracking URL prominently and provide a convenient **Copy Private Link** action.

Future versions may send email notifications for:

- request submission;
- acceptance;
- information requested;
- completion.

The database should retain requester email addresses from version 1.

The server should not operate its own Internet-facing mail server.

---

# 36. File Handling

Version 1 should not accept uploaded files.

Supporting material is provided through URLs.

---

# 37. Mobile Support

The requester-facing interface must be mobile responsive.

The Owner administrative interface should also support common mobile tasks, particularly:

- reviewing a submitted request;
- accepting/declining;
- requesting information;
- completing a Quick Action.

Desktop remains the primary interface for queue management and drag-and-drop ordering.

---

# 38. Accessibility

At minimum:

- keyboard-accessible controls;
- semantic HTML;
- properly associated labels;
- sufficient contrast;
- visible focus indicators;
- status/urgency never communicated through colour alone;
- usable without drag-and-drop where practical.

For queue reordering, provide an accessible alternative to dragging, such as Move Up / Move Down controls.

---

# 39. Explicit Non-Goals for Version 1

Version 1 should **not** attempt to implement:

- meeting scheduling;
- multiple Owners;
- requester accounts;
- file uploads;
- general-purpose discussion threads;
- project management;
- subtasks;
- dependencies;
- sprints;
- High/Medium/Low priority classifications;
- automatic Long Action prioritization;
- automatic Long Action queue reordering;
- Outlook integration;
- Slack integration;
- Motion integration;
- outbound email;
- institutional SSO;
- detailed time tracking;
- estimated completion dates;
- public visibility of other requesters or request titles.

---

# 40. Product Philosophy

When implementation choices are ambiguous, favor the option that:

1. reduces administrative work for the Owner;
2. requires requesters to provide complete and actionable requests;
3. makes accepted workload transparent;
4. prevents submission from being interpreted as acceptance;
5. avoids creating unnecessary communication channels;
6. preserves Owner control over prioritization;
7. minimizes the number of fields, statuses, clicks, and configuration options;
8. makes the system substantially simpler than Jira or a conventional project-management application.

The system should feel like a **personal request queue**, not enterprise ticketing software.

The intended flow is:

**Requester defines work → Owner accepts work → Owner prioritizes work → Requester can observe progress**

rather than:

**Requester sends message → Owner interprets message → Owner creates task → Requester asks for updates.**

---

# 41. Version 1 Success Criteria

The initial implementation should be considered successful if:

- a requester can submit a complete request in roughly 1–2 minutes;
- a Quick Action enters the Owner's queue automatically;
- a Long Action requires explicit Owner acceptance;
- the Owner can request missing information;
- the Owner can drag Long Actions to reprioritize them;
- accepted requesters can see their current queue position;
- unaccepted requesters can see aggregate Submitted/Queued counts;
- the Owner can process Quick Actions rapidly in a dedicated batch workflow;
- requesters can independently check status without contacting the Owner;
- requesters can add information or withdraw requests;
- private requests cannot reasonably be discovered by unauthorized users;
- the system requires little ongoing administration;
- no paid external service is necessary for core operation.

---

# 42. Deferred Design Work

The following should be specified before or during implementation:

- final visual design details;
- archive/search implementation details;
- retention policy;
- database backup strategy;
- Owner authentication/recovery mechanism;
- exact hostname and deployment configuration;
- wording and URL for the Owner's meeting-booking process.

---

# 43. UI Design Principles

The Owner interface should optimize for **doing work rather than managing work**.

The application should not resemble a general-purpose project-management platform. In particular, avoid unnecessary:

- Kanban columns;
- filters;
- menus;
- dashboards;
- charts;
- configuration;
- priority labels;
- metadata displayed on every request.

The Owner should normally be able to understand the current workload within a few seconds of opening the application.

The visual hierarchy should emphasize:

1. work requiring Owner action;
2. accepted Long Actions;
3. Quick Actions;
4. work currently blocked or in progress;
5. historical/completed work.

---

# 44. Global Navigation

Desktop navigation should be minimal.

Recommended header:

```text
┌──────────────────────────────────────────────────────────────────┐
│  REQUESTS                                      Search     Account │
├──────────────────────────────────────────────────────────────────┤
```

The application name/logo links to the Owner dashboard.

Secondary navigation should contain only:

- Dashboard
- Archive
- Search, if implemented separately from Archive
- Settings

Avoid a permanent complex sidebar unless later functionality makes one necessary.

On mobile, these controls may collapse into a menu.

---

# 45. Owner Dashboard Overview

The dashboard is the Owner's primary working screen.

Recommended overall structure:

```text
┌──────────────────────────────────────────────────────────────────┐
│ Requests                                                         │
│                                                                  │
│  LONG ACTIONS                                                    │
│                                                                  │
│  Awaiting review                                      3          │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ Review manuscript draft       Sarah      Sep 27    ● 5d   │  │
│  │ Analyze experimental results  Mohammed   Oct 02    ● 10d  │  │
│  │ Review proposal               Jane       Oct 10    ● 18d  │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                  │
│  Accepted queue                                       6          │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ ≡ 1  Thesis chapter           Alex       Sep 25    ● 3d   │  │
│  │ ≡ 2  Manuscript review        Sarah      Sep 30    ● 8d   │  │
│  │ ≡ 3  CFD analysis             John       Sep 28    ● 6d   │  │
│  │ ≡ 4  Proposal figures         Jane       Oct 04    ● 12d  │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                  │
│  QUICK ACTIONS                                        7          │
│  [ Start Quick Action Session ]                                  │
│                                                                  │
│  Sign travel form             Alex       Sep 22                  │
│  Approve purchase             Sarah      Sep 22                  │
│  Confirm figure caption       Mohammed   Sep 21                  │
│                                                                  │
│  WAITING FOR INFORMATION                              2          │
│  ...                                                             │
└──────────────────────────────────────────────────────────────────┘
```

Exact styling may vary, but this information hierarchy should be preserved.

---

# 46. Dashboard Summary

At the top of the dashboard, optionally show a compact workload summary:

```text
3 awaiting review    6 long actions queued    7 quick actions
```

This should remain informational rather than becoming an analytics dashboard.

Do not display vanity statistics such as number completed this month unless later usage demonstrates value.

---

# 47. Awaiting Review Section

This section contains Long Actions in `Submitted`.

The section header should display the count.

Each row should display:

- request title;
- requester;
- submission date or age;
- desired completion date, if supplied;
- urgency indicator.

Clicking anywhere on the row opens the request detail.

Do not require the Owner to interact with requests in submission order.

No numerical position is assigned in this section.

---

# 48. Accepted Long Action Queue

This is the most important part of the dashboard.

The section header should display the number of accepted queued requests.

Each row should display:

- drag handle;
- queue position;
- request title;
- requester;
- desired completion date;
- relative time to desired completion date;
- urgency indicator.

The queue should be visually compact enough that approximately 8–12 items can be inspected without excessive scrolling on a normal desktop display.

---

# 49. Queue Reordering

Long Actions must support drag-and-drop reordering.

Dragging should:

1. provide an obvious insertion indicator;
2. update queue ordering immediately on drop;
3. persist the new ordering server-side;
4. update displayed numerical positions;
5. not require a Save button.

For accessibility and mobile use, each queued request should also support:

- Move up
- Move down

These controls may be located in an overflow menu rather than always visible.

---

# 50. Desired-Date Indicators

Every desired date displayed to the Owner should include:

1. actual desired date;
2. relative time;
3. colour/status indicator.

Suggested initial thresholds:

### Green

More than 7 days remaining.

### Yellow

3–7 days remaining.

### Red

2 or fewer days remaining OR overdue.

These thresholds should be configurable application settings.

Requests without a desired date should display:

`No date`

with neutral styling.

Colour must never be the only indication of urgency.

---

# 51. In-Progress Request

If one or more requests are In Progress, display them above the accepted queue.

An In Progress request is removed from its numbered queue position while being actively worked on.

If the Owner chooses **Return to Queue**, it should be restored near its previous queue position where practical.

The system should retain its previous position for this purpose.

---

# 52. Quick Actions Section

The dashboard should display:

> **Quick Actions — 7**

and a prominent:

**Start Quick Action Session**

button.

Below the button, optionally display the first several pending Quick Actions.

Each compact row should show:

- title;
- requester;
- submitted date/age;
- desired completion date if supplied.

Version 1 does not require Quick Action drag-and-drop prioritization.

---

# 53. Quick Action Session

Quick Action Session is a focused processing mode.

It should hide most dashboard navigation and display one request at a time.

The primary action should be **Done**.

Other actions:

- Need Information
- Move to Long Actions
- Exit Session

When Done is clicked:

1. mark the request Done;
2. record completion time;
3. briefly provide completion feedback;
4. automatically load the next Quick Action.

No return to the dashboard should be required between Quick Actions.

---

# 54. Quick Action Ordering During a Session

Recommended default ordering:

1. requests with overdue desired dates;
2. requests with desired dates approaching soonest;
3. requests without desired dates, oldest first.

Alternatively, a simpler oldest-first implementation is acceptable for the first implementation if documented.

The application should **not automatically reorder Long Actions** using this logic.

---

# 55. Request Detail — Owner View

Opening any request should display a dedicated detail page or sufficiently large modal/drawer.

A full page is recommended for mobile compatibility and deep links.

It should show:

- request number;
- title;
- request class;
- requester name and email;
- submission date;
- status;
- desired completion date and reason;
- requested action;
- materials;
- additional context;
- contextual actions;
- activity history.

Actions displayed must depend on current status.

---

# 56. Accepting a Long Action

For a Submitted Long Action, **Accept & Queue** should be visually prominent.

Clicking it should:

1. set status to Queued;
2. append the request to the bottom of the Long Action queue;
3. assign the appropriate position;
4. create an activity event;
5. show confirmation.

No additional priority dialog should be required.

---

# 57. Request Information Interaction

Clicking **Request Information** opens a focused dialog or inline form:

> **What additional information do you need?**

The message is required.

After sending:

- status becomes Needs Information;
- previous status is retained;
- previous queue position is retained where relevant;
- request is removed from the active queue;
- event is recorded.

---

# 58. Needs Information Dashboard Section

Requests waiting on the requester should not clutter active work queues.

Display a separate collapsed or lower-priority section:

> **Waiting for information — 2**

Each row should display:

- title;
- requester;
- date information was requested;
- previous state.

These requests do not count toward the active Long Action queue total.

---

# 59. Requester Response Indicator

When a requester supplies requested information, the request returns to its previous workflow state.

The Owner dashboard should make the new information visible with a **New information** indicator.

This indicator remains until the Owner opens the request.

Do not create a separate inbox solely for these responses.

---

# 60. Completing a Long Action

For an In Progress request, the primary action should be:

**Complete**

Clicking Complete:

- sets status to Done;
- records completion timestamp;
- creates activity event;
- removes it from active work;
- updates queue counts.

A lightweight optional Owner completion note may be supported.

---

# 61. Starting Work

A Queued Long Action should provide:

**Start Work**

Clicking it:

- records its current queue position;
- sets status to In Progress;
- records started timestamp;
- removes it from numbered Queued items;
- renumbers remaining queued requests.

---

# 62. Returning Work to Queue

An In Progress request should support:

**Return to Queue**

When used:

- status returns to Queued;
- restore the request near its previous queue position where practical;
- update numerical positions.

---

# 63. Move Quick Action to Long Actions

Quick Actions must provide:

**Move to Long Actions**

When selected:

- request class changes from Quick to Long;
- request becomes Queued rather than Submitted;
- it is appended to the bottom of the Long Action queue.

The Owner has already inspected the request, so requiring a second acceptance step is unnecessary.

---

# 64. Declining a Request

For Submitted Long Actions, **Decline** should require a short explanation.

The explanation is shown to the requester.

After confirmation:

- status becomes Declined;
- timestamp is recorded;
- event is recorded;
- request leaves active dashboard sections.

---

# 65. Dashboard Empty States

Empty states should be concise and positive without gamification.

Examples:

**Awaiting review**

> No Long Actions awaiting review.

**Long Action queue**

> No accepted Long Actions queued.

**Quick Actions**

> No Quick Actions waiting.

Avoid streaks, confetti, productivity scores, or other gamified elements.

---

# 66. Requester Page Visual Hierarchy

The requester-facing page should prioritize:

1. current status;
2. whether requester action is required;
3. queue information;
4. original request;
5. activity.

It should include:

- request number and title;
- prominent status card;
- desired completion date;
- original request;
- materials;
- Add Information action;
- Withdraw Request action;
- activity history.

---

# 67. Requester Submitted State

For a Submitted Long Action:

> **Submitted**
>
> Your request has been received and is awaiting review.
>
> 3 Long Actions are currently awaiting review.  
> 6 Long Actions have been accepted and are currently queued.
>
> If your request is accepted, its queue position will appear here.

Do not show:

- submission position;
- names of other requesters;
- titles of other requests.

---

# 68. Requester Needs Information State

Needs Information should visually override ordinary status information.

It should display:

- Action Required;
- Owner's question;
- date requested;
- response field;
- Send Response button.

After successful submission:

> Response received. Your request has returned for processing.

---

# 69. Requester Done State

Completed requests should clearly state:

> **Completed**
>
> Completed September 30, 2026.

If the Owner provided an optional completion note, display it immediately below.

The original request and history remain available.

---

# 70. Requester Withdraw Action

Withdraw should be visually secondary and require confirmation.

The confirmation should explain that withdrawal removes the request from active work while preserving its history and private tracking page.

---

# 71. Mobile Owner Interface

On mobile:

- dashboard sections become vertically stacked;
- queue rows may display metadata on two lines;
- drag-and-drop may be omitted or secondary;
- Move Up / Move Down controls must remain available;
- Quick Action Session should use nearly the full viewport;
- primary actions should be large enough for touch interaction.

---

# 72. Desktop Density

The desktop Owner interface should favor moderate information density.

Avoid:

- oversized cards;
- excessive whitespace;
- large illustrations;
- unnecessary animations.

The Owner should be able to inspect approximately:

- 3–5 Submitted requests;
- 8–12 queued Long Actions;
- several Quick Actions;

within roughly one desktop viewport or modest scrolling.

---

# 73. Keyboard Interaction

Where practical, support keyboard-efficient Owner workflows.

Desirable enhancements include:

- `j` / `k` to move between requests;
- Enter to open selected request;
- keyboard-accessible queue reordering;
- keyboard shortcuts during Quick Action Session.

These are enhancements rather than version 1 blockers unless straightforward to implement.

---

# 74. Confirmation Philosophy

Avoid confirmation dialogs for easily reversible, low-risk actions.

No confirmation needed for:

- Start Work;
- Return to Queue;
- drag reorder;
- Accept & Queue.

Confirmation or explicit input is appropriate for:

- Decline;
- Withdraw;
- potentially destructive administrative actions.

---

# 75. Loading and Interaction Feedback

Interactions should feel immediate.

For operations such as:

- queue reorder;
- status transition;
- Quick Action completion;

the UI should update optimistically where safe or provide rapid feedback.

Do not require full-page reloads for routine dashboard actions if lightweight HTMX-style updates can provide a smoother workflow.

Errors must be clearly surfaced and must not silently leave the UI inconsistent with server state.

---

# 76. Search and Archive

Completed, Declined and Withdrawn requests should leave the active dashboard.

Archive should support basic search by:

- request ID;
- title;
- requester name;
- requester email.

Useful filters:

- status;
- request class;
- completion/submission date range.

Do not build complex reporting/filter builders in version 1.

---

# 77. Settings Interface

Version 1 settings should remain small.

Potential settings:

### Owner

- display name;
- meeting/booking URL;
- optional explanatory text.

### Queue

- Quick Action threshold label, default 10 minutes;
- desired-date green/yellow/red thresholds.

### Security

- change Owner password;
- logout other sessions, if implemented.

---

# 78. Recommended Visual Language

The application should feel:

- professional;
- calm;
- lightweight;
- academic rather than corporate;
- functional rather than decorative.

Use restrained typography and colour.

Status colours should be consistent across the application.

Desired-date urgency colours should be visually distinct from workflow status where practical.

Avoid making red ubiquitous; reserve it primarily for urgency, errors, and destructive actions.

---

# 79. Owner Dashboard Acceptance Criteria

The dashboard implementation is acceptable when the Owner can:

1. open the application and immediately see how many Long Actions await acceptance;
2. see the ordered list of accepted Long Actions;
3. identify approaching desired dates at a glance;
4. drag accepted Long Actions to reprioritize them;
5. open and accept a Submitted request with minimal interaction;
6. request additional information;
7. start a Long Action;
8. complete a Long Action;
9. enter Quick Action Session;
10. process multiple Quick Actions without returning to the dashboard;
11. move an incorrectly classified Quick Action into Long Actions;
12. see which requests are waiting on requester information;
13. search historical requests;
14. perform common actions comfortably from a phone.

---

# 80. UI Non-Goals

The Owner UI should not include in version 1:

- Kanban boards;
- Gantt charts;
- calendar views;
- project dashboards;
- burndown charts;
- productivity scores;
- estimated hours;
- time tracking;
- automatic scheduling;
- priority labels;
- nested tasks;
- customizable workflow builders;
- customizable dashboard widgets;
- requester-to-requester visibility.

---

# 81. Updated Version 1 Product Flow

The complete intended workflow is:

```text
REQUESTER
    │
    │ submits complete request
    ▼
┌─────────────────────────────┐
│ Quick?                      │
│                             │
│ YES → automatically Queued  │
│ NO  → Submitted             │
└─────────────────────────────┘
              │
              ▼
OWNER REVIEWS LONG ACTION
              │
      ┌───────┼────────┐
      ▼       ▼        ▼
   Queue    Need      Decline
            Info
      │
      ▼
ORDERED LONG-ACTION QUEUE
      │
      │ Owner drag/reorders
      ▼
   In Progress
      │
      ▼
     Done
```

At any appropriate stage:

```text
Owner → Needs Information → Requester responds → Previous state restored

Requester → Withdraw → Removed from active workload
```

The core product remains intentionally asymmetric:

**Requesters describe and track work.**

**The Owner accepts, prioritizes and performs work.**

The software exists to reduce coordination overhead between those two activities.

---

# 82. Remaining Implementation Decisions

The following may be resolved during implementation without additional product-design work:

- exact font;
- exact spacing;
- CSS framework;
- exact icons;
- modal versus full-page presentation for specific secondary interactions;
- precise mobile breakpoints;
- exact animation behavior for drag-and-drop;
- choice of JavaScript drag-and-drop library;
- exact archive pagination strategy.

These choices should preserve the workflow and visual hierarchy specified above.
