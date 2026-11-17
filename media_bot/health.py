"""Small local HTTP endpoint for uptime monitors (stdlib only)."""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = logging.getLogger(__name__)


class _Handler(BaseHTTPRequestHandler):
    server: HealthServer

    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] not in {"/", "/healthz"}:
            self.send_error(404)
            return
        try:
            payload = self.server.payload()
            status = 200
        except Exception as exc:
            LOG.warning("Health payload failed: %s", exc)
            payload = {"status": "error"}
            status = 500
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:
        return


class HealthServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], payload: Callable[[], dict]):
        super().__init__(address, _Handler)
        self.payload = payload


def start_health_server(host: str, port: int,
                        payload: Callable[[], dict]) -> HealthServer | None:
    """Bind and serve in a daemon thread; ``port <= 0`` disables the endpoint."""
    if port <= 0:
        return None
    try:
        server = HealthServer((host, port), payload)
    except OSError as exc:
        LOG.warning("Health endpoint could not bind %s:%s: %s", host, port, exc)
        return None
    thread = threading.Thread(target=server.serve_forever, name="healthz", daemon=True)
    thread.start()
    LOG.info("Health endpoint: http://%s:%s/healthz", host, port)
    return server
