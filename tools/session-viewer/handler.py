"""HTTP request handler for Claude Code Session Viewer."""

import json
from http.server import SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import sys
from pathlib import Path

# Allow direct script execution (python3 tools/session-viewer/server.py)
_pkg_dir = str(Path(__file__).resolve().parent)
if _pkg_dir not in sys.path:
    sys.path.insert(0, _pkg_dir)

from data import (  # noqa: E402
    HISTORY_FILE,
    PROJECTS_DIR,
    get_all_projects,
    get_sessions_list,
    load_hidden_sessions,
    parse_session,
    save_hidden_sessions,
)
from templates import get_html  # noqa: E402


class SessionViewerHandler(SimpleHTTPRequestHandler):
    project_dir = None

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == "/" or path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(get_html().encode())

        elif path == "/api/projects":
            self.json_response(get_all_projects())

        elif path == "/api/sessions":
            project = params.get("project", [None])[0]
            if project:
                proj_dir = PROJECTS_DIR / project
            else:
                proj_dir = self.project_dir
            self.json_response(get_sessions_list(proj_dir))

        elif path == "/api/session":
            session_id = params.get("id", [None])[0]
            project = params.get("project", [None])[0]
            if project:
                proj_dir = PROJECTS_DIR / project
            else:
                proj_dir = self.project_dir

            if session_id:
                filepath = proj_dir / f"{session_id}.jsonl"
                if filepath.exists():
                    self.json_response(parse_session(filepath))
                else:
                    self.send_error(404, f"Session {session_id} not found")
            else:
                self.send_error(400, "Missing session id")

        elif path == "/api/history":
            # Global prompt history
            entries = []
            if HISTORY_FILE.exists():
                with open(HISTORY_FILE) as f:
                    for line in f:
                        try:
                            entry = json.loads(line)
                            if entry.get("display"):
                                entries.append({
                                    "text": entry["display"],
                                    "timestamp": entry.get("timestamp"),
                                    "project": entry.get("project", ""),
                                    "session_id": entry.get("sessionId", ""),
                                })
                        except json.JSONDecodeError:
                            continue
            self.json_response(entries)

        else:
            self.send_error(404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == "/api/hide-session":
            content_length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_length)) if content_length else {}
            session_id = body.get("session_id")
            hide = body.get("hide", True)
            project = body.get("project") or params.get("project", [None])[0]

            proj_dir = PROJECTS_DIR / project if project else self.project_dir
            hidden = load_hidden_sessions(proj_dir)
            if hide:
                hidden.add(session_id)
            else:
                hidden.discard(session_id)
            save_hidden_sessions(proj_dir, hidden)
            self.json_response({"ok": True, "hidden": sorted(hidden)})
        else:
            self.send_error(404)

    def json_response(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str).encode())

    def log_message(self, format, *args):
        pass  # Suppress request logs
