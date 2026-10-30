#!/usr/bin/python3
"""CGI script of the rodeo-cli Settings page: ``/rodeo-builder/admin?action=list|add|delete``.

Answers only over HTTPS (403 otherwise) and for a user the web server logged in, read from
``REMOTE_USER`` (401 otherwise). A POST must be ``application/json`` (415 otherwise), which a
form on another site cannot send. The request goes on stdin to rodeo-builder-helper, run
through ``sudo -n`` (``RODEO_BUILDER_HELPER`` overrides its path), and its answer is relayed
as JSON with the logged-in ``user`` added to ``list``. Standard library only.
"""
import json
import os
import subprocess
import sys

ACTIONS = {"list": "GET", "add": "POST", "delete": "POST"}
MAX_REQUEST = 1024 * 1024
STATUS = {200: "200 OK", 400: "400 Bad Request", 401: "401 Unauthorized", 403: "403 Forbidden",
          404: "404 Not Found", 405: "405 Method Not Allowed", 413: "413 Content Too Large",
          415: "415 Unsupported Media Type", 429: "429 Too Many Requests", 500: "500 Internal Server Error",
          501: "501 Not Implemented"}


def helper_path():
    return os.environ.get("RODEO_BUILDER_HELPER", "/usr/local/sbin/rodeo-builder-helper")


def answer(env, stdin):
    """(HTTP status, JSON body) for the request described by CGI environment *env*."""
    if env.get("HTTPS", "").lower() not in ("on", "1"):
        return 403, {"error": "the Settings page needs HTTPS"}
    user = env.get("REMOTE_USER", "")
    if not user:
        return 401, {"error": "log in to manage credentials"}
    method = env.get("REQUEST_METHOD", "GET")
    action = ""
    for part in env.get("QUERY_STRING", "").split("&"):
        key, _, value = part.partition("=")
        if key == "action":
            action = value
    if action not in ACTIONS:
        return 400, {"error": "unknown action {!r}".format(action)}
    if method != ACTIONS[action]:
        return 405, {"error": "{} needs {}".format(action, ACTIONS[action])}
    body = b"{}"
    if method == "POST":
        if env.get("CONTENT_TYPE", "").split(";")[0].strip() != "application/json":
            return 415, {"error": "send application/json"}
        try:
            length = int(env.get("CONTENT_LENGTH") or "0")
        except ValueError:
            length = -1
        if not 0 <= length <= MAX_REQUEST:
            return 413, {"error": "request too large"}
        body = stdin.read(length)
    cmd = [helper_path(), action] if os.geteuid() == 0 else ["sudo", "-n", helper_path(), action]
    try:
        r = subprocess.run(cmd, input=body, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        out = json.loads(r.stdout.decode("utf-8"))
        status, obj = int(out["status"]), out["body"]
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        return 500, {"error": "rodeo-builder-helper failed: is rodeo-cli installed with its Settings page?"}
    if action == "list" and status == 200:
        obj["user"] = user
    return status, obj


def main():
    status, obj = answer(os.environ, sys.stdin.buffer)
    payload = json.dumps(obj).encode("utf-8")
    out = sys.stdout.buffer
    out.write("Status: {}\r\n".format(STATUS.get(status, "{} Error".format(status))).encode())
    out.write(b"Content-Type: application/json; charset=utf-8\r\nCache-Control: no-store\r\n")
    out.write("Content-Length: {}\r\n\r\n".format(len(payload)).encode())
    out.write(payload)


if __name__ == "__main__":
    main()
