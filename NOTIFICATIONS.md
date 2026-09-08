# Shared inbox notifications

All active authenticated accounts currently have access to the shared inbox. Each incoming message queues one delivery per registered device. Reading a channel clears its unread messages for everyone. Hidden-tab polling does not mark messages read. No individual read receipts or handled state are introduced.

## Deployment

1. Install requirements: `.venv/bin/pip install -r requirements.txt`.
2. Run `.venv/bin/python manage.py migrate` and `.venv/bin/python manage.py setup_push`.
3. Set `WEB_PUSH_SUBJECT` to an operator contact such as `mailto:YOUR_EMAIL` or your public HTTPS site URL in BOTH web and worker environments. Keep `.integration-secrets/web-push.pem` private, backed up, and shared between those processes. Do not regenerate it during deployments.
4. Serve the app over HTTPS. The root `/push-worker.js` and `/manifest.webmanifest` routes must be accessible. Production requires the usual Django deployment settings (DEBUG off, secret key, allowed hosts, HTTPS cookies, static hosting).
5. Run `.venv/bin/python manage.py run_notifications` as a supervised long-running process, separate from the integration worker. `--once` drains one batch for diagnostics. Keep the existing integration worker running to receive email; its current Gmail polling interval can add up to ten minutes before an email enters the app. SMS and WhatsApp arrive via their existing webhooks.
6. In each device's profile, choose Enable notifications and Send test notification. On iPhone use iOS 16.4+ and add the app to the Home Screen first. An actual iPhone 7 does not support web push.

## Behavior and limits

- Previews default off. Users can opt in per device. Logout removes that session's device registrations; expired sessions and inactive users are removed at delivery time.
- Queue entries survive restarts, retry temporary provider failures with backoff, and remove 404/410 subscriptions. `accepted` means the push service accepted the request, not that a person saw it. Duplicate retries use a stable notification tag.
- Already-read and day-old messages are skipped before dispatch. Visible app pages sync shared unread state and clear displayed notifications on that device. Already-delivered lock-screen alerts on a sleeping device cannot be reliably retracted remotely; they reconcile when the app is opened. OS Focus settings, revoked permission, connectivity, and browser restrictions can suppress delivery.
- Existing foreground polling remains as a fallback; receiving push or returning to the app triggers an immediate refresh. Do not use notification delivery as the source of truth for inbox state.
- Inspect `messaging.PushDelivery` state/error/attempts for worker diagnostics. Error fields intentionally exclude subscription secrets and message bodies.
- Device endpoints are restricted to known browser push-service hosts to prevent arbitrary outbound server requests. Add any new provider deliberately after verifying its official endpoint domain.
- Test with mocks or a dedicated demo device. No real provider delivery is required for the automated tests.
