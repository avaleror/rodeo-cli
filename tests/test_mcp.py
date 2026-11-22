"""Tests for the MCP server: protocol handshake, tool surface, argv building."""
from __future__ import annotations

import io
import json
import os
import queue
import signal
import sys
import threading
import time

import pytest

from rodeo.mcp import tools as mcp_tools
from rodeo.mcp.exec import clamp, rodeo_argv, run_rodeo
from rodeo.mcp.server import SUPPORTED_PROTOCOLS, RodeoMCPServer, serve


def _req(method: str, params: dict | None = None, req_id: int | None = 1) -> dict:
    msg: dict = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    if req_id is not None:
        msg["id"] = req_id
    return msg


# --- protocol ---------------------------------------------------------------

def test_initialize_echoes_known_protocol_and_server_info():
    server = RodeoMCPServer()
    reply = server.handle(_req("initialize", {"protocolVersion": "2024-11-05"}))
    result = reply["result"]
    assert result["protocolVersion"] == "2024-11-05"
    assert result["serverInfo"]["name"] == "rodeo"
    assert "tools" in result["capabilities"]


def test_initialize_falls_back_to_newest_protocol():
    reply = RodeoMCPServer().handle(_req("initialize", {"protocolVersion": "1999-01-01"}))
    assert reply["result"]["protocolVersion"] == SUPPORTED_PROTOCOLS[-1]


def test_notifications_get_no_reply():
    assert RodeoMCPServer().handle(_req("notifications/initialized", req_id=None)) is None


def test_unknown_method_is_a_jsonrpc_error():
    reply = RodeoMCPServer().handle(_req("tools/nope"))
    assert reply["error"]["code"] == -32601


def test_bad_json_frame_yields_parse_error():
    out = io.StringIO()
    serve(stdin=io.StringIO("{not json}\n"), stdout=out)
    assert json.loads(out.getvalue())["error"]["code"] == -32700


def test_serve_round_trip_over_stdio():
    frames = "\n".join(json.dumps(m) for m in (
        _req("initialize", {"protocolVersion": SUPPORTED_PROTOCOLS[-1]}),
        _req("notifications/initialized", req_id=None),
        _req("tools/list", req_id=2),
    ))
    out = io.StringIO()
    assert serve(stdin=io.StringIO(frames + "\n"), stdout=out) == 0
    replies = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [r["id"] for r in replies] == [1, 2]
    assert any(t["name"] == "rodeo_status" for t in replies[1]["result"]["tools"])


# --- tool surface -----------------------------------------------------------

def test_read_only_server_hides_lifecycle_tools():
    names = {t["name"] for t in
             RodeoMCPServer().handle(_req("tools/list"))["result"]["tools"]}
    assert "rodeo_status" in names
    assert "rodeo_clean" not in names and "rodeo_up" not in names


def test_allow_write_offers_lifecycle_tools():
    names = {t["name"] for t in RodeoMCPServer(allow_write=True)
             .handle(_req("tools/list"))["result"]["tools"]}
    assert {"rodeo_up", "rodeo_deploy", "rodeo_clean", "rodeo_destroy"} <= names


def test_calling_a_hidden_tool_is_a_tool_error_not_a_crash():
    result = RodeoMCPServer().handle(
        _req("tools/call", {"name": "rodeo_clean", "arguments": {}}))["result"]
    assert result["isError"] is True
    assert "unknown tool" in result["content"][0]["text"]


def test_every_tool_descriptor_has_an_object_schema():
    for tool in mcp_tools.tool_map(allow_write=True).values():
        schema = tool.descriptor()["inputSchema"]
        assert schema["type"] == "object"
        assert isinstance(schema["properties"], dict)


def test_tool_names_are_unique():
    names = [t.name for t in mcp_tools.READ_TOOLS + mcp_tools.WRITE_TOOLS]
    assert len(names) == len(set(names))


def test_missing_required_argument_is_reported():
    result = RodeoMCPServer().handle(
        _req("tools/call", {"name": "rodeo_logs", "arguments": {}}))["result"]
    assert result["isError"] is True
    assert "vm" in result["content"][0]["text"]


# --- argv building ----------------------------------------------------------

@pytest.mark.parametrize("name,params,expected", [
    ("rodeo_status", {"output": "json"}, ["status", "--output", "json"]),
    ("rodeo_logs", {"vm": "eib", "lines": 20},
     ["logs", "eib", "--lines", "20", "--no-follow"]),
    ("rodeo_deploy", {"from_phase": "labinabox", "force": True},
     ["deploy", "--from", "labinabox", "--force", "--no-tui"]),
    ("rodeo_clean", {"all": True}, ["clean", "--all", "--yes"]),
    ("rodeo_up", {"profile": "harvester"},
     ["up", "--profile", "harvester", "--yes", "--no-tmux"]),
    ("rodeo_plan", {"param": ["a=1", "b=2"]},
     ["plan", "--param", "a=1", "--param", "b=2"]),
    ("rodeo_plan", {"output": "json", "param": ["a=1"]},
     ["plan", "--output", "json", "--param", "a=1"]),
    ("rodeo_help", {}, ["--help"]),
    ("rodeo_help", {"command": "deploy"}, ["deploy", "--help"]),
])
def test_argv_building(name, params, expected):
    assert mcp_tools.tool_map(allow_write=True)[name].argv(params) == expected


def test_lab_dir_is_not_passed_to_the_command_line():
    tool = mcp_tools.tool_map(allow_write=True)["rodeo_status"]
    assert "--lab-dir" not in tool.argv({"lab_dir": "/labs/x"})
    assert "lab_dir" in tool.input_schema()["properties"]


def test_tool_call_runs_rodeo_in_the_lab_dir(monkeypatch, tmp_path):
    seen: dict = {}

    def fake_run(args, *, cwd=None, **kwargs):
        seen.update(args=args, cwd=cwd)
        return 0, "ok"

    monkeypatch.setattr(mcp_tools, "run_rodeo", fake_run)
    server = RodeoMCPServer(allow_write=True, lab_dir=tmp_path)
    result = server.handle(_req("tools/call", {"name": "rodeo_status",
                                               "arguments": {}}))["result"]
    assert result["isError"] is False
    assert result["content"][0]["text"] == "ok"
    assert seen["args"] == ["status"] and seen["cwd"] == tmp_path


def test_argument_lab_dir_overrides_the_server_default(monkeypatch, tmp_path):
    seen: dict = {}
    monkeypatch.setattr(mcp_tools, "run_rodeo",
                        lambda args, *, cwd=None, **kw: (seen.update(cwd=cwd), (0, "ok"))[1])
    RodeoMCPServer(lab_dir=tmp_path).handle(_req(
        "tools/call", {"name": "rodeo_status", "arguments": {"lab_dir": "/labs/other"}}))
    assert seen["cwd"] == "/labs/other"


def test_non_zero_exit_becomes_a_tool_error(monkeypatch):
    monkeypatch.setattr(mcp_tools, "run_rodeo", lambda *a, **k: (2, "boom"))
    result = RodeoMCPServer().handle(
        _req("tools/call", {"name": "rodeo_doctor", "arguments": {}}))["result"]
    assert result["isError"] is True
    assert "exited 2" in result["content"][0]["text"]


# --- engines (native and external) -----------------------------------------

def test_engines_tool_reports_native_and_external_engines():
    result = RodeoMCPServer().handle(
        _req("tools/call", {"name": "rodeo_engines", "arguments": {}}))["result"]
    rows = json.loads(result["content"][0]["text"])
    by_type = {r["type"]: r for r in rows}
    assert by_type["lab-in-a-box"]["engine"] == "external"
    assert by_type["suse-virt"]["engine"] == "native"
    assert "labinabox" in by_type["lab-in-a-box"]["phases"]


# --- exec helpers -----------------------------------------------------------

def test_rodeo_argv_uses_an_absolute_binary(monkeypatch):
    monkeypatch.setattr("rodeo.mcp.exec.find_rodeo_bin", lambda: "/usr/local/bin/rodeo")
    assert rodeo_argv(["status"]) == ["/usr/local/bin/rodeo", "status"]


def test_clamp_keeps_the_tail():
    clamped = clamp("x" * 50 + "TAIL", limit=10)
    assert clamped.endswith("TAIL")
    assert "omitted" in clamped


# --- process groups: timeout, cancel, EOF ----------------------------------

STUB = '''#!{python}
"""Stub rodeo: start a grandchild that ignores SIGTERM, record its pid, then wait."""
import os, subprocess, sys, time
child = subprocess.Popen(
    [sys.executable, "-c",
     "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
with open(os.environ["STUB_PIDFILE"] + ".tmp", "w") as fh:
    fh.write(str(child.pid))
os.rename(os.environ["STUB_PIDFILE"] + ".tmp", os.environ["STUB_PIDFILE"])
print("stub started", flush=True)
time.sleep(float(os.environ.get("STUB_SLEEP", "5")))
'''


def _alive(pid: int) -> bool:
    """True while ``pid`` exists and is not a zombie."""
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().rsplit(")", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False


def _wait_dead(pid: int, within: float = 3.0) -> bool:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return not _alive(pid)


def _wait_file(path, within: float = 10.0) -> int:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if path.exists():
            return int(path.read_text())
        time.sleep(0.02)
    raise AssertionError(f"stub never wrote {path}")


@pytest.fixture
def stub_rodeo(monkeypatch, tmp_path):
    """A stub rodeo binary; yields the pid file its grandchild's pid lands in."""
    stub = tmp_path / "rodeo"
    stub.write_text(STUB.format(python=sys.executable))
    stub.chmod(0o755)
    pidfile = tmp_path / "grandchild.pid"
    monkeypatch.setattr("rodeo.mcp.exec.find_rodeo_bin", lambda: str(stub))
    monkeypatch.setattr("rodeo.mcp.exec.KILL_GRACE", 0.5, raising=False)
    monkeypatch.setenv("STUB_PIDFILE", str(pidfile))
    yield pidfile
    if pidfile.exists():  # never leak a sleeper, even when an assertion failed
        try:
            os.kill(int(pidfile.read_text()), signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_timeout_kills_the_grandchild(stub_rodeo):
    code, output = run_rodeo(["status"], timeout=2)
    assert code == 124
    assert output.startswith("timed out after 2s")
    assert _wait_dead(_wait_file(stub_rodeo))


def test_normal_exit_still_returns_output(stub_rodeo, monkeypatch):
    monkeypatch.setenv("STUB_SLEEP", "0")
    code, output = run_rodeo(["status"], timeout=20)
    assert code == 0
    assert "stub started" in output


class _Pipe:
    """A line stream the test writes to while serve() reads it."""

    def __init__(self) -> None:
        self.lines: queue.Queue = queue.Queue()

    def send(self, message: dict) -> None:
        self.lines.put(json.dumps(message) + "\n")

    def close(self) -> None:
        self.lines.put(None)

    def __iter__(self):
        while (line := self.lines.get()) is not None:
            yield line


def _serve_in_thread(pipe: _Pipe, out: io.StringIO, tmp_path) -> threading.Thread:
    thread = threading.Thread(
        target=serve, kwargs={"stdin": pipe, "stdout": out, "lab_dir": tmp_path}, daemon=True)
    thread.start()
    return thread


def test_cancel_kills_the_process_group_and_server_keeps_answering(stub_rodeo, tmp_path):
    pipe, out = _Pipe(), io.StringIO()
    thread = _serve_in_thread(pipe, out, tmp_path)
    pipe.send(_req("tools/call", {"name": "rodeo_status", "arguments": {}}, req_id=7))
    grandchild = _wait_file(stub_rodeo)
    started = time.monotonic()
    pipe.send(_req("notifications/cancelled", {"requestId": 7, "reason": "user"}, req_id=None))
    assert _wait_dead(grandchild), "cancel left the grandchild running"
    assert time.monotonic() - started < 4, "cancel did not stop the call early"
    pipe.send(_req("tools/list", req_id=8))
    pipe.close()
    thread.join(10)
    assert not thread.is_alive()

    replies = {r["id"]: r for r in map(json.loads, out.getvalue().splitlines())}
    cancelled = replies[7]["result"]
    assert cancelled["isError"] is True
    assert cancelled["content"][0]["text"].startswith("cancelled")
    assert any(t["name"] == "rodeo_status" for t in replies[8]["result"]["tools"])


def test_cancel_for_an_unknown_id_is_ignored():
    server = RodeoMCPServer()
    assert server.handle(_req("notifications/cancelled", {"requestId": 99}, req_id=None)) is None
    assert server.cancel(99) is False


def test_eof_kills_calls_in_flight(stub_rodeo, tmp_path):
    pipe, out = _Pipe(), io.StringIO()
    thread = _serve_in_thread(pipe, out, tmp_path)
    pipe.send(_req("tools/call", {"name": "rodeo_status", "arguments": {}}, req_id=3))
    grandchild = _wait_file(stub_rodeo)
    pipe.close()
    thread.join(4)
    assert not thread.is_alive(), "serve() did not return promptly at EOF"
    assert _wait_dead(grandchild)


# --- rodeo_logins ----------------------------------------------------------

def test_no_tool_runs_the_builder_logins_group():
    # 'rodeo logins' manages Builder htpasswd users; it does not show a lab's
    # login details, and no read-only command does. The tool must not exist.
    tools = mcp_tools.tool_map(allow_write=True)
    assert "rodeo_logins" not in tools
    assert all(t.command[:1] != ("logins",) for t in tools.values())
