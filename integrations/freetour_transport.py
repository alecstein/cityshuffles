"""Isolated, read-only FreeTour HTTP transport.

Cookies arrive over stdin and never appear in process arguments or errors.
The parent process enforces the whole-request deadline by terminating this
worker when necessary.
"""
import base64
import json
import os
import re
import socket
import sys
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


BASE_URL = os.environ.get("FREETOUR_TRANSPORT_BASE_URL", "https://admin.freetour.com")
MAX_RESPONSE_BYTES = 5_000_000
ALLOWED_PATHS = (
    re.compile(r"^/backoffice/bookings\?date=\d{4}-\d{2}-\d{2}$"),
    re.compile(r"^/backoffice/get_booking/\d{4}/\d{1,2}\?page=bookings$"),
)


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def result(**values):
    sys.stdout.write(json.dumps(values, separators=(",", ":")))
    sys.stdout.flush()


def main():
    if not (BASE_URL == "https://admin.freetour.com" or BASE_URL.startswith("http://127.0.0.1:")):
        result(ok=False, error="worker_input")
        return 2
    try:
        command = json.load(sys.stdin)
        path = command.get("path")
        cookies = command.get("cookies")
        timeout = float(command.get("timeout"))
        if not isinstance(path, str) or not any(pattern.fullmatch(path) for pattern in ALLOWED_PATHS):
            raise ValueError()
        if not isinstance(cookies, dict) or not cookies or timeout <= 0:
            raise ValueError()
        cookie_header = "; ".join(
            f"{name}={value}" for name, value in cookies.items()
            if re.fullmatch(r"[A-Za-z0-9_-]+", str(name)) and isinstance(value, str) and value
        )
        if not cookie_header:
            raise ValueError()
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
        result(ok=False, error="worker_input")
        return 2

    request = Request(
        BASE_URL + path,
        headers={
            "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
            "Cookie": cookie_header,
            "User-Agent": "CityShuffles/1.0",
            "X-Requested-With": "XMLHttpRequest" if "get_booking" in path else "",
        },
    )
    try:
        with build_opener(NoRedirects()).open(request, timeout=timeout) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
            content_type = response.headers.get("Content-Type", "")
            set_cookies = response.headers.get_all("Set-Cookie") or []
        if len(body) > MAX_RESPONSE_BYTES:
            result(ok=False, error="response_too_large")
            return 0
    except HTTPError as exc:
        result(ok=False, error="http", status=int(exc.code))
        return 0
    except (URLError, socket.timeout, OSError):
        result(ok=False, error="network")
        return 0

    result(
        ok=True,
        body=base64.b64encode(body).decode(),
        content_type=content_type,
        set_cookies=set_cookies,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
