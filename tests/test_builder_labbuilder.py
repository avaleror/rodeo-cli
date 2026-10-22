"""Rodeo Builder: lab-in-a-box's dynamic lab-builder next to rodeo builder (rodeo/builder/labbuilder.py)."""
from __future__ import annotations

import json
import re
import socket
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from click.testing import CliRunner

from rodeo.builder import labbuilder
from rodeo.builder.api import Api
from rodeo.builder.server import csp, make_server
from rodeo.commands.builder_cmd import builder_cmd

RUN_LOCAL = '''
import os, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        data = ("lab-builder from " + os.getcwd()).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass
'''


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _checkout(root: Path, script: str = RUN_LOCAL) -> Path:
    (root / "webui").mkdir(parents=True)
    (root / "webui" / "run-local.py").write_text(script)
    return root


def test_start_serves_the_checkouts_lab_builder_and_stops(tmp_path):
    port = _free_port()
    lb = labbuilder.start(_checkout(tmp_path / "liab"), "127.0.0.1", port)
    try:
        assert lb.url == "http://{host}:%d/" % port
        with urllib.request.urlopen("http://127.0.0.1:{}/".format(port), timeout=5) as resp:
            assert resp.read().decode() == "lab-builder from " + str(tmp_path / "liab")
        with pytest.raises(RuntimeError, match="already in use"):
            labbuilder.start(_checkout(tmp_path / "other"), "127.0.0.1", port)
    finally:
        lb.stop()
    assert lb.proc.poll() is not None


def test_start_says_why_it_cannot_run(tmp_path):
    with pytest.raises(RuntimeError, match="no webui/run-local.py"):
        labbuilder.start(tmp_path, "127.0.0.1", _free_port())
    broken = _checkout(tmp_path / "broken", "raise SystemExit('lab-builder needs something')\n")
    with pytest.raises(RuntimeError, match="lab-builder needs something"):
        labbuilder.start(broken, "127.0.0.1", _free_port())


def test_csp_frames_the_lab_builder_on_the_requests_host():
    assert "frame-src http://192.168.1.5:8677;" in csp("http://{host}:8677/", "192.168.1.5")
    assert "frame-src http://[::1]:8677;" in csp("http://{host}:8677/", "[::1]")
    assert "frame-src https://lb.example;" in csp("https://lb.example/lab-builder/", "ignored")


def test_server_policy_follows_allowed_hosts_only(labinabox_checkout):
    api = Api(labinabox_checkout, "http://{host}:8677/", can_save=True)
    server = make_server(api, "127.0.0.1", 0, "t", ("127.0.0.1", "localhost", "lab.example"), token="t0ken")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        def policy(host: str) -> str:
            req = urllib.request.Request("http://127.0.0.1:{}/".format(server.server_address[1]), headers={"Host": host})
            try:
                with urllib.request.urlopen(req, timeout=5) as resp:
                    return resp.headers["Content-Security-Policy"]
            except urllib.error.HTTPError as exc:
                return exc.headers["Content-Security-Policy"]

        assert "frame-src http://lab.example:8677;" in policy("lab.example:8678")
        assert "frame-src http://localhost:8677;" in policy("evil.example")
        status = urllib.request.urlopen("http://127.0.0.1:{}/api?action=labinabox".format(server.server_address[1]))
        assert json.loads(status.read())["builder_url"] == "http://{host}:8677/"
    finally:
        server.shutdown()
        server.server_close()


def _serve_once(monkeypatch, seen):
    real_serve = ThreadingHTTPServer.serve_forever

    def serve(self):
        threading.Thread(target=real_serve, args=(self,), daemon=True).start()
        url = "http://127.0.0.1:{}/api?action=labinabox".format(self.server_address[1])
        seen["liab"] = json.loads(urllib.request.urlopen(url, timeout=5).read())
        self.shutdown()
        raise KeyboardInterrupt

    monkeypatch.setattr(ThreadingHTTPServer, "serve_forever", serve)


def test_builder_command_starts_and_stops_the_lab_builder(monkeypatch, tmp_path, labinabox_checkout):
    seen, started = {}, []
    _serve_once(monkeypatch, seen)
    real_start = labbuilder.start

    def start(checkout, host, port):
        lb = real_start(_checkout(tmp_path / "liab"), host, port)
        started.append(lb)
        return lb

    monkeypatch.setattr(labbuilder, "start", start)
    port = _free_port()
    r = CliRunner().invoke(builder_cmd, ["--port", "0", "--no-open", "--no-fetch-sources", "--labinabox",
                                         str(labinabox_checkout), "--start-lab-builder",
                                         "--lab-builder-port", str(port)])
    assert r.exit_code == 0, r.output
    assert seen["liab"]["builder_url"] == "http://{host}:%d/" % port
    assert re.search(r"lab-in-a-box lab-builder .*→ http://127\.0\.0\.1:%d/" % port, r.output)
    assert started[0].proc.poll() is not None


def test_builder_command_uses_a_given_lab_builder(monkeypatch, labinabox_checkout):
    seen = {}
    _serve_once(monkeypatch, seen)
    monkeypatch.setattr(labbuilder, "start", lambda *a: pytest.fail("must not start a lab-builder"))
    monkeypatch.setenv("RODEO_LAB_BUILDER_URL", "https://automation.example:8443/")
    r = CliRunner().invoke(builder_cmd, ["--port", "0", "--no-open", "--no-fetch-sources", "--labinabox", str(labinabox_checkout)])
    assert r.exit_code == 0, r.output
    assert seen["liab"]["builder_url"] == "https://automation.example:8443/"


def test_builder_command_links_the_published_lab_builder_by_default(monkeypatch, labinabox_checkout):
    seen = {}
    _serve_once(monkeypatch, seen)
    monkeypatch.setattr(labbuilder, "start", lambda *a: pytest.fail("must not start a lab-builder"))
    r = CliRunner().invoke(builder_cmd, ["--port", "0", "--no-open", "--no-fetch-sources", "--labinabox", str(labinabox_checkout)])
    assert r.exit_code == 0, r.output
    assert seen["liab"]["builder_url"].startswith("https://rmahique.github.io/lab-in-a-box/lab-builder/")


def test_builder_command_refuses_to_start_the_lab_builder_off_loopback(monkeypatch):
    monkeypatch.setattr(labbuilder, "start", lambda *a: pytest.fail("must not start a lab-builder"))
    r = CliRunner().invoke(builder_cmd, ["--host", "0.0.0.0", "--expose", "--start-lab-builder", "--no-open"])
    assert r.exit_code == 2
    assert "--start-lab-builder needs a loopback --host" in r.output


def test_builder_command_falls_back_to_the_published_lab_builder(monkeypatch, labinabox_checkout):
    seen = {}
    _serve_once(monkeypatch, seen)
    r = CliRunner().invoke(builder_cmd, ["--port", "0", "--no-open", "--no-fetch-sources", "--labinabox",
                                         str(labinabox_checkout), "--start-lab-builder"])
    assert r.exit_code == 0, r.output
    assert "linking to the published lab-builder" in r.output
    assert seen["liab"]["builder_url"].startswith("https://rmahique.github.io/lab-in-a-box/lab-builder/")
