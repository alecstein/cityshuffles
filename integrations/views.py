import secrets
import os
import subprocess
import sys
import uuid
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_GET, require_POST
from .credentials import (cancel_requested, clear_cancel, read_gmail_refresh_token,
                           read_sync_progress, read_token, request_cancel_for_run,
                           save_gmail_refresh_token, save_token, sync_lock,
                           sync_progress_stale, write_sync_diagnostic, write_sync_progress)
from .forms import CredentialForm
from .gmail import (GmailAuthenticationError, GmailIntegrationError,
                    GmailClient, authorization_url,
                    check_auth as check_gmail_auth, exchange_code,
                    oauth_configured)
from .guruwalk import AuthenticationError, GuruWalkClient, IntegrationError
from .models import Connection


def _recover_stale_sync(connection, progress):
    """Release a running status when its worker has stopped reporting."""
    if connection.sync_status != "running":
        return connection, progress

    run_id = connection.sync_run_id or ""
    if progress and str(progress.get("run_id", "")) != run_id:
        # A late progress file from a previous run is not evidence about the
        # current run.  Ignore it rather than reviving or failing the wrong run.
        progress = {}
    if progress:
        max_age = (getattr(settings, "GURUWALK_SYNC_STARTUP_GRACE", 30)
                   if progress.get("phase") in {"starting", "reserved"}
                   else getattr(settings, "GURUWALK_SYNC_PROGRESS_MAX_AGE", 120))
        if isinstance(progress.get("updated_at"), (int, float)):
            stale = sync_progress_stale(progress, max_age=max_age)
        else:
            # A partially written/corrupt progress record should get the
            # same bounded startup grace as a missing record.
            started_at = connection.sync_reserved_at or connection.last_attempt_at
            stale = started_at is None or started_at < timezone.now() - timedelta(
                seconds=getattr(settings, "GURUWALK_SYNC_STARTUP_GRACE", 30))
    else:
        latest_attempt = connection.sync_reserved_at or max(
            (getattr(connection, field) for field in (
                "last_attempt_at", "today_last_attempt_at",
                "tomorrow_last_attempt_at", "future_last_attempt_at",
            ) if getattr(connection, field)), default=None)
        grace = getattr(settings, "GURUWALK_SYNC_STARTUP_GRACE", 30)
        stale = latest_attempt is None or latest_attempt < timezone.now() - timedelta(seconds=grace)

    if not stale:
        return connection, progress

    detail = "The GuruWalk sync worker stopped reporting progress. Existing bookings were preserved; try again."
    changed = Connection.objects.filter(pk=connection.pk, sync_status="running",
                                        sync_run_id=run_id).update(
        sync_status="failed",
        detail=detail,
        sync_reserved_at=None,
    )
    if changed:
        clear_cancel(run_id)
        write_sync_diagnostic(run_id, "abandoned", reason="stale_progress")
        try:
            write_sync_progress("failed", 0, 0, detail, run_id=run_id)
        except OSError:
            pass
        connection.refresh_from_db()
    failed_progress = dict(progress or {})
    failed_progress.update({
        "phase": "failed",
        "message": detail,
    })
    return connection, failed_progress


def _check_authentication(connection):
    """Refresh the small health signal shown on the Integrations page."""
    sync_failure_detail = connection.detail if connection.sync_status == "failed" else ""
    token = read_token()
    checked_run_id = connection.sync_run_id
    checked_status = connection.sync_status
    checked_account_id = connection.account_id
    if not token:
        connection.auth_status = "unknown"
        connection.auth_checked_at = timezone.now()
        connection.enabled = False
        connection.detail = ""
    else:
        try:
            GuruWalkClient(token, request_timeout=getattr(settings, "GURUWALK_REQUEST_TIMEOUT", 20)).check_auth()
        except AuthenticationError as exc:
            connection.auth_status = "failed"
            connection.auth_checked_at = timezone.now()
            connection.enabled = False
            connection.detail = str(exc)
        except IntegrationError:
            # A timeout or provider outage says nothing about whether the
            # credential is valid. Keep the last verified authentication state.
            return
        else:
            connection.auth_status = "ok"
            connection.auth_checked_at = timezone.now()
            connection.enabled = True
            connection.detail = sync_failure_detail
    # A credential replacement may have completed while the remote request
    # was in flight. Never publish health for the token we did not finish with.
    if token != read_token():
        return
    # Do not let a slow health request overwrite a run reserved after it
    # started.  The conditional update is the ownership check.
    Connection.objects.filter(pk=connection.pk, sync_run_id=checked_run_id,
                              sync_status=checked_status,
                              account_id=checked_account_id).update(
        auth_status=connection.auth_status,
        auth_checked_at=connection.auth_checked_at,
        enabled=connection.enabled,
        detail=connection.detail,
    )


def _health_due(connection, max_age=timedelta(minutes=12)):
    return not connection.auth_checked_at or connection.auth_checked_at < timezone.now() - max_age


def _check_gmail_authentication(connection):
    if not connection.account_id or not read_gmail_refresh_token() or not oauth_configured():
        connection.auth_status = "unknown"
        connection.auth_checked_at = timezone.now()
        connection.enabled = False
        connection.detail = ""
    else:
        try:
            email = check_gmail_auth(connection)
            if connection.account_id != email:
                connection.account_id = email
        except GmailAuthenticationError as exc:
            connection.auth_status = "failed"
            connection.auth_checked_at = timezone.now()
            connection.enabled = False
            connection.detail = str(exc)
        except GmailIntegrationError as exc:
            connection.auth_status = "failed"
            connection.auth_checked_at = timezone.now()
            connection.detail = str(exc)
        else:
            connection.auth_status = "ok"
            connection.auth_checked_at = timezone.now()
            connection.enabled = True
            connection.detail = ""
    connection.save(update_fields=[
        "auth_status", "auth_checked_at", "enabled", "detail", "account_id",
    ])


@sensitive_post_parameters("token")
@login_required
def index(request):
    connection, _ = Connection.objects.get_or_create(vendor="guruwalk")
    gmail_connection, _ = Connection.objects.get_or_create(vendor="gmail")
    demo_connection = Connection.objects.filter(vendor="demotours").first()
    form = CredentialForm(request.POST if request.method == "POST" else None)
    sync_progress = read_sync_progress()
    if request.method == "POST" and form.is_valid():
        candidate_token = form.cleaned_data["token"]
        candidate_account = form.account_id
        original_token = read_token()
        # Check ownership before any provider call, then release the lock so
        # credential verification cannot block a running worker or status read.
        can_verify = False
        with sync_lock() as acquired:
            if not acquired:
                form.add_error(None, "A sync is in progress. Please try again shortly.")
            else:
                connection.refresh_from_db()
                connection, _ = _recover_stale_sync(connection, read_sync_progress())
                if connection.sync_status == "running":
                    form.add_error(None, "A sync is in progress. Please try again shortly.")
                elif connection.account_id and connection.account_id != candidate_account:
                    form.add_error(None, "This token belongs to a different GuruWalk account. Sign in to the original account.")
                else:
                    can_verify = True
        if can_verify:
            try:
                GuruWalkClient(candidate_token, request_timeout=getattr(settings, "GURUWALK_REQUEST_TIMEOUT", 20)).check_auth()
            except IntegrationError as exc:
                form.add_error(None, str(exc))
            except OSError:
                form.add_error(None, "The server could not securely save the credential.")
            else:
                with sync_lock() as acquired:
                    if not acquired:
                        form.add_error(None, "A sync is in progress. Please try again shortly.")
                    else:
                        connection.refresh_from_db()
                        if connection.sync_status == "running":
                            form.add_error(None, "A sync is in progress. Please try again shortly.")
                        elif connection.account_id and connection.account_id != candidate_account:
                            form.add_error(None, "This token belongs to a different GuruWalk account. Sign in to the original account.")
                        elif read_token() != original_token:
                            form.add_error(None, "The saved credential changed while this one was being verified. Try again.")
                        else:
                            try:
                                save_token(candidate_token)
                            except OSError:
                                form.add_error(None, "The server could not securely save the credential.")
                            else:
                                now = timezone.now()
                                Connection.objects.filter(pk=connection.pk).update(
                                    account_id=candidate_account,
                                    enabled=True,
                                    auth_status="ok",
                                    auth_checked_at=now,
                                    last_attempt_at=None,
                                    detail="",
                                )
                                messages.success(request, "GuruWalk authentication verified.")
                                return redirect("integrations:index")
    if request.method == "GET":
        # Never hold the worker lock across provider I/O.  A running import
        # owns the remote calls; status and the page remain local and usable.
        connection.refresh_from_db()
        if connection.sync_status != "running":
            if _health_due(connection):
                _check_authentication(connection)
            if _health_due(gmail_connection):
                _check_gmail_authentication(gmail_connection)
        with sync_lock() as acquired:
            connection.refresh_from_db()
            if acquired:
                connection, sync_progress = _recover_stale_sync(
                    connection, read_sync_progress())
            else:
                sync_progress = read_sync_progress()
        connection.refresh_from_db()
        gmail_connection.refresh_from_db()
    response = render(request, "integrations/index.html", {
        "connection": connection,
        "gmail_connection": gmail_connection,
        "demo_connection": demo_connection,
        "form": form,
        "gmail_oauth_configured": oauth_configured(),
        "sync_running": connection.sync_status == "running",
        "sync_progress": sync_progress,
        "sync_cancel_requested": connection.sync_status == "running" and (
            cancel_requested(connection.sync_run_id) if connection.sync_run_id else cancel_requested()),
    })
    response["Cache-Control"] = "no-store"
    return response


@login_required
def connect_gmail(request):
    if not oauth_configured():
        messages.error(request, "Gmail OAuth is not configured on this server yet.")
        return redirect("integrations:index")
    state = secrets.token_urlsafe(32)
    request.session["gmail_oauth_state"] = state
    redirect_uri = settings.GOOGLE_OAUTH_REDIRECT_URI or request.build_absolute_uri(
        reverse("integrations:gmail_callback")
    )
    return redirect(authorization_url(state, redirect_uri))


@login_required
def gmail_callback(request):
    expected_state = request.session.pop("gmail_oauth_state", None)
    if not expected_state or not secrets.compare_digest(expected_state, request.GET.get("state", "")):
        return HttpResponseBadRequest("Invalid Gmail authorization state.")
    if request.GET.get("error"):
        messages.error(request, "Gmail authorization was not completed.")
        return redirect("integrations:index")
    code = request.GET.get("code")
    if not code:
        messages.error(request, "Google did not return an authorization code.")
        return redirect("integrations:index")
    redirect_uri = settings.GOOGLE_OAUTH_REDIRECT_URI or request.build_absolute_uri(
        reverse("integrations:gmail_callback")
    )
    try:
        access_token, refresh_token = exchange_code(code, redirect_uri)
        if refresh_token:
            save_gmail_refresh_token(refresh_token)
        elif not read_gmail_refresh_token():
            raise GmailAuthenticationError("Google did not return a Gmail refresh token.")
        connection, _ = Connection.objects.get_or_create(vendor="gmail")
        connection.account_id = GmailClient(access_token).profile()
        connection.auth_status = "ok"
        connection.auth_checked_at = timezone.now()
        connection.enabled = True
        connection.detail = ""
        connection.save()
    except (GmailAuthenticationError, GmailIntegrationError, OSError) as exc:
        messages.error(request, str(exc))
        return redirect("integrations:index")
    messages.success(request, "Gmail connected.")
    return redirect("integrations:index")


@require_POST
@login_required
def disconnect_gmail(request):
    from .credentials import secret_dir

    (secret_dir() / "gmail.refresh-token").unlink(missing_ok=True)
    connection, _ = Connection.objects.get_or_create(vendor="gmail")
    connection.enabled = False
    connection.auth_status = "unknown"
    connection.account_id = ""
    connection.save(update_fields=["enabled", "auth_status", "account_id"])
    messages.success(request, "Gmail disconnected.")
    return redirect("integrations:index")


@require_POST
@login_required
def manual_sync(request):
    connection, _ = Connection.objects.get_or_create(vendor="guruwalk")
    if not read_token():
        messages.error(request, "Connect GuruWalk before syncing bookings.")
        return redirect("integrations:index")

    run_id = None
    # Serialize reservation with the worker. This also lets us recover a
    # stale running flag left behind by a process that exited unexpectedly.
    with sync_lock() as acquired:
        if not acquired:
            messages.info(request, "A GuruWalk sync is already running.")
        else:
            connection.refresh_from_db()
            connection, _ = _recover_stale_sync(connection, read_sync_progress())
            if connection.sync_status == "running":
                messages.info(request, "A GuruWalk sync is already running.")
            else:
                clear_cancel()
                run_id = uuid.uuid4().hex
                now = timezone.now()
                Connection.objects.filter(pk=connection.pk).update(
                    enabled=True, sync_status="running", last_attempt_at=now, detail="",
                    sync_run_id=run_id, sync_reserved_at=now,
                )
                try:
                    write_sync_progress("starting", 0, 0, "Starting GuruWalk sync…", run_id=run_id)
                except OSError:
                    # The sync can still run if the optional progress file is unavailable.
                    pass
    if run_id:
        # The reservation is committed and the lifecycle lock is released
        # before spawning the child. The child can claim immediately.
        try:
            child_env = os.environ.copy()
            child_env["CITYSHUFFLES_GURUWALK_RUN_ID"] = run_id
            subprocess.Popen(
                [sys.executable, str(settings.BASE_DIR / "manage.py"), "run_integrations", "--once"],
                cwd=str(settings.BASE_DIR),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
                env=child_env,
            )
        except (OSError, ValueError):
            detail = "Could not start the GuruWalk sync. Try again."
            Connection.objects.filter(pk=connection.pk, sync_run_id=run_id,
                                      sync_status="running").update(
                sync_status="failed",
                detail=detail, sync_reserved_at=None,
            )
            clear_cancel(run_id)
            try:
                write_sync_progress("failed", 0, 0, detail, run_id=run_id)
            except OSError:
                pass
            write_sync_diagnostic(run_id, "failed", error="worker_launch")
    return redirect("integrations:index")


@require_POST
@login_required
def cancel_sync(request):
    connection = Connection.objects.filter(vendor="guruwalk").first()
    if connection:
        connection.refresh_from_db()
    requested_run_id = request.POST.get("run_id", "")
    if (connection and connection.sync_status == "running"
            and requested_run_id == (connection.sync_run_id or "")):
        request_cancel_for_run(connection.sync_run_id or None)
    return redirect("integrations:index")


@login_required
@require_GET
def sync_status(request):
    """Return local sync state without making any provider requests."""
    connection, _ = Connection.objects.get_or_create(vendor="guruwalk")
    with sync_lock() as acquired:
        connection.refresh_from_db()
        progress = read_sync_progress()
        if acquired:
            connection, progress = _recover_stale_sync(connection, progress)
    connection.refresh_from_db()
    run_id = connection.sync_run_id or ""
    if str(progress.get("run_id", "")) != run_id:
        progress = {}
    if connection.sync_status != "running" and progress.get("phase") not in {
            "complete", "failed", "cancelled"}:
        progress = {
            "run_id": run_id,
            "phase": connection.sync_status,
            "current": progress.get("current", 0),
            "total": progress.get("total", 0),
            "message": connection.detail or "",
        }
    payload = {
        "status": connection.sync_status,
        "phase": progress.get("phase", ""),
        "current": progress.get("current", 0),
        "total": progress.get("total", 0),
        "message": progress.get("message", ""),
        "cancel_requested": (cancel_requested(run_id) if run_id else cancel_requested()),
        "run_id": run_id,
        "detail": connection.detail,
        "last_sync_at": connection.last_sync_at.isoformat() if connection.last_sync_at else None,
        "auth_status": connection.auth_status,
        "auth_current": connection.auth_current,
        "auth_fresh": connection.auth_fresh,
        "auth_checked_at": (connection.auth_checked_at.isoformat()
                            if connection.auth_checked_at else None),
    }
    response = JsonResponse(payload)
    response["Cache-Control"] = "no-store"
    return response
