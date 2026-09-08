"""Owner-only local secret files. No credentials in templates, logs, or SQLite."""
import base64
import json
import os
import re
import tempfile
import time
import threading
from contextlib import contextmanager
import fcntl
from django.conf import settings


def secret_dir():
    path = settings.INTEGRATIONS_SECRET_DIR
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def parse_token(value):
    value = value.strip()
    if value.startswith("Bearer "):
        value = value[7:].strip()
    if value.startswith("sso_jwt="):
        value = value[8:].split(";", 1)[0]
    if len(value) > 8192 or not re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", value):
        raise ValueError("Paste only the bearer token value, not a full cookie list or command.")
    try:
        part = value.split(".")[1]
        payload = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        subject = payload.get("sub") or payload.get("user_id")
        if not isinstance(subject, str) or not subject or len(subject) > 200:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise ValueError("This does not look like a GuruWalk SSO token.") from None
    # These claims are only an identity hint; the remote API must authenticate it.
    return value, subject


def save_token(value):
    save_secret("guruwalk.token", value)


def save_secret(filename, value):
    """Atomically write one owner-only integration secret."""
    folder = secret_dir()
    fd, temp = tempfile.mkstemp(dir=folder)
    try:
        os.chmod(temp, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(value)
        os.replace(temp, folder / filename)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def read_token():
    return read_secret("guruwalk.token")


def read_secret(filename):
    try:
        return (secret_dir() / filename).read_text().strip()
    except FileNotFoundError:
        return ""


def save_gmail_refresh_token(value):
    save_secret("gmail.refresh-token", value)


def read_gmail_refresh_token():
    return read_secret("gmail.refresh-token")


def cancel_path():
    return secret_dir() / "guruwalk.cancel"


def request_cancel():
    """Request cancellation for a specific run (or a legacy unscoped run)."""
    request_cancel_for_run(None)


def request_cancel_for_run(run_id=None):
    save_secret("guruwalk.cancel", str(run_id or ""))


def clear_cancel(run_id=None):
    path = cancel_path()
    if run_id:
        try:
            if path.read_text().strip() not in ("", str(run_id)):
                return
        except FileNotFoundError:
            return
    path.unlink(missing_ok=True)


def cancel_requested(run_id=None):
    path = cancel_path()
    if not path.exists():
        return False
    if not run_id:
        return True
    try:
        value = path.read_text().strip()
    except (FileNotFoundError, OSError):
        return False
    # An empty file is a pre-run/legacy cancellation request. It is honored
    # by the current run and cleared only by that run at termination.
    return not value or value == str(run_id)


def write_sync_progress(phase, current=0, total=0, message="", run_id=None):
    save_secret("guruwalk.sync-progress", json.dumps({
        "run_id": str(run_id or ""),
        "phase": str(phase),
        "current": max(0, int(current)),
        "total": max(0, int(total)),
        "message": str(message),
        "updated_at": time.time(),
    }, separators=(",", ":")))


def read_sync_progress():
    try:
        progress = json.loads(read_secret("guruwalk.sync-progress"))
    except (TypeError, ValueError, UnicodeError):
        return {}
    return progress if isinstance(progress, dict) else {}


def sync_progress_stale(progress, max_age=120):
    try:
        updated_at = float(progress["updated_at"])
    except (KeyError, TypeError, ValueError):
        return True
    return time.time() - updated_at > max_age


_diagnostic_lock = threading.Lock()


def write_sync_diagnostic(run_id, phase, **fields):
    """Append a bounded, sanitized operational record to the private log."""
    record = {
        "at": time.time(),
        "run_id": str(run_id or "")[:64],
        "phase": str(phase)[:40],
    }
    for key, value in fields.items():
        if key in {"token", "authorization", "body", "name", "phone", "url"}:
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            record[str(key)[:40]] = str(value)[:200] if isinstance(value, str) else value
    try:
        path = secret_dir() / "guruwalk.sync-log"
        with _diagnostic_lock:
            with path.open("a") as handle:
                os.chmod(path, 0o600)
                handle.write(json.dumps(record, separators=(",", ":")) + "\n")
    except OSError:
        # Diagnostics must never turn a successful import into a failure.
        return


@contextmanager
def sync_lock(timeout=0, poll_interval=0.05):
    # Serializes web checks, credential replacement, and worker syncs on this host.
    with (secret_dir() / "guruwalk.lock").open("a") as handle:
        deadline = time.monotonic() + max(0, float(timeout or 0))
        acquired = False
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    break
                time.sleep(min(max(0.001, poll_interval), max(0, deadline - time.monotonic())))
        if not acquired:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
