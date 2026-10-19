"""lab-in-a-box's dynamic lab-builder, run next to the live Rodeo Builder.

:func:`start` serves a lab-in-a-box checkout's lab-builder (``webui/run-local.py``:
its page and live API) from a separate interpreter on *host*:*port*, the address
``rodeo builder`` listens on. Its URL is :data:`SAME_HOST` with the port, so the
page opens it on whatever host name the browser used for the Rodeo Builder.
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .discovery import _addon_interpreter

SAME_HOST = "{host}"
DEFAULT_PORT = 8677
START_TIMEOUT = 20.0

_RUNNER = r"""
import runpy, socket, sys
mod = runpy.run_path(sys.argv[1], run_name="rodeo_lab_builder")
host, port = sys.argv[2], int(sys.argv[3])
cls = mod["ThreadingHTTPServer"]
if ":" in host:
    cls = type("Server6", (cls,), {"address_family": socket.AF_INET6})
httpd = cls((host, port), mod["Handler"])
print("ready", flush=True)
httpd.serve_forever()
"""


class LabBuilder:
    """A running lab-builder: its URL (``http://{host}:<port>/``) and :meth:`stop`."""

    def __init__(self, proc: subprocess.Popen[str], url: str, shim: tempfile.TemporaryDirectory[str]):
        self.proc, self.url, self._shim = proc, url, shim

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self._shim.cleanup()


def url_for(port: int) -> str:
    return "http://{}:{}/".format(SAME_HOST, port)


def _connect_host(host: str) -> str:
    return {"0.0.0.0": "127.0.0.1", "::": "::1"}.get(host, host)


def start(checkout: Path, host: str, port: int = DEFAULT_PORT, popen=subprocess.Popen,
          timeout: float = START_TIMEOUT) -> LabBuilder:
    """Start the checkout's lab-builder; RuntimeError when it has none or it does not come up."""
    script = checkout / "webui" / "run-local.py"
    if not script.is_file():
        raise RuntimeError("{} has no webui/run-local.py (lab-in-a-box's lab-builder server)".format(checkout))
    try:
        with socket.create_connection((_connect_host(host), port), timeout=1):
            raise RuntimeError("port {} is already in use: pick another with --lab-builder-port".format(port))
    except OSError:
        pass
    shim = tempfile.TemporaryDirectory(prefix="rodeo-lab-builder-")
    log = Path(shim.name) / "lab-builder.log"
    env = {**os.environ}
    wanted = _addon_interpreter(checkout)
    if wanted and not shutil.which(wanted):
        os.symlink(sys.executable, os.path.join(shim.name, wanted))
        env["PATH"] = shim.name + os.pathsep + env.get("PATH", "")
    proc = popen([sys.executable, "-c", _RUNNER, str(script), host, str(port)], cwd=str(checkout),
                 stdin=subprocess.DEVNULL, stdout=log.open("w"), stderr=subprocess.STDOUT, text=True, env=env)
    builder = LabBuilder(proc, url_for(port), shim)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            out = log.read_text(errors="replace").strip() if log.is_file() else ""
            builder.stop()
            raise RuntimeError("lab-in-a-box's lab-builder did not start on port {}: {}".format(port, out[-300:]))
        try:
            with socket.create_connection((_connect_host(host), port), timeout=1):
                return builder
        except OSError:
            time.sleep(0.2)
    builder.stop()
    raise RuntimeError("lab-in-a-box's lab-builder did not answer on port {} within {:.0f} s".format(port, timeout))
