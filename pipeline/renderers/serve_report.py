"""Tiny local HTTP server for the admin edit-mode round-trip.

Serves the already-generated HTML report at ``/`` and accepts a POST at
``/save`` with the JSON patch produced by the admin edit UI in
templates/bank_report_v2.html. The patch is applied via
``tools.category.override.apply_customer_edits`` which mutates rgs.csv for the
specified customer only.

Bound to 127.0.0.1 by design — never exposed beyond localhost. Uses stdlib
only (no Flask/FastAPI dependency).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

logger = logging.getLogger(__name__)


def _make_handler(html_path: str, reports_dir: str):
    class _Handler(BaseHTTPRequestHandler):
        # Quieter access log
        def log_message(self, fmt, *args):
            logger.info("[serve] " + fmt, *args)

        def _send_json(self, status: int, payload: dict):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802 (BaseHTTPRequestHandler API)
            path = self.path.split("?", 1)[0]
            if path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return
            if path in ("/", "/index.html"):
                file_path = html_path
            else:
                # Serve other files from the reports/ directory (e.g. assets).
                rel = path.lstrip("/")
                file_path = os.path.normpath(os.path.join(reports_dir, rel))
                # Prevent path traversal outside reports_dir.
                if not file_path.startswith(os.path.abspath(reports_dir)):
                    self.send_error(403)
                    return
            if not os.path.isfile(file_path):
                self.send_error(404)
                return
            ext = os.path.splitext(file_path)[1].lower()
            ctype = {
                ".html": "text/html; charset=utf-8",
                ".css": "text/css",
                ".js": "application/javascript",
                ".json": "application/json",
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".svg": "image/svg+xml",
                ".pdf": "application/pdf",
            }.get(ext, "application/octet-stream")
            with open(file_path, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):  # noqa: N802
            if self.path != "/save":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length <= 0 or length > 2_000_000:
                self._send_json(400, {"error": "missing or oversized body"})
                return
            try:
                raw = self.rfile.read(length)
                payload = json.loads(raw.decode("utf-8"))
                customer_id = int(payload.get("customer_id"))
                edits = payload.get("edits") or []
                if not isinstance(edits, list):
                    raise ValueError("edits must be a list")
            except Exception as e:
                self._send_json(400, {"error": f"bad payload: {e}"})
                return
            try:
                from tools.category.override import apply_customer_edits
                updated = apply_customer_edits(customer_id, edits)
            except Exception as e:
                logger.exception("apply_customer_edits failed")
                self._send_json(500, {"error": str(e)})
                return
            self._send_json(200, {"updated": updated})

    return _Handler


def _is_headless() -> bool:
    """Best-effort detection of a non-desktop env (SageMaker, Docker, SSH)."""
    if os.environ.get("SAGEMAKER_INTERNAL_IMAGE_URI"):
        return True
    if os.environ.get("AWS_EXECUTION_ENV", "").startswith("AWS_ECS"):
        return True
    # On Linux, no DISPLAY → no GUI browser available.
    import sys as _sys
    if _sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        return True
    return False


def serve(customer_id: int, html_path: str, port: int = 8765, open_browser: bool = True) -> None:
    """Block on a local HTTP server that hosts the report and a /save endpoint."""
    html_path = os.path.abspath(html_path)
    if not os.path.isfile(html_path):
        raise FileNotFoundError(f"Report HTML not found: {html_path}")
    reports_dir = os.path.dirname(html_path)
    handler = _make_handler(html_path, os.path.abspath(reports_dir))
    # Bind to 0.0.0.0 in headless env so SageMaker's JupyterServerProxy can reach it.
    headless = _is_headless()
    bind_host = "0.0.0.0" if headless else "127.0.0.1"
    server = ThreadingHTTPServer((bind_host, port), handler)
    local_url = f"http://127.0.0.1:{port}/"
    print(f"Serving report for customer {customer_id}")
    print(f"  Local:        {local_url}")
    if headless:
        print(f"  SageMaker Notebook Instance: https://<instance>.notebook.<region>.sagemaker.aws/proxy/{port}/")
        print(f"  SageMaker Studio (JLab 3+):  https://<domain>.studio.<region>.sagemaker.aws/jupyter/default/proxy/{port}/")
        print("  (Trailing slash matters — without it, the /save call from the page will 404.)")
    print("Press Ctrl+C to stop.")
    if open_browser and not headless:
        threading.Timer(0.4, lambda: webbrowser.open(local_url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        server.server_close()


def _cli() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Serve a generated banking report with admin save endpoint")
    parser.add_argument("--customer", type=int, required=True)
    parser.add_argument("--html", type=str, required=False,
                        help="Path to the generated HTML (defaults to reports/customer_<id>_report.html)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    html_path: Optional[str] = args.html
    if not html_path:
        html_path = os.path.join("reports", f"customer_{args.customer}_report.html")
    serve(args.customer, html_path, port=args.port, open_browser=not args.no_browser)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    _cli()
