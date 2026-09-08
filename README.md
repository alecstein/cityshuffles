# CityShuffles

A Django + HTMX operations app for tours, bookings, messaging, templates, and vendor integrations.

## Start here

```bash
cd cityshuffles
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python manage.py migrate
python manage.py seed_demo
python manage.py createsuperuser
python manage.py runserver
```

Open:

```text
http://127.0.0.1:8000/
```

Log in with the superuser you just created.

The messenger works locally without Twilio. Messages you send will be saved as `Local only`.

## GuruWalk integration

Open **Integrations** in the sidebar. After signing in to GuruWalk, copy the value of its authentication cookie from the browser's developer tools and paste it into the private connection form as your bearer token. The app verifies it before saving it in `.integration-secrets/guruwalk.token`; it is not stored in SQLite, templates, or logs.

The Django web server and integration worker are separate processes. `runserver` alone does not start automatic syncing; run the integration worker in a second terminal to refresh authentication and import bookings on a tiered schedule:

```bash
source .venv/bin/activate
python manage.py run_integrations
```

After deploying a version that includes run ownership, apply the additive migration before starting the worker:

```bash
python manage.py migrate
```

For a scheduler such as cron, run one cycle instead:

```bash
python manage.py run_integrations --once
```

The Integrations page's **Sync all bookings** button starts that one-cycle command in the background, so the browser remains usable. Each launch receives a run id and releases the lifecycle lock before starting the child, so a child cannot strand the page in a perpetual spinner if it starts during launch. The status panel polls a local JSON endpoint and never performs provider authentication. The small X targets the run shown by the page, stops new requests, terminates active request processes, and preserves existing data. GuruWalk requests are server-side, so they do not appear in the browser's Network panel; the relevant flow is `manual_sync` → `run_integrations --once` → `run_sync` → `fetch_snapshot`. A sync makes one `search_events` request for its complete date window and one `get_event_bookings` request for every returned departure, including departures whose participant count is zero. Up to four booking requests are submitted and run concurrently (controlled by `GURUWALK_BOOKING_CONCURRENCY`); this reduces elapsed time, not the number of provider requests. `GURUWALK_REQUEST_TIMEOUT` (20 seconds by default) is a whole-response deadline enforced by an isolated process for each request, covering DNS, TLS, headers and the response body. Startup reservations are recovered after their bounded grace period. The same worker polls connected Gmail accounts and imports unread replies into the shared inbox.

Operational sync records are written to the owner-only `.integration-secrets/guruwalk.sync-log` file. They contain run ids, phases, counts, elapsed time, endpoint names, sanitized failure reasons and outcomes; credentials, response bodies and guest contact details are never written. Booking changes and the successful terminal status commit in one database transaction. If a worker exits before claiming a reserved run, the next local status read marks that run failed after the startup grace period. Restart the web server and persistent integration worker after deploying changes.

GuruWalk's booking `totalCount` has been observed to under-report returned history when cancelled records are present, so a smaller count is retained while a larger count still pauses the import as incomplete. Explicit pagination metadata is rejected until its contract is verified; the sync never silently drops pages.

The first/manual sync imports GuruWalk events in New York time from yesterday through the next 30 days. The automatic worker uses a tiered schedule: today every 10 minutes, tomorrow every 6 hours, and days 2–30 every 12 hours. It maps vendor tour, event, guest, and booking IDs separately, keeps repeated departures as separate records, preserves local attendance/contact edits, and never sends welcome messages for imported bookings until staff explicitly starts a conversation from the Dashboard. If GuruWalk rejects the token, automatic import pauses and the Integrations page asks for reconnection.

New GuruWalk records are marked as new until staff starts their conversation. The Dashboard shows those bookings first; starting a conversation creates the inbox thread and sends the default welcome message. The five local mock bookings can be recreated with:

```bash
python manage.py seed_mock_new_bookings
```

Message templates live in the **Templates** sidebar app. Create the initial set with:

```bash
python manage.py seed_templates
```

Use `{guest_name}`, `{guide_first_name}`, `{tour_name}`, and `{booking_time}` as reusable placeholders.

To create local calendar and inbox fixtures from a second vendor:

```bash
python manage.py seed_demo_vendor alecstein
```

Remove only those fixtures later with:

```bash
python manage.py remove_demo_vendor
```

## 3. Connect Twilio SMS

Set these environment variables before running Django:

```bash
export TWILIO_ACCOUNT_SID="AC..."
export TWILIO_AUTH_TOKEN="..."
export TWILIO_SMS_FROM="+15551234567"
```

Now outbound messages from SMS conversations will use Twilio.

For inbound SMS, point the Twilio number's incoming-message webhook at:

```text
https://YOUR-PUBLIC-HOST/messages/twilio/inbound/
```

Use HTTP POST.

Webhook signature validation is enabled by default whenever `TWILIO_AUTH_TOKEN` exists.

For local webhook debugging only, you can temporarily disable it:

```bash
export TWILIO_VALIDATE_WEBHOOKS="0"
```

Do not leave signature validation disabled in production.

## 4. Optional Twilio WhatsApp

Set:

```bash
export TWILIO_WHATSAPP_FROM="+14155238886"
```

Use the bare E.164 number in the environment variable. The code adds the `whatsapp:` prefix itself.

The same inbound webhook can accept either SMS or Twilio WhatsApp messages.

## 5. Delivery-status callbacks

If your Django site has a public URL, set:

```bash
export TWILIO_STATUS_CALLBACK_URL="https://YOUR-PUBLIC-HOST/messages/twilio/status/"
```

Outbound Twilio messages will then update their delivery status.

## Tour group messages

The bottom of My Tours has **Closing message**, **Photos** (disabled until uploads exist), and **Custom message** actions. Closing and Photos use editable, non-deletable built-in templates seeded by `seed_templates`. Repeat submissions are deduplicated by request key. Every send snapshots the personalized message and sends once per contact, excluding canceled bookings. The most recent incoming channel takes precedence, then the guest's saved preference is used.

Closing gets a checkmark when all recipients are successfully sent. Expandable **Message history** rows show individual results: green for confirmed delivery, red for failure, neutral for pending, sent, or unconfirmed outcomes. Confirmed failures, missing contact information, and disconnected channels can be retried; opt-outs, filtering, successful and unconfirmed sends cannot. DemoTours messages are always local. Gmail's “Sent” means accepted for sending, not a confirmed delivery receipt.

Pending sends are stored in SQLite and dispatched in the background. The existing `run_integrations` worker also resumes pending work after a web-server restart. Restart that worker after updating the application. Twilio's signed callback endpoint above updates delivery outcomes and failure codes.

## Project shape

```text
cityshuffles/
    settings.py
    urls.py

core/
    authenticated entry-point redirect

messaging/
    models.py
    forms.py
    services.py
    views.py
    urls.py
    templates/
    management/commands/seed_demo.py

bookings/
    tour and guest models, dashboard, and booking workflows

mytours/
    calendar, attendance, guest editing, and group messages

integrations/
    vendor connections, import worker, and sync commands

message_templates/
    reusable opening and group-message templates

templates/
    base.html

static/css/
    site.css
```

## Messaging data model

`Contact`
- name
- phone_number

`Conversation`
- contact
- channel (`sms` or `whatsapp`)
- status
- last_message_at

`Message`
- conversation
- direction
- body
- source vendor, when known
- provider SID
- delivery status
- read/unread
- timestamp

A `Contact` is the reusable person identity, while each `Guest` records a booking on a specific tour. Vendor-specific identities remain in the integration mapping tables, so a repeated vendor guest ID or normalized phone number can continue the existing conversation without making vendor IDs globally unique.
