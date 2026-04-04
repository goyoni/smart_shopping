#!/usr/bin/env python3
"""Claude Code Session Viewer - reads Claude Code's built-in JSONL logs and serves a web UI."""

import sys
from http.server import HTTPServer

import os
from pathlib import Path

# Allow direct script execution (python3 tools/session-viewer/server.py)
_pkg_dir = str(Path(__file__).resolve().parent)
if _pkg_dir not in sys.path:
    sys.path.insert(0, _pkg_dir)

from data import PROJECTS_DIR, find_project_dir, get_all_projects  # noqa: E402
from handler import SessionViewerHandler  # noqa: E402


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Claude Code Session Viewer")
    parser.add_argument("--port", type=int, default=5111, help="Port to serve on")
    parser.add_argument("--project", type=str, help="Project path (auto-detected if not specified)")
    args = parser.parse_args()

    try:
        project_dir = find_project_dir(args.project)
        SessionViewerHandler.project_dir = project_dir
        print(f"Claude Code Session Viewer")
        print(f"Project: {project_dir}")
        print(f"Sessions: {len(list(project_dir.glob('*.jsonl')))}")
    except FileNotFoundError:
        # Still start - user can pick project in the UI
        projects = get_all_projects()
        if projects:
            first = PROJECTS_DIR / projects[0]["dir_name"]
            SessionViewerHandler.project_dir = first
            print(f"Claude Code Session Viewer")
            print(f"Auto-selected project: {first}")
        else:
            print("No projects found!")
            sys.exit(1)
    print(f"Serving at: http://localhost:{args.port}")

    server = HTTPServer(("localhost", args.port), SessionViewerHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
