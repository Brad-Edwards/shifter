"""Start one pool server: credentials, firewall, OpenVPN, then the controller loop.

Any failure of OpenVPN, the management connection or the controller ends the
process; the VM restarts the container, and clients reconnect to a healthy
server. A restarted controller starts with no sessions and a fresh ruleset.
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import gcp
from .config import load_config
from .controller import Controller
from .firewall import Firewall
from .management import Management
from .portal import PortalClient
from .server_config import write_config, write_material

_OPENVPN = "/usr/sbin/openvpn"


_LOGGER = logging.getLogger("shifter_openvpn")


def _log(message: str) -> None:
    _LOGGER.info(message)


def _wait_for_socket(path: str, process: subprocess.Popen[bytes], timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while not os.path.exists(path):
        if process.poll() is not None or time.monotonic() > deadline:
            raise RuntimeError("OpenVPN did not open its management socket")
        time.sleep(0.2)


def _serve_health(port: int, healthy: threading.Event) -> None:
    class Health(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            ok = self.path == "/healthz" and healthy.is_set()
            self.send_response(200 if ok else 503)
            self.end_headers()
            self.wfile.write(b"ok\n" if ok else b"unavailable\n")

        def log_message(self, *_args: object) -> None:
            return

    # Bind the container's own interface only: tunnel clients never reach the health endpoint.
    address = socket.gethostbyname(socket.gethostname())
    ThreadingHTTPServer((address, port), Health).serve_forever()


def _heartbeat(controller: Controller) -> None:
    while True:
        time.sleep(controller.heartbeat())


def main() -> int:
    logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(asctime)s %(message)s")
    config = load_config()
    write_material(config.runtime_dir, gcp.read_secret(config.server_secret))
    paths = write_config(config)
    Firewall().install(config.tunnel_network)
    process = subprocess.Popen([_OPENVPN, "--config", paths.config])  # noqa: S603 (fixed argv, no shell, no external input)
    _wait_for_socket(paths.management_socket, process)
    management = Management(paths.management_socket)
    portal = PortalClient(config.portal_url, config.control_audience, config.server_name, gcp.identity_token)
    controller = Controller(management, portal, Firewall(), log=_log)
    healthy = threading.Event()
    threading.Thread(target=_serve_health, args=(config.health_port, healthy), daemon=True).start()
    threading.Thread(target=_heartbeat, args=(controller,), daemon=True, name="heartbeat").start()
    threading.Thread(target=lambda: (process.wait(), os._exit(1)), daemon=True, name="openvpn-watch").start()
    healthy.set()
    _log(f"pool server {config.server_name} ready")
    while (event := management.events.get()) is not None:
        controller.handle(event)
    _log("management connection closed")
    return 1


if __name__ == "__main__":
    sys.exit(main())
