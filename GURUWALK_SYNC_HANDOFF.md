# GuruWalk sync repair and efficiency handoff

Implement this plan in `/Users/alecstein/Documents/cityshuffles`. Complete the fixes and validation; do not stop after proposing another plan. This document was the implementation brief for the sync repair and remains the acceptance checklist.

## 1. Establish the baseline

Read any applicable AGENTS.md instructions, then inspect these files:

- `integrations/guruwalk.py`: HTTP client, snapshot retrieval, transactional import, sync lifecycle.
- `integrations/credentials.py`: file lock, cancellation and progress files.
- `integrations/views.py`: manual launch, cancellation, authentication, stale recovery.
- `integrations/templates/integrations/index.html`: status polling and controls.
- `integrations/management/commands/run_integrations.py`: one-shot and scheduled execution.
- `integrations/models.py`, `integrations/tests.py`, `cityshuffles/settings.py`, `README.md`.

Use `.venv/bin/python` for Django commands. Run the existing integration tests before editing. Do not reset the working directory or overwrite unrelated changes. Do not print credential files or include real guest records in test fixtures, logs, or this handoff.

Observed on September 8, 2026:

- Database status was `failed`, with the stale-worker error. Last attempt: 18:21:41 UTC. Last successful sync: September 6, 19:21:21 UTC; imported count 204.
- Progress file remained at `starting`, 0/0. Cancellation file existed. The sync lock was available.
- These observations establish that no worker held the lock at inspection time. They do NOT establish precisely why the child stopped.
- Manual launch holds a nonblocking flock while spawning the child. The child immediately attempts that lock and silently returns if unavailable. This is a real race; reproduce it in a regression test rather than claiming it caused the historical incident.
- Every status poll currently renders the entire Integrations page; when the lock is free, that GET checks GuruWalk and Gmail authentication over the network while holding the GuruWalk lock.
- Child stdout/stderr are discarded, leaving startup failures unexplained.

## 2. Repair lifecycle ownership before changing concurrency

Implement a run identity shared by launch, worker, progress, cancellation, and terminal status. A UUID field on the connection plus matching progress/cancel metadata is an acceptable small migration. Preserve existing booking data and migration compatibility.

Required behavior:

1. Reserve a manual run atomically under the existing lifecycle lock, with its run ID and startup timestamp. Release the lock before launching the child. Pass the reserved run ID to the one-shot command.
2. Reject duplicate manual starts while an active or fresh pending run exists. Scheduled runs must respect the reservation instead of claiming or overwriting it. A worker may only claim its own pending run.
3. Give a reserved child a bounded, cancel-aware startup wait for temporary lock contention (for example, at most 10 seconds). It must not silently exit and leave `running` forever. Make acquisition failure visible for that run without overwriting a different worker's state.
4. Keep the existing single-writer exclusion throughout a claimed sync. Do not replace flock with an in-process threading lock: the web server and workers are separate processes.
5. Make terminal writes and cancellation cleanup conditional on run ID. An old worker must never mark a newer run complete/failed, clear its cancellation, or replace its progress.
6. Catch launch failure, record a sanitized failure immediately, and preserve the last successful sync timestamp/count. Pending runs whose child dies before claiming must become failed after a bounded startup grace period (at most 30 seconds), via status reconciliation.
7. Stale progress alone must not authorize a second worker while a live worker owns the lock. Recover abandoned status only when ownership has been checked safely under the lifecycle lock. Do not force-unlock or kill arbitrary processes.
8. Update only lifecycle fields on the connection; avoid a stale full-model `save()` overwriting concurrent settings changes. Define completion at transaction commit: cancellation after commit must not falsely claim the import was rolled back.

Add sanitized operational diagnostics: run ID, phase, exception class, endpoint name, elapsed time, request count and terminal outcome. Never log authorization headers, tokens, URLs containing credentials, response bodies, guest names/phones, or arbitrary exception text. Cover early command startup failures as well as failures inside `run_sync`; do not solve lost diagnostics by dumping unrestricted stderr into a log.

## 3. Separate status polling from network authentication

Add an authenticated, read-only JSON status endpoint and update the page to poll it. It must read local state, perform safe abandoned-run reconciliation, and return status/progress/cancel state/run ID/last success. It must never call GuruWalk or Gmail.

Use a single in-flight poll (schedule the next poll after the preceding one settles), handle transient errors visibly, stop on terminal state, and keep Cancel usable after controls change. Retain CSRF protection for mutations and require login for status. Polling must not queue overlapping requests or compete with worker startup through remote authentication calls.

Keep explicit connection verification intact. On an ordinary page visit, use cached health or a bounded refresh when due, without holding the worker lifecycle lock across network I/O; revalidate ownership/account before saving a remote result. Do not allow a stale health response to overwrite a new credential/account. Avoid adding an unrelated Gmail refactor.

## 4. Make cancellation bounded and honest

Cancellation must target the current run. Check it before authentication/search, before scheduling each booking request, while waiting for requests, before applying the snapshot, inside the import transaction, and immediately before commit.

- A pending run can be cancelled before its child claims it; that child must then exit without API calls or data changes.
- On cancellation, stop scheduling requests, cancel queued work, and discard fetched data. Do not claim cancellation completed while work can still write bookings.
- In-flight HTTP must have a real wall-clock deadline, not just a socket inactivity timeout. Target at most 20 seconds per request and at most 25 seconds to finish cancellation during network fetching. Implement this using a transport/process boundary that actually enforces the deadline; explain the choice. A thread future timeout does not terminate its HTTP request, and `ThreadPoolExecutor.__exit__` waits for running tasks.
- While cancelling, show that the current requests are finishing; then show a terminal cancelled state. Cancellation during apply must roll back the entire import. Status reads should remain usable during SQLite writes; test this rather than writing heartbeat database rows from networking threads.
- Do not introduce a blanket short deadline for a legitimate full import. Bound individual calls and provide continuing progress.

## 5. Reduce API overhead without inventing endpoints

Use only the observed read-only scheduling endpoints:

- `GET https://back.guruwalk.com/api/v1/scheduling/search_events` with `startDate` and `endDate`.
- `GET https://back.guruwalk.com/api/v1/scheduling/get_event_bookings` with one `eventId`.

For a nonempty sync window, issue one search covering the entire window. Keep current New York date boundaries: manual/full is yesterday through today + configured 30 days; today, tomorrow, and future preserve their existing definitions. Handle an empty configured future window without an invalid request.

Use successful search as authentication evidence; remove the redundant `check_auth()` request from a sync, but retain standalone credential verification. Do not call `get_event_by_id` because search already returns the event data used by the importer.

Fetch booking lists with a maximum of four concurrent requests, configurable down to one. Keep database access and progress writes on the coordinator; HTTP workers only fetch/parse data. Bound outstanding submissions so cancellation or an early failure does not leave hundreds of queued jobs. Assemble results in stable event order, validate the entire snapshot, then apply once in the existing transaction.

On 401/403 stop scheduling, fail authentication and pause automatic import. On 429 stop scheduling and report rate limiting; no retry storm. On network/format errors preserve existing bookings and the last successful timestamp. Account for already in-flight calls and clean them up within their deadlines. Preserve redirect refusal, response-size limits and strict schema validation.

Fetch bookings for EVERY returned event, including `participantCount == 0`: an event can contain cancelled bookings when no active participants remain. Never infer deletion/cancellation from absence or skip a booking fetch solely because the participant count is unchanged.

Request budget for E returned departures: exactly 1 + E on a successful nonempty window, excluding standalone connection verification and any separately documented, genuinely required pagination. This is not necessarily fewer requests than the old incorrect zero-participant shortcut. Be explicit that four-way concurrency reduces elapsed time, not request count.

Keep tiered automatic refresh intervals unchanged in this repair. Bulk undocumented endpoints, conditional caching, incremental change tokens and changed polling intervals are follow-up ideas, not requirements. Do not claim bulk export is impossible merely because the current endpoints are per-event.

## 6. Resolve `totalCount` semantics conservatively

The user's pasted booking response has six records but `totalCount: 5`; it also contains copy/paste formatting damage. Treat it as evidence to investigate, not proof that totalCount means active bookings, participants or anything else.

Current `rows()` rejects ANY mismatch. Refactor count handling to be endpoint-specific and retain duplicate-ID/list/row validation.

1. Inspect a small number of raw, read-only responses if credentials/network access are available. Read the saved token in memory; do not copy the pasted token into commands or source. Output only aggregate counts/status distributions and metadata keys. Check for explicit pagination indicators, including whether zero-participant events contain cancellations. No broad endpoint probing is necessary.
2. Do not invent pagination parameters or silently disable completeness validation globally. Where pagination is actually evidenced, retrieve all pages with bounded work and duplicate validation; otherwise explicitly report the limitation.
3. Minimum safe handling without further provider evidence: for booking lists, accept a nonnegative integer totalCount smaller than the number of distinct returned records (all records are retained), but reject a totalCount larger than the returned list as possibly incomplete. Reject invalid count types, including booleans. Keep search completeness checks strict. Explicit next-page/has-more evidence must never be ignored even when the count is small.
4. If live evidence supports a different rule, document the exact sanitized evidence and add tests before changing the rule. Never claim proven completeness when provider semantics remain unknown.

Keep `adults`/`children`, attendance overrides, contact edits and raw source retention semantics unchanged. The pasted cancelled records also contain `howMany`; do not silently substitute it for party counts without a separately established contract.

## 7. Required regression tests

Use synthetic records and temporary secret directories/databases; never make automated tests depend on real GuruWalk. Adapt old mocks that assume `check_auth()` precedes every sync. Add behavioral tests for:

- Child startup while the launch lock is held, then released: one run eventually executes, without orphaned `running` status. Use real independent processes for flock behavior, not only mocked lock return values.
- Double-click manual start, scheduled/manual collision, launch exception, child exit before claim, startup timeout, cancellation before claim, and abandoned reservation recovery.
- Stale old worker/progress/cancel updates cannot affect a newer run. A live lock owner is never replaced merely because progress is old.
- Status polling calls neither provider, requires login, reaches a terminal state, and leaves Cancel functional after progress/control updates.
- One search plus one booking request for every event, including zero participants; zero events; empty date window; configured date bounds; no `get_event_by_id` or redundant sync auth call.
- Concurrent fetches overlap but never exceed the configured limit; deterministic result assembly; first fatal response stops new submissions.
- Synthetic delayed/hanging transport: cancel stops submission and returns within the documented bound; no lingering writer can apply a discarded snapshot. Prefer synchronization barriers/events over arbitrary sleeps. Include a local stalled/trickling HTTP-server test for the actual transport deadline, not just a mocked timeout exception.
- Six distinct bookings with totalCount five are retained; totalCount greater than returned length fails safely; invalid counts, duplicates and explicit pagination metadata are handled as specified.
- Confirmed booking later cancelled on a zero-participant event updates to cancelled. A cancelled booking on a previously unseen zero-participant event is imported.
- 401/403, 429, malformed payload, unknown booking status, one failing concurrent request, and cancellation during apply all leave booking tables unchanged. Last successful timestamp/count remain unchanged on unsuccessful runs.
- Repeat import remains idempotent; existing staff party-size/attendance/contact edits survive; absent records are not deleted; no welcome or other outbound message is sent.
- Completion followed by a late cancellation does not falsely report rollback. Terminal progress has correct completed/total counts and last-success timestamps represent completed work.

Preserve and run the existing regression suite; do not delete protective tests to make the new code pass.

## 8. Verification and delivery

Run, from the project root:

```sh
.venv/bin/python manage.py check
.venv/bin/python manage.py makemigrations --check --dry-run
.venv/bin/python manage.py test integrations
.venv/bin/python manage.py test bookings messaging mytours
```

If models change, create the migration before the migration check. Use a disposable database for process-level tests and a consistent SQLite backup before any real database migration or live import. Do not seed, purge or reset the user's database.

Verify the UI with available browser tooling: start, progressing counts, cancel during fetch, terminal cancelled, retry, terminal success. Use the existing login session where available. Do not send guest messages. If live provider access or the browser is unavailable, complete deterministic tests and report exactly which live checks remain unverified; do not fabricate an end-to-end success.

Update README with the actual command arguments, API request pattern, concurrency, cancellation guarantee, diagnostics location and startup recovery behavior. Include any migration/restart steps for the web server and persistent worker.

Final report must state: files changed; confirmed defects versus the unproven historical cause; test results; measured mock/live elapsed time and request counts (label each); any unresolved provider completeness limitation; and required migration/restart actions. All eight sections above must be addressed before calling the job complete.
