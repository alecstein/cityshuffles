"""Isolated GuruWalk HTTP transport.

The parent process sends one request over stdin and enforces the wall-clock
deadline by terminating this process.  Never include credentials in argv,
stdout errors, or exception text.
"""
import json
import os
import socket
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener


BASE_URL = os.environ.get(
    "GURUWALK_TRANSPORT_BASE_URL",
    "https://back.guruwalk.com/api/v1/scheduling/",
)
ENDPOINTS = {"search_events", "get_event_bookings"}
MAX_RESPONSE_BYTES = 5_000_000


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def result(**values):
    sys.stdout.write(json.dumps(values, separators=(",", ":")))
    sys.stdout.flush()


def main():
    if not (BASE_URL == "https://back.guruwalk.com/api/v1/scheduling/"
            or BASE_URL.startswith("http://127.0.0.1:")):
        result(ok=False, error="worker_input")
        return 2
    try:
        command = json.load(sys.stdin)
        endpoint = command.get("endpoint")
        token = command.get("token")
        params = command.get("params")
        timeout = float(command.get("timeout"))
        if endpoint not in ENDPOINTS or not isinstance(token, str) or not token:
            raise ValueError()
        if not isinstance(params, dict) or timeout <= 0:
            raise ValueError()
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
        result(ok=False, error="worker_input")
        return 2

    request = Request(
        BASE_URL + endpoint + "?" + urlencode(params),
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/json",
            "User-Agent": "CityShuffles/1.0",
        },
    )
    try:
        with build_opener(NoRedirects()).open(request, timeout=timeout) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            result(ok=False, error="response_too_large")
            return 0
        payload = json.loads(body)
    except HTTPError as exc:
        result(ok=False, error="http", status=int(exc.code))
        return 0
    except (URLError, socket.timeout, OSError):
        result(ok=False, error="network")
        return 0
    except (ValueError, UnicodeError):
        result(ok=False, error="invalid_response")
        return 0

    result(ok=True, payload=payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
