"""Tests for the MCP server: protocol handshake, tool surface, argv building."""
from __future__ import annotations

import io
import json

import pytest

from rodeo.mcp import tools as mcp_tools
from rodeo.mcp.exec import clamp, rodeo_argv
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
