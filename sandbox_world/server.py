"""HTTP front for the sandbox world: ``python -m sandbox_world [--host H] [--port P]``."""

from __future__ import annotations

import argparse
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .core import App, Store

MAX_BODY = 5 * 1024 * 1024


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "HandsSandbox"

        def log_message(self, *args):  # quiet
            pass

        def _serve(self):
            length = min(int(self.headers.get("Content-Length") or 0), MAX_BODY)
            body = self.rfile.read(length) if length else b""
            resp = app.handle(
                self.command, self.headers.get("Host", ""), self.path, dict(self.headers), body
            )
            self.send_response(resp.status)
            self.send_header("Content-Type", resp.ctype)
            self.send_header("Content-Length", str(len(resp.body)))
            for k, v in resp.headers.items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(resp.body)

        do_GET = do_POST = do_HEAD = _serve

    return Handler


def build_app() -> App:
    public = os.environ.get("HANDS_SANDBOX_PUBLIC", "0") == "1"
    return App(Store(), public=public, state_token=os.environ.get("HANDS_SANDBOX_STATE_TOKEN", ""))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")  # noqa: S104 - container-internal
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    ThreadingHTTPServer((args.host, args.port), make_handler(build_app())).serve_forever()
