"""A loopback-only browser interface for Harness²; no web framework required."""

from __future__ import annotations

import argparse
import hmac
import http.client
import json
import mimetypes
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlsplit

from .. import config
from ..locking import file_lock
from .jobs import BusyError, JobManager
from .workspace import Workspace, validate_url


class App:
    def __init__(self, root: Path = config.ROOT):
        self.workspace = Workspace(root)
        self.jobs = JobManager(self.workspace)
        self.token = secrets.token_urlsafe(32)

    def state(self):
        with self.jobs.lock:
            jobs = self.jobs.list()
            active_id = self.jobs.active_id
            if active_id:
                active = self.jobs.get(active_id)
                jobs = [active if row["id"] == active["id"] else row for row in jobs]
        return {
            "workspace": str(self.workspace.root),
            "runs_root": str(self.workspace.runs_root),
            "python": sys.version.split()[0],
            "settings": self.workspace.settings(),
            "tools": self.workspace.tools(),
            "benchmarks": self.workspace.benchmarks(),
            "jobs": jobs,
            "active_id": active_id,
        }

    def connection(self, data: dict):
        url = validate_url(str(data.get("url") or self.workspace.settings()["local_url"]))
        # A connection test reads model metadata, without generating a completion.
        # It is limited to the user's chosen OpenAI-compatible endpoint.
        provider = data.get("provider", "local")
        if provider not in ("local", "custom"):
            raise ValueError("Choose a local or Codex endpoint connection test.")
        key_name = "HARNESS2_CODEX_API_KEY" if provider == "custom" else "HARNESS2_LOCAL_API_KEY"
        api_key = data.get("api_key") or self.workspace.environment().get(key_name)
        if api_key is not None and (
            not isinstance(api_key, str) or any(c in api_key for c in "\r\n")
        ):
            raise ValueError("Invalid API key.")
        headers = {"Accept": "application/json"}
        if api_key:
            headers["Authorization"] = "Bearer " + api_key
        request = urllib.request.Request(url + "/models", headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                result = json.loads(response.read(1024 * 1024))
            if not isinstance(result, dict) or not isinstance(result.get("data"), list):
                raise ValueError("The server did not return an OpenAI-compatible model list")
            models = [
                str(item["id"])
                for item in result.get("data", [])
                if isinstance(item, dict) and "id" in item
            ]
            selected = data.get("model")
            if selected and selected not in models:
                return {
                    "ok": False,
                    "models": models,
                    "error": f"Connected, but the server did not list model {selected!r}. Choose one of its listed model IDs.",
                }
            return {"ok": True, "models": models, "error": ""}
        except (OSError, ValueError, urllib.error.URLError) as exc:
            return {
                "ok": False,
                "models": [],
                "error": f"Could not read the model list: {exc}. Check that your model server is running and the URL includes /v1.",
            }

    def open_folder(self, data: dict):
        identifier = data.get("job_id")
        if identifier:
            record = self.jobs.get(identifier)
            path = Path(record.get("output_dir") or self.workspace.root)
        else:
            path = self.workspace.root
        if not path.is_dir():
            raise ValueError("That output folder does not exist yet.")
        if os.name == "nt":
            os.startfile(str(path.resolve()))
        else:
            command = "open" if sys.platform == "darwin" else "xdg-open"
            binary = shutil.which(command)
            if not binary:
                raise ValueError(f"Open this folder in your file manager: {path}")
            subprocess.Popen(
                [binary, str(path.resolve())], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        return {"ok": True}

    def close(self):
        self.jobs.close()


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def __init__(self, address, app: App):
        self.app = app
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "Harness2Local"

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, format, *args):
        # Access logs would add noise and can disclose artifact names or tokens.
        pass

    def _headers(self, code: int, content_type: str, size: int):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        )

    def _json(self, value, code: int = 200):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self._headers(code, "application/json; charset=utf-8", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _guard(self, *, api: bool):
        port = self.server.server_port
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host") not in allowed:
            self._json({"error": "This interface accepts only its local address."}, 403)
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in {"http://" + host for host in allowed}:
            self._json({"error": "Requests from another website are not accepted."}, 403)
            return False
        if api:
            supplied = self.headers.get("Authorization", "")
            expected = "Bearer " + self.server.app.token
            if not hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")):
                self._json(
                    {
                        "error": "Reopen this window with harness2-ui to reconnect your local session."
                    },
                    401,
                )
                return False
        return True

    def do_GET(self):
        path = urlsplit(self.path).path
        if not self._guard(api=path.startswith("/api/")):
            return
        try:
            if path == "/api/ping":
                self._json({"ok": True, "workspace": str(self.server.app.workspace.root)})
            elif path == "/api/state":
                self._json(self.server.app.state())
            elif path.startswith("/api/jobs/") and len(path.strip("/").split("/")) == 3:
                self._json(self.server.app.jobs.get(path.rsplit("/", 1)[1]))
            elif path.startswith("/api/artifacts/"):
                parts = path.strip("/").split("/")
                if len(parts) != 4:
                    raise KeyError(path)
                artifact = self.server.app.jobs.download(parts[2], parts[3])
                with artifact.open("rb") as stream:
                    self._headers(200, "application/octet-stream", artifact.stat().st_size)
                    self.send_header(
                        "Content-Disposition",
                        "attachment; filename*=UTF-8''" + quote(artifact.name, safe=""),
                    )
                    self.end_headers()
                    shutil.copyfileobj(stream, self.wfile)
            elif path in ("/", "/index.html", "/app.js", "/app.css"):
                asset = Path(__file__).with_name("assets") / (
                    "index.html" if path == "/" else path[1:]
                )
                body = asset.read_bytes()
                content_type = mimetypes.guess_type(asset.name)[0] or "application/octet-stream"
                self._headers(200, content_type + "; charset=utf-8", len(body))
                self.end_headers()
                self.wfile.write(body)
            else:
                self._json({"error": "Not found."}, 404)
        except KeyError:
            self._json({"error": "That run or file was not found."}, 404)
        except (OSError, ValueError) as exc:
            self._json({"error": str(exc)}, 400)

    def do_POST(self):
        if not self._guard(api=True):
            return
        try:
            if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
                self._json({"error": "Expected a JSON request."}, 415)
                return
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 65536:
                raise ValueError("Request body is missing or too large.")
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError("Expected a JSON object.")
            path = urlsplit(self.path).path
            app = self.server.app
            if path == "/api/jobs":
                self._json(
                    app.jobs.start(
                        data.get("kind"),
                        data.get("params", {}),
                        confirmed=data.get("confirm") is True,
                    ),
                    201,
                )
            elif path.startswith("/api/jobs/"):
                parts = path.strip("/").split("/")
                if len(parts) != 4:
                    raise KeyError(path)
                identifier, action = parts[2:]
                if action == "cancel":
                    self._json(app.jobs.cancel(identifier))
                elif action == "resume":
                    previous = app.jobs.get(identifier)
                    self._json(
                        app.jobs.start(
                            previous["kind"],
                            previous["params"],
                            confirmed=data.get("confirm") is True,
                            resume_id=identifier,
                        ),
                        201,
                    )
                else:
                    raise KeyError(path)
            elif path == "/api/settings":
                with app.jobs.lock:
                    if app.jobs.active_id:
                        raise BusyError(
                            "Let the current operation finish before changing its model connection."
                        )
                    saved = app.workspace.save(data)
                self._json(saved)
            elif path == "/api/connection":
                self._json(app.connection(data))
            elif path == "/api/open-folder":
                self._json(app.open_folder(data))
            elif path == "/api/shutdown":
                if app.jobs.active_id and data.get("confirm") is not True:
                    raise BusyError("An operation is running. Stop it before closing the app.")
                self._json({"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                self._json({"error": "Not found."}, 404)
        except BusyError as exc:
            self._json({"error": str(exc)}, 409)
        except KeyError:
            self._json({"error": "That operation was not found."}, 404)
        except (OSError, ValueError, TypeError) as exc:
            self._json({"error": str(exc)}, 400)


def existing_session(root: Path) -> str | None:
    """Reconnect a second launch without reclassifying live jobs as interrupted."""
    from .jobs import read_json

    saved = read_json(root / ".harness2-ui/server.json", {})
    if (
        not isinstance(saved, dict)
        or type(saved.get("port")) is not int
        or not isinstance(saved.get("token"), str)
    ):
        return None
    if not 1 <= saved["port"] <= 65535 or not 20 <= len(saved["token"]) <= 100:
        return None
    connection = http.client.HTTPConnection("127.0.0.1", saved["port"], timeout=2)
    try:
        connection.request(
            "GET", "/api/ping", headers={"Authorization": "Bearer " + saved["token"]}
        )
        response = connection.getresponse()
        data = json.loads(response.read(8192))
        if (
            response.status == 200
            and isinstance(data, dict)
            and data.get("ok")
            and data.get("workspace") == str(root.resolve())
        ):
            return f"http://127.0.0.1:{saved['port']}/#token={saved['token']}"
    except (OSError, ValueError, http.client.HTTPException):
        pass
    finally:
        connection.close()
    return None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--port", type=int, default=0, help="local port; default chooses a free port"
    )
    parser.add_argument(
        "--no-browser", action="store_true", help="print the local URL without opening a browser"
    )
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    state = config.ROOT / ".harness2-ui"
    state.mkdir(parents=True, exist_ok=True)
    try:
        with file_lock(state / "server.lock", blocking=False):
            return serve(args)
    except BlockingIOError:
        # Another launch may be between obtaining the lease and writing its URL.
        for _ in range(30):
            existing = existing_session(config.ROOT)
            if existing:
                print(f"Harness² is already running: {existing}", flush=True)
                if not args.no_browser:
                    webbrowser.open(existing)
                return 0
            time.sleep(0.1)
        print("Harness² is starting or closing. Please open it again in a moment.", file=sys.stderr)
        return 1


def serve(args) -> int:
    existing = existing_session(config.ROOT)
    if existing:
        print(f"Harness² is already running: {existing}", flush=True)
        if not args.no_browser:
            webbrowser.open(existing)
        return 0
    app = App(config.ROOT)
    server = LocalServer(("127.0.0.1", args.port), app)
    url = f"http://127.0.0.1:{server.server_port}/#token={app.token}"
    session = app.workspace.state_dir / "server.json"
    session.write_text(
        json.dumps({"port": server.server_port, "token": app.token, "pid": os.getpid()}),
        encoding="utf-8",
    )
    print(f"Harness² is ready: {url}", flush=True)
    print("Your workspace stays on this computer. Press Ctrl+C to close the app.", flush=True)
    if not args.no_browser:
        threading.Timer(0.3, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        app.close()
        server.server_close()
        session.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
