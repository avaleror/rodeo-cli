"""Rodeo Builder live server (rodeo/builder/server.py), saving into profiles
(rodeo/profile_install.py) and `rodeo new --from-zip`."""
from __future__ import annotations

import http.client
import io
import json
import re
import stat
import subprocess
import threading
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from rodeo.builder.api import Api
from rodeo.builder.server import TOKEN_HEADER, host_allowed, make_server
from rodeo.commands.builder_cmd import builder_cmd
from rodeo.commands.new_cmd import new_cmd
from rodeo.labseed import custom_profile_dir
from rodeo.profile_install import ProfileFile, install_profile, read_zip

PLAN = b"type: rancher\nname: ride\ndeployment_target: instruqt\nstory:\n  language: en\n"


def _files(**extra: bytes) -> list[ProfileFile]:
    files = [ProfileFile("rodeo-plan.yaml", PLAN), ProfileFile("story/01-welcome.md", b"# Welcome\n"),
             ProfileFile("checks/check-welcome.sh", b"#!/bin/sh\nexit 0\n", True)]
    return files + [ProfileFile(path.replace("__", "/"), data) for path, data in extra.items()]


# ── install_profile ─────────────────────────────────────────────────────────

def test_install_lays_the_files_over_the_base_profile():
    dest = install_profile("ride", _files(), base="rancher")
    assert dest == custom_profile_dir("ride")
    assert (dest / "definition.yaml").is_file()                      # from the base
    assert (dest / "story" / "01-welcome.md").read_text() == "# Welcome\n"
    assert (dest / "checks" / "check-welcome.sh").stat().st_mode & stat.S_IXUSR
    assert not (dest / "story" / "01-welcome.md").stat().st_mode & stat.S_IXUSR
    plan = yaml.safe_load((dest / "rodeo-plan.yaml").read_text())
    assert plan["name"] == "ride" and plan["deployment_target"] == "instruqt"
    assert not list(dest.parent.glob(".ride.*"))


def test_install_keeps_a_safe_plan_as_written():
    text = b"# my notes\ntype: rancher\nname: ride\ndeployment_target: baremetal\n"
    dest = install_profile("ride", [ProfileFile("rodeo-plan.yaml", text)])
    assert (dest / "rodeo-plan.yaml").read_bytes() == text
    assert sorted(p.name for p in dest.iterdir()) == ["rodeo-plan.yaml"]


def test_install_refuses_to_overwrite_without_force():
    install_profile("ride", _files())
    with pytest.raises(FileExistsError):
        install_profile("ride", _files(**{"README.md": b"new"}))
    assert not (custom_profile_dir("ride") / "README.md").exists()
    install_profile("ride", _files(**{"README.md": b"new"}), force=True)
    assert (custom_profile_dir("ride") / "README.md").read_bytes() == b"new"


@pytest.mark.parametrize("path", ["../escape.yaml", "/etc/passwd", "story/../../x", "custom/scripts/10-run.sh",
                                  ".hidden", "story/.git/config", "notes.txt", "story\\x.md", "story//x.md",
                                  "story/strings/es/../x/@default/content.json", "story/strings/es/x/@default/x.json",
                                  "story/strings/ES/x/@default/content.json", "story/strings/es/.x/@default/content.json",
                                  "story/strings/es/x/other/content.json", "story/strings/es/x.md"])
def test_install_refuses_paths_a_builder_rodeo_never_has(path):
    with pytest.raises(ValueError):
        install_profile("ride", _files() + [ProfileFile(path, b"x")])
    assert not custom_profile_dir("ride").exists()


def test_install_keeps_the_rmstory_translation_store():
    leaf = "story/strings/pt-br/intro.1/@default/content.json"
    install_profile("ride", _files() + [ProfileFile(leaf, b'{"content": "Ola"}')])
    assert (custom_profile_dir("ride") / leaf).read_bytes() == b'{"content": "Ola"}'


def test_install_checks_name_modes_sizes_and_plan(monkeypatch):
    with pytest.raises(ValueError, match="profile name"):
        install_profile("Ride On", _files())
    with pytest.raises(ValueError, match="only checks/"):
        install_profile("ride", [ProfileFile("rodeo-plan.yaml", PLAN), ProfileFile("README.md", b"x", True)])
    with pytest.raises(ValueError, match="no rodeo-plan.yaml"):
        install_profile("ride", [ProfileFile("README.md", b"x")])
    with pytest.raises(ValueError, match="deployment_target"):
        install_profile("ride", [ProfileFile("rodeo-plan.yaml", b"type: rancher\ndeployment_target: mars\n")])
    with pytest.raises(FileNotFoundError):
        install_profile("other", [ProfileFile("rodeo-plan.yaml", PLAN)], base="no-such-profile")
    monkeypatch.setattr("rodeo.profile_install.MAX_FILE_BYTES", 4)
    with pytest.raises(ValueError, match="larger"):
        install_profile("ride", _files())
    assert not custom_profile_dir("ride").exists() and not custom_profile_dir("other").exists()


# ── zips ────────────────────────────────────────────────────────────────────

def _zip(entries: dict[str, tuple[bytes, int]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, (data, mode) in entries.items():
            info = zipfile.ZipInfo(name)
            info.external_attr = mode << 16
            zf.writestr(info, data)
    return buf.getvalue()


def test_read_zip_strips_the_top_directory_and_keeps_exec_bits(tmp_path):
    z = tmp_path / "ride.zip"
    z.write_bytes(_zip({"ride/rodeo-plan.yaml": (PLAN, 0o100644),
                        "ride/checks/check-a.sh": (b"#!/bin/sh\n", 0o100755)}))
    files = {f.path: f for f in read_zip(z)}
    assert set(files) == {"rodeo-plan.yaml", "checks/check-a.sh"}
    assert files["checks/check-a.sh"].executable and not files["rodeo-plan.yaml"].executable


def test_read_zip_refuses_links_traversal_and_oversize(tmp_path, monkeypatch):
    link = tmp_path / "link.zip"
    link.write_bytes(_zip({"ride/rodeo-plan.yaml": (PLAN, 0o100644), "ride/story/x.md": (b"/etc/passwd", 0o120777)}))
    with pytest.raises(ValueError, match="link"):
        read_zip(link)
    trav = tmp_path / "trav.zip"
    trav.write_bytes(_zip({"rodeo-plan.yaml": (PLAN, 0o100644), "../evil.yaml": (b"x", 0o100644)}))
    with pytest.raises(ValueError, match="not a file a builder rodeo can hold"):
        read_zip(trav)
    monkeypatch.setattr("rodeo.profile_install.MAX_TOTAL_BYTES", 10)
    big = tmp_path / "big.zip"
    big.write_bytes(_zip({"ride/rodeo-plan.yaml": (PLAN, 0o100644)}))
    with pytest.raises(ValueError, match="larger"):
        read_zip(big)


def test_rodeo_new_from_zip_uses_the_manifest_base(tmp_path):
    z = tmp_path / "ride.zip"
    z.write_bytes(_zip({"ride/rodeo-plan.yaml": (PLAN, 0o100644),
                        "ride/builder.yaml": (b"base: rancher\n", 0o100644),
                        "ride/story/01-welcome.md": (b"# Welcome\n", 0o100644)}))
    r = CliRunner().invoke(new_cmd, ["ride", "--from-zip", str(z)])
    assert r.exit_code == 0, r.output
    dest = custom_profile_dir("ride")
    assert (dest / "definition.yaml").is_file() and (dest / "story" / "01-welcome.md").is_file()
    assert "on 'rancher'" in r.output and "rodeo up --profile ride" in r.output
    again = CliRunner().invoke(new_cmd, ["ride", "--from-zip", str(z)])
    assert again.exit_code == 1 and "already exists" in again.output
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    assert CliRunner().invoke(new_cmd, ["x", "--from-zip", str(bad)]).exit_code == 1


def test_rodeo_new_still_checks_the_bundled_base():
    r = CliRunner().invoke(new_cmd, ["ride", "--from", "nope"])
    assert r.exit_code == 1 and "not a bundled base" in r.output


# ── server ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def server(labinabox_checkout):
    srv = make_server(Api(labinabox_checkout, can_save=True), "127.0.0.1", 0, "9.9.9", token="t0ken",
                      lab_builder_url="https://lb.example/lab-builder/")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def _req(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(method, path, body=body, headers={"Host": "127.0.0.1:{}".format(port), **(headers or {})})
    res = conn.getresponse()
    data = res.read()
    conn.close()
    return res.status, dict(res.getheaders()), data


def _save(port, payload, token="t0ken", ctype="application/json"):
    status, _, data = _req(port, "POST", "/api?action=save", json.dumps(payload).encode(),
                           {TOKEN_HEADER: token, "Content-Type": ctype})
    return status, json.loads(data)


def test_server_serves_the_page_with_token_and_headers(server):
    status, headers, data = _req(server, "GET", "/")
    page = data.decode()
    assert status == 200 and '<meta name="rodeo-builder-token" content="t0ken">' in page
    assert "9.9.9" in page and "__RODEOVERSION__" not in page and "RB_STATIC" not in page
    assert "frame-src https://lb.example" in headers["Content-Security-Policy"]
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert _req(server, "GET", "/app.js")[0] == 200
    assert _req(server, "GET", "/../../etc/passwd")[0] == 404
    assert _req(server, "GET", "/%2e%2e/%2e%2e/etc/passwd")[0] == 404


def test_server_answers_the_api_live(server):
    status, _, data = _req(server, "GET", "/api?action=labinabox")
    liab = json.loads(data)
    assert status == 200 and liab["error"] == "" and [a["name"] for a in liab["kclusters"]] == ["rke2"]
    info = json.loads(_req(server, "GET", "/api?action=server")[2])
    assert info["save"] is True and info["profiles"] == [] and "rancher" in info["bundled"]
    assert _req(server, "GET", "/api?action=nope")[0] == 404


def test_server_refuses_foreign_hosts_and_unauthenticated_writes(server):
    assert _req(server, "GET", "/", headers={"Host": "evil.example"})[0] == 403
    payload = {"name": "ride", "files": [{"path": "rodeo-plan.yaml", "content": PLAN.decode()}]}
    assert _save(server, payload, token="")[0] == 403
    assert _save(server, payload, token="wrong")[0] == 403
    assert _save(server, payload, ctype="text/plain")[0] == 415
    assert not custom_profile_dir("ride").exists()


def test_server_saves_into_profiles(server):
    payload = {"name": "ride", "base": "rancher", "files": [
        {"path": "rodeo-plan.yaml", "content": PLAN.decode()},
        {"path": "checks/check-a.sh", "content": "#!/bin/sh\n", "executable": True}]}
    status, body = _save(server, payload)
    assert status == 200 and body["profile"] == "ride" and Path(body["path"]) == custom_profile_dir("ride")
    assert (custom_profile_dir("ride") / "definition.yaml").is_file()
    assert _save(server, payload) == (409, {"error": _save(server, payload)[1]["error"], "exists": True})
    assert _save(server, {**payload, "force": True})[0] == 200
    assert "ride" in json.loads(_req(server, "GET", "/api?action=server")[2])["profiles"]
    status, body = _save(server, {**payload, "name": "x", "files": payload["files"] + [{"path": "../x", "content": ""}]})
    assert status == 400 and "not a file" in body["error"]
    assert _save(server, {"name": "x"})[0] == 400


def test_server_serves_the_settings_page_on_its_own_address(server):
    for path in ("/rodeo-builder/settings/", "/rodeo-builder/settings", "/rodeo-builder/settings/index.html"):
        status, headers, data = _req(server, "GET", path)
        page = data.decode()
        assert status == 200 and headers["Content-Type"].startswith("text/html")
        assert '<meta name="rodeo-builder-token" content="t0ken">' in page
        assert 'src="/rodeo-builder/settings/settings.js"' in page and 'href="/style.css"' in page
    status, headers, data = _req(server, "GET", "/rodeo-builder/settings/settings.js")
    assert status == 200 and headers["Content-Type"].startswith("text/javascript") and b'"/api"' in data
    assert _req(server, "GET", "/rodeo-builder/settings/../../app.js")[0] == 404
    assert _req(server, "GET", "/rodeo-builder/settings/nope.js")[0] == 404
    assert _req(server, "GET", "/ca.pem")[0] == 404


def test_server_speaks_https_with_the_generated_ca_and_serves_it(tmp_path, labinabox_checkout):
    import ssl

    from rodeo.builder import tls

    made = tls.ensure("127.0.0.1", directory=tmp_path / "tls")
    srv = make_server(Api(labinabox_checkout, can_save=True), "127.0.0.1", 0, "9.9.9", token="t0ken",
                      ssl_context=tls.ssl_context(made.cert, made.key), ca_pem=made.ca.read_bytes())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        port = srv.server_address[1]
        ctx = ssl.create_default_context(cafile=str(made.ca))
        conn = http.client.HTTPSConnection("127.0.0.1", port, timeout=10, context=ctx)
        conn.request("GET", "/ca.pem", headers={"Host": "127.0.0.1:{}".format(port)})
        res = conn.getresponse()
        assert res.status == 200 and res.getheader("Content-Type") == "application/x-pem-file"
        assert res.read() == made.ca.read_bytes()
        conn.close()
        with pytest.raises(ssl.SSLError):
            http.client.HTTPSConnection("127.0.0.1", port, timeout=10,
                                        context=ssl.create_default_context()).request("GET", "/")
    finally:
        srv.shutdown()
        srv.server_close()


def test_host_allowed_strips_ports_and_brackets():
    assert host_allowed("127.0.0.1:8678", ("127.0.0.1",))
    assert host_allowed("[::1]:8678", ("::1",))
    assert host_allowed("LOCALHOST", ("localhost",))
    assert not host_allowed("127.0.0.1.evil:80", ("127.0.0.1",))
    assert not host_allowed(None, ("localhost",))


def test_builder_command_needs_expose_for_other_hosts():
    r = CliRunner().invoke(builder_cmd, ["--host", "0.0.0.0", "--no-open"])
    assert r.exit_code == 2 and "--expose" in r.output
    r = CliRunner().invoke(builder_cmd, ["--source", "nope", "--no-open"])
    assert r.exit_code == 2 and "ID=CHECKOUT" in r.output


def test_builder_command_wants_the_tls_certificate_and_key_together(tmp_path):
    cert = tmp_path / "c.pem"
    cert.write_text("x")
    r = CliRunner().invoke(builder_cmd, ["--tls-cert", str(cert), "--no-open"])
    assert r.exit_code == 2 and "--tls-key" in r.output


def _https_get(port, path, host, cafile, server_hostname):
    """HTTP status of GET *path* over TLS to 127.0.0.1:*port*, trusting *cafile*, with the
    certificate checked for *server_hostname* and *host* as the Host header."""
    import socket
    import ssl

    ctx = ssl.create_default_context(cafile=str(cafile))
    with socket.create_connection(("127.0.0.1", port), timeout=10) as raw, \
            ctx.wrap_socket(raw, server_hostname=server_hostname) as tls_sock:
        tls_sock.sendall("GET {} HTTP/1.1\r\nHost: {}\r\nConnection: close\r\n\r\n".format(path, host).encode())
        data = b""
        while chunk := tls_sock.recv(65536):
            data += chunk
    return int(data.split(b" ", 2)[1])


def test_an_exposed_builder_serves_https_with_its_own_ca(monkeypatch, labinabox_checkout, tmp_path):
    from rodeo.builder import tls

    monkeypatch.setattr(tls, "tls_dir", lambda: tmp_path / "tls")
    monkeypatch.setattr(tls.socket, "getfqdn", lambda *a: "builder.example.test")
    monkeypatch.setattr(tls.socket, "gethostname", lambda *a: "builder")
    real_gai = tls.socket.getaddrinfo
    monkeypatch.setattr(tls.socket, "getaddrinfo",
                        lambda host, port, *a, **k: [] if port is None else real_gai(host, port, *a, **k))
    seen = {}
    real_serve = ThreadingHTTPServer.serve_forever

    def serve(self):
        threading.Thread(target=real_serve, args=(self,), daemon=True).start()
        seen["status"] = _https_get(self.server_address[1], "/api?action=server", "builder.example.test",
                                    tmp_path / "tls" / "ca.pem", "builder.example.test")
        self.shutdown()
        raise KeyboardInterrupt

    monkeypatch.setattr(ThreadingHTTPServer, "serve_forever", serve)
    r = CliRunner().invoke(builder_cmd, ["--host", "0.0.0.0", "--port", "0", "--expose",
                                         "--no-open", "--no-fetch-sources", "--labinabox", str(labinabox_checkout)])
    assert r.exit_code == 0, r.output
    assert seen["status"] == 200
    assert re.search(r"https://builder\.example\.test:\d+/", r.output) and "Made a CA" in r.output
    assert (tmp_path / "tls" / "ca.pem").is_file()


def test_builder_command_serves_until_interrupted(monkeypatch, labinabox_checkout):
    seen = {}
    real_serve = ThreadingHTTPServer.serve_forever

    def serve(self):
        threading.Thread(target=real_serve, args=(self,), daemon=True).start()
        seen["liab"] = json.loads(_req(self.server_address[1], "GET", "/api?action=labinabox")[2])
        self.shutdown()
        raise KeyboardInterrupt

    monkeypatch.setattr(ThreadingHTTPServer, "serve_forever", serve)
    r = CliRunner().invoke(builder_cmd, ["--port", "0", "--no-open", "--no-fetch-sources",
                                         "--labinabox", str(labinabox_checkout)])
    assert r.exit_code == 0, r.output
    assert re.search(r"http://127\.0\.0\.1:\d+/", r.output)
    assert [a["name"] for a in seen["liab"]["addons"]] == ["smlm", "client_registration"]


def test_builders_started_together_fetch_lab_in_a_box_one_at_a_time(tmp_path, monkeypatch):
    import sys

    from rodeo import labinabox_host
    from rodeo.commands import builder_cmd as cmd

    busy = tmp_path / "busy"
    fetch = ("import os, sys, time\n"
             "fd = os.open(sys.argv[1], os.O_CREAT | os.O_EXCL)\n"
             "time.sleep(0.3)\n"
             "os.close(fd); os.unlink(sys.argv[1])\n")
    monkeypatch.setattr(labinabox_host, "local_override", lambda: None)
    monkeypatch.setattr(labinabox_host, "source", lambda cfg: ("https://example.invalid/liab.git", "1.0.0"))
    monkeypatch.setattr(labinabox_host, "resolve_ref", lambda repo, ref: ref)
    monkeypatch.setattr(labinabox_host, "checkout_dir", lambda ref: tmp_path / "vendor" / ref)
    monkeypatch.setattr(labinabox_host, "fetch_commands",
                        lambda repo, ref, dest: [[sys.executable, "-c", fetch, str(busy)]])
    results = []
    threads = [threading.Thread(target=lambda: results.append(cmd._labinabox_checkout(None))) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [(tmp_path / "vendor" / "1.0.0", "1.0.0", "")] * 2


def test_own_names_are_the_host_names_and_interface_addresses(monkeypatch):
    from rodeo.commands import builder_cmd as cmd

    out = ("1: lo    inet 127.0.0.1/8 scope host lo\n"
           "2: eth0    inet 192.168.1.5/24 brd 192.168.1.255 scope global eth0\n"
           "2: eth0    inet6 fe80::1/64 scope link\n"
           "3: wg0    inet6 2001:db8::7/64 scope global\n")
    monkeypatch.setattr(cmd.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, out, ""))
    monkeypatch.setattr(cmd.socket, "gethostname", lambda: "Box")
    monkeypatch.setattr(cmd.socket, "getfqdn", lambda: "box.lab.example")
    assert cmd._own_names() == ("box", "box.lab.example", "127.0.0.1", "192.168.1.5", "2001:db8::7")


def test_builder_on_every_interface_answers_to_its_own_names_only(monkeypatch, labinabox_checkout, tmp_path):
    from rodeo.builder import tls
    from rodeo.commands import builder_cmd as cmd

    monkeypatch.setattr(cmd, "_own_names", lambda: ("box.lab.example", "192.168.1.5"))
    monkeypatch.setattr(tls, "tls_dir", lambda: tmp_path / "tls")
    seen = {}
    real_serve = ThreadingHTTPServer.serve_forever

    def serve(self):
        threading.Thread(target=real_serve, args=(self,), daemon=True).start()
        port = self.server_address[1]
        for host in ("box.lab.example", "192.168.1.5", "alias.example", "evil.example"):
            seen[host] = _https_get(port, "/", f"{host}:{port}", tmp_path / "tls" / "ca.pem", "box.lab.example")
        self.shutdown()
        raise KeyboardInterrupt

    monkeypatch.setattr(ThreadingHTTPServer, "serve_forever", serve)
    r = CliRunner().invoke(builder_cmd, ["--host", "0.0.0.0", "--expose", "--allow-host", "alias.example",
                                         "--port", "0", "--no-open", "--no-fetch-sources",
                                         "--labinabox", str(labinabox_checkout)])
    assert r.exit_code == 0, r.output
    assert "box.lab.example" in r.output
    assert seen["box.lab.example"] == seen["192.168.1.5"] == seen["alias.example"] == 200
    assert seen["evil.example"] != 200
