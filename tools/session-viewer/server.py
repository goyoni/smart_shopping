#!/usr/bin/env python3
"""Claude Code Session Viewer - reads Claude Code's built-in JSONL logs and serves a web UI."""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# Auto-detect project data directory
CLAUDE_DIR = Path.home() / ".claude"
PROJECTS_DIR = CLAUDE_DIR / "projects"
HISTORY_FILE = CLAUDE_DIR / "history.jsonl"

# Default to current project; override with --project flag
DEFAULT_PROJECT = None


def find_project_dir(project_path: str | None = None) -> Path:
    """Find the Claude Code project data directory for a given project path."""
    if project_path:
        # Convert path to Claude's directory naming convention
        dir_name = project_path.replace("/", "-").lstrip("-")
        candidate = PROJECTS_DIR / f"-{dir_name}"
        if not candidate.exists():
            candidate = PROJECTS_DIR / dir_name
        if candidate.exists():
            return candidate

    # Try to detect from cwd
    cwd = os.getcwd()
    dir_name = "-" + cwd.replace("/", "-").lstrip("-")
    candidate = PROJECTS_DIR / dir_name
    if candidate.exists():
        return candidate

    # Try without leading dash
    candidate = PROJECTS_DIR / dir_name.lstrip("-")
    if candidate.exists():
        return candidate

    # List available projects and pick first match
    if PROJECTS_DIR.exists():
        projects = [d for d in PROJECTS_DIR.iterdir() if d.is_dir() and not d.name.startswith(".") and d.name != "memory"]
        # Try substring match on cwd basename
        basename = Path(cwd).name
        for p in projects:
            if basename in p.name:
                return p
        if len(projects) == 1:
            return projects[0]

    raise FileNotFoundError(f"Cannot find Claude project directory. Available: {[d.name for d in PROJECTS_DIR.iterdir() if d.is_dir()]}")


def parse_session(filepath: Path) -> dict:
    """Parse a session JSONL file into structured data."""
    session_id = filepath.stem
    messages = []
    total_input_tokens = 0
    total_output_tokens = 0
    total_cache_creation = 0
    total_cache_read = 0
    first_timestamp = None
    last_timestamp = None
    git_branch = None
    version = None

    with open(filepath) as f:
        for line in f:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            entry_type = entry.get("type")
            timestamp = entry.get("timestamp")

            if timestamp:
                if isinstance(timestamp, str):
                    ts = timestamp
                elif isinstance(timestamp, (int, float)):
                    ts = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc).isoformat()
                else:
                    ts = None

                if ts:
                    if first_timestamp is None:
                        first_timestamp = ts
                    last_timestamp = ts

            if not git_branch and entry.get("gitBranch"):
                git_branch = entry["gitBranch"]
            if not version and entry.get("version"):
                version = entry["version"]

            if entry_type == "user":
                msg_content = entry.get("message", {}).get("content", "")
                is_meta = entry.get("isMeta", False)

                # Extract readable text from content
                if isinstance(msg_content, list):
                    text_parts = []
                    for part in msg_content:
                        if isinstance(part, dict):
                            if part.get("type") == "text":
                                text_parts.append(part["text"])
                            elif part.get("type") == "tool_result":
                                text_parts.append(f"[tool_result: {str(part.get('content', ''))[:100]}]")
                        elif isinstance(part, str):
                            text_parts.append(part)
                    display_text = "\n".join(text_parts)
                else:
                    display_text = str(msg_content)

                # Skip tool results (they show as user messages)
                if isinstance(msg_content, list) and all(
                    isinstance(p, dict) and p.get("type") == "tool_result" for p in msg_content
                ):
                    continue
                if isinstance(msg_content, dict) and msg_content.get("type") == "tool_result":
                    continue

                # Skip meta messages (system tags, expanded skill prompts)
                if is_meta:
                    continue

                # Clean up command messages
                if "<command-name>" in display_text:
                    import re
                    cmd_match = re.search(r"<command-name>(.*?)</command-name>", display_text)
                    msg_match = re.search(r"<command-message>(.*?)</command-message>", display_text)
                    if cmd_match:
                        cmd = cmd_match.group(1)
                        msg = msg_match.group(1) if msg_match else ""
                        # Skip /clear commands entirely
                        if cmd.strip().lstrip("/") == "clear" or msg.strip() == "clear":
                            continue
                        display_text = f"/{cmd}" + (f" {msg}" if msg else "")

                if display_text.strip():
                    messages.append({
                        "role": "user",
                        "text": display_text.strip(),
                        "timestamp": ts if ts else None,
                        "is_meta": is_meta,
                    })

            elif entry_type == "assistant":
                msg = entry.get("message", {})
                content = msg.get("content", [])
                usage = msg.get("usage", {})

                input_tok = usage.get("input_tokens", 0) + usage.get("cache_creation_input_tokens", 0) + usage.get("cache_read_input_tokens", 0)
                output_tok = usage.get("output_tokens", 0)
                cache_create = usage.get("cache_creation_input_tokens", 0)
                cache_read = usage.get("cache_read_input_tokens", 0)

                total_input_tokens += input_tok
                total_output_tokens += output_tok
                total_cache_creation += cache_create
                total_cache_read += cache_read

                # Extract text and tool uses
                text_parts = []
                tool_uses = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text" and block.get("text", "").strip():
                        text_parts.append(block["text"].strip())
                    elif block.get("type") == "tool_use":
                        tool_info = {
                            "name": block.get("name", "unknown"),
                        }
                        inp = block.get("input", {})
                        # Extract key info from tool inputs without full content
                        if block["name"] in ("Read", "Glob", "Grep"):
                            tool_info["target"] = inp.get("file_path") or inp.get("pattern") or inp.get("query", "")
                        elif block["name"] == "Write":
                            tool_info["target"] = inp.get("file_path", "")
                        elif block["name"] == "Edit":
                            tool_info["target"] = inp.get("file_path", "")
                        elif block["name"] == "Bash":
                            tool_info["target"] = inp.get("command", "")[:150]
                        elif block["name"] == "Agent":
                            tool_info["target"] = inp.get("description", "")
                        elif block["name"] == "Skill":
                            tool_info["target"] = inp.get("skillName", "")
                        else:
                            tool_info["target"] = str(inp)[:100] if inp else ""
                        tool_uses.append(tool_info)

                combined_text = "\n".join(text_parts)

                # Skip empty streaming chunks (only add if there's content)
                if combined_text or tool_uses:
                    messages.append({
                        "role": "assistant",
                        "text": combined_text,
                        "tools": tool_uses,
                        "timestamp": ts if ts else None,
                        "tokens": {
                            "input": input_tok,
                            "output": output_tok,
                            "cache_creation": cache_create,
                            "cache_read": cache_read,
                        },
                        "model": msg.get("model", ""),
                        "_request_id": entry.get("requestId"),
                    })

    # Deduplicate consecutive assistant messages (streaming chunks)
    # Claude Code logs multiple assistant entries per API call (streaming).
    # Entries sharing the same requestId are the same response - keep the richest one.
    deduped = []
    seen_request_ids = set()

    for msg in messages:
        if msg["role"] == "assistant":
            req_id = msg.get("_request_id")
            if req_id and req_id in seen_request_ids:
                # Find the existing entry and merge
                for prev in reversed(deduped):
                    if prev.get("_request_id") == req_id:
                        if len(msg.get("text", "")) > len(prev.get("text", "")):
                            prev["text"] = msg["text"]
                        if msg.get("tools"):
                            existing_tools = {(t["name"], t.get("target", "")) for t in prev.get("tools", [])}
                            for t in msg["tools"]:
                                if (t["name"], t.get("target", "")) not in existing_tools:
                                    prev.setdefault("tools", []).append(t)
                        # Keep max tokens (not sum - they're cumulative in streaming)
                        if msg.get("tokens"):
                            for k in ("input", "output", "cache_creation", "cache_read"):
                                prev["tokens"][k] = max(prev.get("tokens", {}).get(k, 0), msg["tokens"].get(k, 0))
                        break
                continue

            if req_id:
                seen_request_ids.add(req_id)
            deduped.append(msg)
        else:
            deduped.append(msg)

    # Recalculate totals from deduped messages
    total_input_tokens = sum(m.get("tokens", {}).get("input", 0) for m in deduped if m["role"] == "assistant")
    total_output_tokens = sum(m.get("tokens", {}).get("output", 0) for m in deduped if m["role"] == "assistant")
    total_cache_creation = sum(m.get("tokens", {}).get("cache_creation", 0) for m in deduped if m["role"] == "assistant")
    total_cache_read = sum(m.get("tokens", {}).get("cache_read", 0) for m in deduped if m["role"] == "assistant")

    # Group into conversation turns (user prompt -> assistant response)
    turns = []
    current_turn = None
    for msg in deduped:
        if msg["role"] == "user":
            if current_turn:
                turns.append(current_turn)
            current_turn = {"user": msg, "assistant_messages": []}
        elif msg["role"] == "assistant" and current_turn:
            current_turn["assistant_messages"].append(msg)

    if current_turn:
        turns.append(current_turn)

    # Strip internal fields from output
    for msg in deduped:
        msg.pop("_request_id", None)

    return {
        "session_id": session_id,
        "first_timestamp": first_timestamp,
        "last_timestamp": last_timestamp,
        "git_branch": git_branch,
        "version": version,
        "turns": turns,
        "total_tokens": {
            "input": total_input_tokens,
            "output": total_output_tokens,
            "cache_creation": total_cache_creation,
            "cache_read": total_cache_read,
            "total": total_input_tokens + total_output_tokens,
        },
        "message_count": len(deduped),
        "turn_count": len(turns),
    }


def get_sessions_list(project_dir: Path) -> list[dict]:
    """Get a summary list of all sessions."""
    sessions = []
    for f in sorted(project_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
        session = parse_session(f)
        # Get first user message as preview
        preview = ""
        for turn in session["turns"]:
            user_text = turn["user"]["text"]
            if user_text and not user_text.startswith("/clear"):
                preview = user_text[:150]
                break

        sessions.append({
            "session_id": session["session_id"],
            "first_timestamp": session["first_timestamp"],
            "last_timestamp": session["last_timestamp"],
            "git_branch": session["git_branch"],
            "version": session["version"],
            "turn_count": session["turn_count"],
            "message_count": session["message_count"],
            "total_tokens": session["total_tokens"],
            "preview": preview,
            "file_size_kb": round(f.stat().st_size / 1024, 1),
        })

    return sessions


def get_all_projects() -> list[dict]:
    """List all available projects."""
    projects = []
    if PROJECTS_DIR.exists():
        for d in sorted(PROJECTS_DIR.iterdir()):
            if d.is_dir() and not d.name.startswith(".") and d.name != "memory":
                jsonl_count = len(list(d.glob("*.jsonl")))
                if jsonl_count > 0:
                    projects.append({
                        "dir_name": d.name,
                        "session_count": jsonl_count,
                        "path": str(d),
                    })
    return projects


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

    def json_response(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str).encode())

    def log_message(self, format, *args):
        pass  # Suppress request logs


def get_html() -> str:
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Claude Code Session Viewer</title>
<style>
:root {
  --bg: #0d1117;
  --bg-secondary: #161b22;
  --bg-tertiary: #21262d;
  --border: #30363d;
  --text: #e6edf3;
  --text-secondary: #8b949e;
  --accent: #58a6ff;
  --accent-subtle: #1f6feb33;
  --green: #3fb950;
  --orange: #d29922;
  --purple: #bc8cff;
  --red: #f85149;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
  background: var(--bg);
  color: var(--text);
  line-height: 1.5;
}
.app { display: flex; height: 100vh; }
.sidebar {
  width: 380px;
  min-width: 380px;
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  background: var(--bg-secondary);
}
.sidebar-header {
  padding: 16px;
  border-bottom: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.sidebar-header h1 { font-size: 16px; font-weight: 600; }
.sidebar-header .stats { font-size: 12px; color: var(--text-secondary); }
.project-selector {
  padding: 4px 8px;
  background: var(--bg-tertiary);
  border: 1px solid var(--border);
  border-radius: 6px;
  color: var(--text);
  font-size: 13px;
  width: 100%;
}
.session-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
}
.session-item {
  padding: 12px;
  border-radius: 8px;
  cursor: pointer;
  margin-bottom: 4px;
  border: 1px solid transparent;
  transition: all 0.15s;
}
.session-item:hover { background: var(--bg-tertiary); }
.session-item.active {
  background: var(--accent-subtle);
  border-color: var(--accent);
}
.session-item .timestamp {
  font-size: 11px;
  color: var(--text-secondary);
  margin-bottom: 4px;
}
.session-item .preview {
  font-size: 13px;
  color: var(--text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-bottom: 6px;
}
.session-item .meta {
  display: flex;
  gap: 12px;
  font-size: 11px;
  color: var(--text-secondary);
}
.session-item .meta .branch { color: var(--green); }
.session-item .meta .tokens { color: var(--orange); }

.main-content {
  flex: 1;
  overflow-y: auto;
  padding: 0;
  display: flex;
  flex-direction: column;
}
.session-header {
  padding: 16px 24px;
  border-bottom: 1px solid var(--border);
  background: var(--bg-secondary);
  display: flex;
  justify-content: space-between;
  align-items: center;
  position: sticky;
  top: 0;
  z-index: 10;
}
.session-header h2 { font-size: 14px; font-weight: 600; }
.session-header .token-summary {
  font-size: 12px;
  color: var(--text-secondary);
  display: flex;
  gap: 16px;
}
.token-badge {
  padding: 2px 8px;
  border-radius: 12px;
  font-size: 11px;
  font-weight: 500;
}
.token-badge.input { background: #1f6feb33; color: var(--accent); }
.token-badge.output { background: #3fb95033; color: var(--green); }
.token-badge.total { background: #d2992233; color: var(--orange); }

.conversation {
  padding: 24px;
  flex: 1;
}
.turn {
  margin-bottom: 8px;
  border: 1px solid var(--border);
  border-radius: 12px;
  overflow: hidden;
}
.turn-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  background: var(--bg-tertiary);
  cursor: pointer;
  user-select: none;
}
.turn-header:hover { background: #282e36; }
.turn-header .turn-arrow { font-size: 10px; color: var(--text-secondary); flex-shrink: 0; }
.turn-header .turn-preview {
  flex: 1;
  font-size: 13px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.turn-header .turn-meta {
  font-size: 11px;
  color: var(--text-secondary);
  white-space: nowrap;
  display: flex;
  gap: 10px;
}
.turn-body { }
.turn.collapsed-turn .turn-body { display: none; }
.turn.collapsed-turn { margin-bottom: 2px; }
.turn.collapsed-turn .turn-header { border-bottom: none; }
.collapse-all-btn {
  background: none;
  border: 1px solid var(--border);
  color: var(--text-secondary);
  padding: 4px 12px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 12px;
  white-space: nowrap;
}
.collapse-all-btn:hover { background: var(--bg-tertiary); color: var(--text); }
.turn-user {
  padding: 12px 16px;
  background: var(--bg-tertiary);
  border-bottom: 1px solid var(--border);
}
.turn-user .label {
  font-size: 11px;
  font-weight: 600;
  color: var(--accent);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 4px;
}
.turn-user .content {
  font-size: 14px;
  white-space: pre-wrap;
  word-break: break-word;
}
.turn-user .content.collapsed {
  max-height: 3em;
  overflow: hidden;
  position: relative;
}
.turn-user .content.collapsed::after {
  content: '';
  position: absolute;
  bottom: 0; left: 0; right: 0;
  height: 1.5em;
  background: linear-gradient(transparent, var(--bg-tertiary));
}
.turn-user .expand-btn { margin-top: 4px; }
.turn-assistant {
  padding: 16px;
  background: var(--bg);
}
.turn-assistant.continuation {
  padding-top: 0;
  padding-bottom: 8px;
}
.turn-assistant .label {
  font-size: 11px;
  font-weight: 600;
  color: var(--purple);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 6px;
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.turn-assistant .content {
  font-size: 14px;
  white-space: pre-wrap;
  word-break: break-word;
}
.turn-assistant .content.collapsed {
  max-height: 3em;
  overflow: hidden;
  position: relative;
}
.turn-assistant .content.collapsed::after {
  content: '';
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  height: 1.5em;
  background: linear-gradient(transparent, var(--bg));
}
.expand-btn {
  background: none;
  border: 1px solid var(--border);
  color: var(--text-secondary);
  padding: 2px 10px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 11px;
  margin-top: 4px;
}
.expand-btn:hover { background: var(--bg-tertiary); color: var(--text); }

.tool-toggle {
  background: none;
  border: 1px solid var(--border);
  color: var(--text-secondary);
  padding: 2px 10px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 11px;
  margin-top: 6px;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.tool-toggle:hover { background: var(--bg-tertiary); color: var(--text); }
.tool-toggle .arrow { font-size: 9px; }
.tool-list {
  margin-top: 4px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.tool-list.hidden { display: none; }
.tool-item {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 12px;
  padding: 3px 8px;
  background: var(--bg-secondary);
  border-radius: 4px;
  border: 1px solid var(--border);
}
.tool-name {
  font-weight: 600;
  color: var(--green);
  white-space: nowrap;
}
.tool-target {
  color: var(--text-secondary);
  font-family: 'SF Mono', 'Fira Code', monospace;
  font-size: 11px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.token-info {
  font-size: 11px;
  color: var(--text-secondary);
  display: flex;
  gap: 8px;
}

.empty-state {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: var(--text-secondary);
  font-size: 14px;
}
.turn-timestamp {
  font-size: 10px;
  color: var(--text-secondary);
  margin-left: 8px;
  font-weight: normal;
}

/* Search */
.search-box {
  padding: 4px 8px;
  background: var(--bg-tertiary);
  border: 1px solid var(--border);
  border-radius: 6px;
  color: var(--text);
  font-size: 13px;
  width: 100%;
}
.search-box::placeholder { color: var(--text-secondary); }

/* Scrollbar */
::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: var(--text-secondary); }
</style>
</head>
<body>
<div class="app">
  <div class="sidebar">
    <div class="sidebar-header">
      <h1>Claude Code Sessions</h1>
      <select id="projectSelector" class="project-selector"></select>
      <input type="text" id="searchBox" class="search-box" placeholder="Search sessions...">
      <div class="stats" id="globalStats"></div>
    </div>
    <div class="session-list" id="sessionList"></div>
  </div>
  <div class="main-content" id="mainContent">
    <div class="empty-state">Select a session to view the conversation</div>
  </div>
</div>

<script>
let allSessions = [];
let currentProject = null;

async function loadProjects() {
  const res = await fetch('/api/projects');
  const projects = await res.json();
  const selector = document.getElementById('projectSelector');
  selector.innerHTML = projects.map(p =>
    `<option value="${p.dir_name}">${p.dir_name.replace(/-/g, '/')} (${p.session_count} sessions)</option>`
  ).join('');

  // Auto-select current project from URL or first
  const urlParams = new URLSearchParams(window.location.search);
  const urlProject = urlParams.get('project');
  if (urlProject) {
    selector.value = urlProject;
  }
  currentProject = selector.value;
  selector.addEventListener('change', () => {
    currentProject = selector.value;
    loadSessions();
  });
  loadSessions();
}

async function loadSessions() {
  const res = await fetch(`/api/sessions?project=${currentProject}`);
  allSessions = await res.json();
  renderSessionList(allSessions);
  updateGlobalStats(allSessions);
}

function updateGlobalStats(sessions) {
  const totalTokens = sessions.reduce((sum, s) => sum + (s.total_tokens?.total || 0), 0);
  const totalSessions = sessions.length;
  const totalTurns = sessions.reduce((sum, s) => sum + s.turn_count, 0);
  document.getElementById('globalStats').textContent =
    `${totalSessions} sessions | ${totalTurns} turns | ${formatTokens(totalTokens)} total tokens`;
}

function formatTokens(n) {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K';
  return n.toString();
}

function formatDate(ts) {
  if (!ts) return '';
  const d = new Date(ts);
  return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' }) +
    ' ' + d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
}

function formatTime(ts) {
  if (!ts) return '';
  return new Date(ts).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function renderSessionList(sessions) {
  const list = document.getElementById('sessionList');
  list.innerHTML = sessions.map(s => `
    <div class="session-item" data-id="${s.session_id}" onclick="loadSession('${s.session_id}')">
      <div class="timestamp">${formatDate(s.first_timestamp)}</div>
      <div class="preview">${escapeHtml(s.preview || '(no preview)')}</div>
      <div class="meta">
        <span class="branch">${s.git_branch || ''}</span>
        <span>${s.turn_count} turns</span>
        <span class="tokens">${formatTokens(s.total_tokens?.total || 0)} tok</span>
        <span>${s.file_size_kb} KB</span>
      </div>
    </div>
  `).join('');
}

async function loadSession(sessionId) {
  // Highlight active
  document.querySelectorAll('.session-item').forEach(el => el.classList.remove('active'));
  document.querySelector(`[data-id="${sessionId}"]`)?.classList.add('active');

  const res = await fetch(`/api/session?id=${sessionId}&project=${currentProject}`);
  const session = await res.json();
  renderSession(session);
}

function renderSession(session) {
  const main = document.getElementById('mainContent');
  const tokens = session.total_tokens;

  let html = `
    <div class="session-header">
      <div>
        <h2>Session ${session.session_id.slice(0, 8)}
          <span class="turn-timestamp">${formatDate(session.first_timestamp)} - ${formatTime(session.last_timestamp)}</span>
        </h2>
        <div style="font-size:12px;color:var(--text-secondary);margin-top:4px;">
          Branch: <span style="color:var(--green)">${session.git_branch || 'unknown'}</span>
          | v${session.version || '?'}
          | ${session.turn_count} turns
        </div>
      </div>
      <div class="token-summary">
        <button class="collapse-all-btn" onclick="toggleAllTurns(this)">Collapse all</button>
        <span class="token-badge input">In: ${formatTokens(tokens.input)}</span>
        <span class="token-badge output">Out: ${formatTokens(tokens.output)}</span>
        <span class="token-badge total">Total: ${formatTokens(tokens.total)}</span>
      </div>
    </div>
    <div class="conversation">
  `;

  for (const turn of session.turns) {
    const turnId = 'turn' + Math.random().toString(36).slice(2, 8);
    const userText = turn.user.text;
    const userTextTrimmed = userText.replace(/^\\s+/, '').replace(/\\s+$/, '');
    const previewText = userTextTrimmed.split('\\n')[0].slice(0, 120);
    const totalTools = turn.assistant_messages.reduce((s, m) => s + (m.tools?.length || 0), 0);
    const totalOut = turn.assistant_messages.reduce((s, m) => s + (m.tokens?.output || 0), 0);
    const turnMeta = `${formatTime(turn.user.timestamp)}` +
      (totalTools > 0 ? ` | ${totalTools} tools` : '') +
      (totalOut > 0 ? ` | out:${formatTokens(totalOut)}` : '');

    html += `<div class="turn" id="${turnId}">`;

    // Collapsible one-liner header
    html += `<div class="turn-header" onclick="toggleTurn('${turnId}')">`;
    html += `<span class="turn-arrow">&#9660;</span>`;
    html += `<span class="turn-preview">${escapeHtml(previewText)}</span>`;
    html += `<span class="turn-meta">${turnMeta}</span>`;
    html += `</div>`;

    html += `<div class="turn-body">`;

    // User message
    const userLines = userTextTrimmed.split('\\n').filter(l => l.trim());
    const isLongUser = userLines.length > 2 || userTextTrimmed.length > 150;
    const userId = 'u' + Math.random().toString(36).slice(2, 8);

    html += `<div class="turn-user">`;
    html += `<div class="label">You <span class="turn-timestamp">${formatTime(turn.user.timestamp)}</span></div>`;
    html += `<div class="content ${isLongUser ? 'collapsed' : ''}" id="${userId}">${escapeHtml(userTextTrimmed)}</div>`;
    if (isLongUser) {
      html += `<button class="expand-btn" onclick="toggleExpand('${userId}', this)">Show more</button>`;
    }
    html += `</div>`;

    // Assistant messages — group consecutive tool-only messages
    const msgs = turn.assistant_messages;
    let i = 0;
    let shownLabel = false;

    while (i < msgs.length) {
      const msg = msgs[i];
      const trimmedText = (msg.text || '').replace(/^\\s+/, '').replace(/\\s+$/, '');
      const hasText = trimmedText.length > 0;
      const hasTools = msg.tools && msg.tools.length > 0;

      if (hasText) {
        // Render text message normally
        const textContent = escapeHtml(trimmedText);
        const textLines = trimmedText.split('\\n').filter(l => l.trim()).length;
        const needsCollapse = textLines > 2 || trimmedText.length > 150;
        const contentId = 'c' + Math.random().toString(36).slice(2, 8);

        html += `<div class="turn-assistant">`;
        if (!shownLabel) {
          html += `<div class="label"><span>Claude <span class="turn-timestamp">${formatTime(msg.timestamp)}</span></span></div>`;
          shownLabel = true;
        }
        html += `<div class="content ${needsCollapse ? 'collapsed' : ''}" id="${contentId}">${textContent}</div>`;
        if (needsCollapse) {
          html += `<button class="expand-btn" onclick="toggleExpand('${contentId}', this)">Show more</button>`;
        }
        // If this text message also has tools, show them inline
        if (hasTools) {
          html += renderToolToggle(msg.tools);
        }
        html += '</div>';
        i++;
      } else if (hasTools) {
        // Accumulate consecutive tool-only messages
        const groupTools = [];
        let groupOutTokens = 0;
        let groupInTokens = 0;
        const groupStart = i;
        while (i < msgs.length) {
          const m = msgs[i];
          const mt = (m.text || '').trim();
          if (mt.length > 0) break;
          if (!m.tools || m.tools.length === 0) { i++; continue; }
          for (const t of m.tools) groupTools.push(t);
          groupOutTokens += (m.tokens?.output || 0);
          groupInTokens += (m.tokens?.input || 0);
          i++;
        }
        if (groupTools.length > 0) {
          if (!shownLabel) {
            html += `<div class="turn-assistant"><div class="label"><span>Claude <span class="turn-timestamp">${formatTime(msgs[groupStart].timestamp)}</span></span></div></div>`;
            shownLabel = true;
          }
          const toolId = 'tg' + Math.random().toString(36).slice(2, 8);
          const toolNames = [...new Set(groupTools.map(t => t.name))];
          const tokSummary = groupOutTokens > 0 ? ` | out:${formatTokens(groupOutTokens)}` : '';
          html += `<div class="turn-assistant continuation">`;
          html += `<button class="tool-toggle" onclick="toggleTools('${toolId}', this)"><span class="arrow">&#9654;</span> ${groupTools.length} tool calls (${toolNames.join(', ')})${tokSummary}</button>`;
          html += `<div class="tool-list hidden" id="${toolId}">`;
          for (const tool of groupTools) {
            html += `<div class="tool-item"><span class="tool-name">${escapeHtml(tool.name)}</span><span class="tool-target">${escapeHtml(tool.target || '')}</span></div>`;
          }
          html += '</div></div>';
        }
      } else {
        i++;
      }
    }

    html += '</div>'; // turn-body
    html += '</div>'; // turn
  }

  function renderToolToggle(tools) {
    const toolId = 'ti' + Math.random().toString(36).slice(2, 8);
    const toolNames = [...new Set(tools.map(t => t.name))].join(', ');
    let h = `<button class="tool-toggle" onclick="toggleTools('${toolId}', this)"><span class="arrow">&#9654;</span> ${tools.length} tool calls (${toolNames})</button>`;
    h += `<div class="tool-list hidden" id="${toolId}">`;
    for (const tool of tools) {
      h += `<div class="tool-item"><span class="tool-name">${escapeHtml(tool.name)}</span><span class="tool-target">${escapeHtml(tool.target || '')}</span></div>`;
    }
    h += '</div>';
    return h;
  }

  html += '</div>';
  main.innerHTML = html;
}

function toggleExpand(id, btn) {
  const el = document.getElementById(id);
  el.classList.toggle('collapsed');
  btn.textContent = el.classList.contains('collapsed') ? 'Show more' : 'Show less';
}

function toggleTools(id, btn) {
  const el = document.getElementById(id);
  const hidden = el.classList.toggle('hidden');
  btn.querySelector('.arrow').innerHTML = hidden ? '&#9654;' : '&#9660;';
}

function toggleTurn(id) {
  const el = document.getElementById(id);
  const collapsed = el.classList.toggle('collapsed-turn');
  el.querySelector('.turn-arrow').innerHTML = collapsed ? '&#9654;' : '&#9660;';
}

function toggleAllTurns(btn) {
  const turns = document.querySelectorAll('.turn');
  const anyExpanded = [...turns].some(t => !t.classList.contains('collapsed-turn'));
  turns.forEach(t => {
    if (anyExpanded) {
      t.classList.add('collapsed-turn');
      t.querySelector('.turn-arrow').innerHTML = '&#9654;';
    } else {
      t.classList.remove('collapsed-turn');
      t.querySelector('.turn-arrow').innerHTML = '&#9660;';
    }
  });
  btn.textContent = anyExpanded ? 'Expand all' : 'Collapse all';
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

// Search
document.getElementById('searchBox').addEventListener('input', (e) => {
  const q = e.target.value.toLowerCase();
  if (!q) {
    renderSessionList(allSessions);
    return;
  }
  const filtered = allSessions.filter(s =>
    (s.preview || '').toLowerCase().includes(q) ||
    (s.git_branch || '').toLowerCase().includes(q) ||
    (s.session_id || '').toLowerCase().includes(q)
  );
  renderSessionList(filtered);
});

loadProjects();
</script>
</body>
</html>"""


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
