"""Data layer for Claude Code Session Viewer — session parsing, project listing, hidden-session management."""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

# Auto-detect project data directory
CLAUDE_DIR = Path.home() / ".claude"
PROJECTS_DIR = CLAUDE_DIR / "projects"
HISTORY_FILE = CLAUDE_DIR / "history.jsonl"


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


def get_hidden_sessions_file(project_dir: Path) -> Path:
    return project_dir / ".session-viewer-hidden.json"


def load_hidden_sessions(project_dir: Path) -> set[str]:
    hidden_file = get_hidden_sessions_file(project_dir)
    if hidden_file.exists():
        try:
            return set(json.loads(hidden_file.read_text()))
        except (json.JSONDecodeError, TypeError):
            return set()
    return set()


def save_hidden_sessions(project_dir: Path, hidden: set[str]):
    hidden_file = get_hidden_sessions_file(project_dir)
    hidden_file.write_text(json.dumps(sorted(hidden)))


def get_sessions_list(project_dir: Path) -> list[dict]:
    """Get a summary list of all sessions."""
    hidden = load_hidden_sessions(project_dir)
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

        # Collect all text for full-text search
        all_text_parts = []
        for turn in session["turns"]:
            all_text_parts.append(turn["user"]["text"])
            for am in turn["assistant_messages"]:
                if am.get("text"):
                    all_text_parts.append(am["text"])
                for tool in am.get("tools", []):
                    if tool.get("target"):
                        all_text_parts.append(tool["target"])

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
            "hidden": session["session_id"] in hidden,
            "searchable_text": "\n".join(all_text_parts),
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
