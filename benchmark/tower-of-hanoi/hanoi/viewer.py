#!/usr/bin/env python3
"""Read-only local Hanoi log viewer. Experiments run through the terminal launcher."""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, unquote, urlparse
import webbrowser

from . import reporting

ROOT = Path(__file__).resolve().parents[1]
RUN_NAME = re.compile(r"[\w][\w.-]{0,79}\Z")


class RunStore:
    """Read records without loading credentials or starting/modifying experiments."""

    def __init__(self, runs_dir):
        self.runs_dir = Path(runs_dir).resolve()

    def path(self, name):
        if not isinstance(name, str) or not RUN_NAME.fullmatch(name) or name in {".", ".."}:
            raise ValueError("Invalid run name.")
        path = (self.runs_dir / name).resolve()
        if path.parent != self.runs_dir:
            raise ValueError("The run must be inside the runs directory.")
        return path

    def get_run(self, name):
        path = self.path(name)
        if not (path / "protocol.json").is_file():
            raise FileNotFoundError("Run log not found.")
        return reporting.snapshot(path)

    def list_runs(self):
        if not self.runs_dir.is_dir():
            return []
        rows = []
        for directory in self.runs_dir.iterdir():
            if not directory.is_dir() or not RUN_NAME.fullmatch(directory.name):
                continue
            if not (directory / "protocol.json").is_file():
                continue
            try:
                data = self.get_run(directory.name)
                frozen = (data.get("protocol") or {}).get("frozen", {})
                if not reporting._candidate_protocol(frozen):
                    continue
                rows.append(
                    {key: data.get(key) for key in ("name", "config", "state", "summary")}
                    | {"created_at": (data.get("protocol") or {}).get("created_at", "")}
                )
            except (ValueError, OSError):
                continue
        return sorted(rows, key=lambda row: row["created_at"], reverse=True)


class LogServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, manager):
        self.manager = manager
        super().__init__(address, LogHandler)


class LogHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def send_data(
        self, body, content_type="application/json; charset=utf-8", status=200, filename=None
    ):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        payload = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'self'",
        )
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def validate_host(self):
        port = self.server.server_address[1]
        if self.headers.get("Host") not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
            raise PermissionError("Use the local viewer address.")

    def do_GET(self):
        try:
            self.validate_host()
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            if path in {"/", "/app.js", "/style.css"}:
                name = "index.html" if path == "/" else path[1:]
                mime = {
                    "index.html": "text/html",
                    "app.js": "text/javascript",
                    "style.css": "text/css",
                }[name]
                return self.send_data((ROOT / "web" / name).read_bytes(), mime + "; charset=utf-8")
            if path == "/api/runs":
                return self.send_data({"runs": self.server.manager.list_runs()})
            parts = path.strip("/").split("/")
            if len(parts) >= 3 and parts[:2] == ["api", "runs"]:
                name = parts[2]
                data = self.server.manager.get_run(name)
                if len(parts) == 3:
                    return self.send_data(data)
                if len(parts) == 5 and parts[3] == "trials" and parts[4].isdigit():
                    return self.send_data(
                        reporting.trial_detail(self.server.manager.path(name), int(parts[4]))
                    )
                if len(parts) == 4 and parts[3] == "report":
                    return self.send_data(reporting.html_text(data), "text/html; charset=utf-8")
                if len(parts) == 4 and parts[3] == "download":
                    kind = parse_qs(parsed.query).get("format", ["json"])[0]
                    if kind == "json":
                        return self.send_data(data, filename="hanoi-log.json")
                    if kind == "csv":
                        return self.send_data(
                            reporting.csv_text(data),
                            "text/csv; charset=utf-8",
                            filename="hanoi-log.csv",
                        )
                    if kind == "html":
                        return self.send_data(
                            reporting.html_text(data),
                            "text/html; charset=utf-8",
                            filename="hanoi-log.html",
                        )
                    raise ValueError("Unsupported export format.")
            raise FileNotFoundError("Page or log not found.")
        except PermissionError as exc:
            self.send_data({"error": str(exc)}, status=403)
        except FileNotFoundError as exc:
            self.send_data({"error": str(exc)}, status=404)
        except ValueError as exc:
            self.send_data({"error": str(exc)}, status=400)
        except Exception:
            self.send_data(
                {"error": "Could not read the log. Check the original files."}, status=500
            )

    def do_POST(self):
        self.send_data({"error": "Start or resume experiments from the terminal."}, status=405)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--runs-dir", type=Path, default=ROOT / "runs")
    args = parser.parse_args(argv)
    server = LogServer(("127.0.0.1", args.port), RunStore(args.runs_dir))
    url = f"http://127.0.0.1:{server.server_address[1]}"
    print(f"Hanoi log viewer: {url}\nRead-only experiment logs.", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nLog viewer stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
