"""Tiny tailnet-only server for SWS screener reports (stdlib only).

  /         the multi-list hub (index.html: a tab per list + "All lists") when it
            exists, otherwise the file listing
  /files    listing of every report in --dir, newest first, with a "latest" link
  /latest   302 redirect to the newest single-list dashboard (.html)
  /<file>   the file itself (html opens in the browser, xlsx downloads)

Bind it to the Tailscale IP, not 0.0.0.0, so reports never reach the public
internet. Run under systemd (see infra/swst-reports.service).
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import os
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote

SHOWN = (".html", ".xlsx")


def _reports(root: str) -> list[os.DirEntry]:
    with os.scandir(root) as it:
        files = [e for e in it if e.is_file() and e.name.endswith(SHOWN)
                 and e.name != "index.html"]
    return sorted(files, key=lambda e: e.stat().st_mtime, reverse=True)


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/latest":
            return self._latest()
        if path in ("/", "/index.html"):
            hub = os.path.join(self.directory, "index.html")
            if os.path.isfile(hub):
                return self._hub(hub)
            return self._index()
        if path == "/files":
            return self._index()
        return super().do_GET()

    def _latest(self):
        dashboards = [e for e in _reports(self.directory) if e.name.endswith(".html")]
        if not dashboards:
            return self._send(404, "<p>No dashboard yet. Run the pipeline first.</p>")
        self.send_response(302)
        self.send_header("Location", "/" + quote(dashboards[0].name))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _hub(self, hub_path: str):
        with open(hub_path, "rb") as fh:
            page = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(page)

    def _index(self):
        rows = []
        for e in _reports(self.directory):
            ts = dt.datetime.fromtimestamp(e.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            kb = e.stat().st_size / 1024
            rows.append(
                f'<tr><td><a href="/{quote(e.name)}">{html.escape(e.name)}</a></td>'
                f"<td>{ts}</td><td>{kb:,.0f} KB</td></tr>")
        body = (
            "<h1>SWS screener reports</h1>"
            '<p><a href="/"><b>Hub (all lists)</b></a> | '
            '<a href="/latest"><b>Open latest dashboard</b></a></p>'
            "<table><tr><th>File</th><th>Modified</th><th>Size</th></tr>"
            + ("".join(rows) or '<tr><td colspan="3">No reports yet.</td></tr>')
            + "</table><p>Screening aid, not investment advice.</p>")
        self._send(200, body)

    def _send(self, code: int, body: str):
        page = ("<!doctype html><meta charset='utf-8'><title>SWS reports</title>"
                "<style>body{font:14px Arial,sans-serif;margin:24px;max-width:900px}"
                "td,th{padding:4px 12px;text-align:left}</style>" + body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(page)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=os.path.expanduser("~/swst_reports"))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=3090)
    a = ap.parse_args()
    os.makedirs(a.dir, exist_ok=True)
    srv = ThreadingHTTPServer((a.host, a.port), partial(Handler, directory=a.dir))
    print(f"serving {a.dir} on http://{a.host}:{a.port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
