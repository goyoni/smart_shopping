Launch the Claude Code Session Viewer — a web UI for browsing conversation history across sessions.

## Steps

1. **Start the viewer server:**
   ```bash
   python3 tools/session-viewer/server.py --port 5111 &
   ```

2. **Confirm it's running:**
   ```bash
   curl -s http://localhost:5111/api/projects | python3 -m json.tool
   ```

3. **Report to the user:**
   - Print: `Session Viewer running at http://localhost:5111`
   - Include the number of projects and sessions found.
   - Remind the user to stop it when done: `pkill -f "session-viewer/server.py"`

## Notes

- The viewer reads Claude Code's built-in JSONL logs from `~/.claude/projects/` — no extra logging or hooks needed.
- If port 5111 is already in use, kill the existing process first: `pkill -f "session-viewer/server.py"`
- The server runs in the background so the user can continue working.
