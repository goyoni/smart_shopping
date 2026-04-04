"""HTML template for Claude Code Session Viewer web UI."""


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

/* Hide button */
.session-item { position: relative; }
.session-hide-btn {
  position: absolute;
  top: 8px;
  right: 8px;
  background: none;
  border: none;
  color: var(--text-secondary);
  cursor: pointer;
  font-size: 14px;
  padding: 2px 6px;
  border-radius: 4px;
  opacity: 0;
  transition: opacity 0.15s;
}
.session-item:hover .session-hide-btn { opacity: 1; }
.session-hide-btn:hover { background: var(--bg-tertiary); color: var(--red); }
.session-item.hidden-session { opacity: 0.4; }
.show-hidden-toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-secondary);
  cursor: pointer;
  user-select: none;
}
.show-hidden-toggle input { cursor: pointer; }

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

/* Search highlights */
mark.search-hit {
  background: #d2992244;
  color: var(--text);
  border-radius: 2px;
  padding: 0 1px;
}
mark.search-hit.active {
  background: var(--orange);
  color: var(--bg);
}
.search-nav {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 24px;
  background: var(--bg-secondary);
  border-bottom: 1px solid var(--border);
  font-size: 13px;
  color: var(--text-secondary);
  position: sticky;
  top: 0;
  z-index: 11;
}
.search-nav button {
  background: var(--bg-tertiary);
  border: 1px solid var(--border);
  color: var(--text);
  padding: 3px 10px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 12px;
}
.search-nav button:hover { background: var(--border); }
.search-nav button:disabled { opacity: 0.3; cursor: default; }
.search-nav .search-count { font-size: 12px; min-width: 80px; }

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
      <div style="display:flex;justify-content:space-between;align-items:center;">
        <div class="stats" id="globalStats"></div>
        <label class="show-hidden-toggle"><input type="checkbox" id="showHidden" onchange="applyFilters()"> Show hidden</label>
      </div>
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
  applyFilters();
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
    <div class="session-item ${s.hidden ? 'hidden-session' : ''}" data-id="${s.session_id}" onclick="loadSession('${s.session_id}')">
      <button class="session-hide-btn" onclick="event.stopPropagation(); toggleHideSession('${s.session_id}', ${!s.hidden})" title="${s.hidden ? 'Unhide session' : 'Hide session'}">${s.hidden ? '&#x21a9;' : '&times;'}</button>
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
  const q = document.getElementById('searchBox').value.trim();
  if (q) applySearchHighlights(q);
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

// Filtering (search + hidden)
function applyFilters() {
  const q = document.getElementById('searchBox').value.toLowerCase();
  const showHidden = document.getElementById('showHidden').checked;
  let filtered = allSessions;
  if (!showHidden) {
    filtered = filtered.filter(s => !s.hidden);
  }
  if (q) {
    filtered = filtered.filter(s =>
      (s.searchable_text || '').toLowerCase().includes(q) ||
      (s.git_branch || '').toLowerCase().includes(q) ||
      (s.session_id || '').toLowerCase().includes(q)
    );
  }
  renderSessionList(filtered);
  updateGlobalStats(filtered);
}

document.getElementById('searchBox').addEventListener('input', applyFilters);

async function toggleHideSession(sessionId, hide) {
  await fetch('/api/hide-session', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({session_id: sessionId, hide, project: currentProject}),
  });
  // Update local state
  const s = allSessions.find(s => s.session_id === sessionId);
  if (s) s.hidden = hide;
  applyFilters();
}

// Search highlighting in conversation view
let searchHits = [];
let currentHitIndex = -1;

function applySearchHighlights(query) {
  searchHits = [];
  currentHitIndex = -1;
  if (!query) { removeSearchNav(); return; }

  const conversation = document.querySelector('.conversation');
  if (!conversation) return;

  // Find all text nodes in .content elements (user and assistant text)
  const contentEls = conversation.querySelectorAll('.turn-user .content, .turn-assistant .content, .tool-target');
  const lowerQ = query.toLowerCase();

  contentEls.forEach(el => {
    highlightTextNodes(el, lowerQ);
  });

  searchHits = [...conversation.querySelectorAll('mark.search-hit')];
  showSearchNav();
  if (searchHits.length > 0) navigateHit(0);
}

function highlightTextNodes(el, query) {
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  const matches = [];
  while (walker.nextNode()) {
    const node = walker.currentNode;
    const text = node.textContent.toLowerCase();
    let idx = text.indexOf(query);
    while (idx !== -1) {
      matches.push({ node, index: idx });
      idx = text.indexOf(query, idx + 1);
    }
  }
  // Process in reverse to preserve indices
  for (let i = matches.length - 1; i >= 0; i--) {
    const { node, index } = matches[i];
    const mark = document.createElement('mark');
    mark.className = 'search-hit';
    const range = document.createRange();
    range.setStart(node, index);
    range.setEnd(node, index + query.length);
    range.surroundContents(mark);
  }
}

function showSearchNav() {
  removeSearchNav();
  const main = document.getElementById('mainContent');
  const header = main.querySelector('.session-header');
  if (!header) return;
  const nav = document.createElement('div');
  nav.className = 'search-nav';
  nav.id = 'searchNav';
  nav.innerHTML = `
    <span class="search-count" id="searchCount">${searchHits.length} match${searchHits.length !== 1 ? 'es' : ''}</span>
    <button onclick="navigateHit(currentHitIndex - 1)" id="prevHitBtn">&#9650; Prev</button>
    <button onclick="navigateHit(currentHitIndex + 1)" id="nextHitBtn">&#9660; Next</button>
  `;
  header.after(nav);
}

function removeSearchNav() {
  document.getElementById('searchNav')?.remove();
}

function navigateHit(index) {
  if (searchHits.length === 0) return;
  // Wrap around
  if (index < 0) index = searchHits.length - 1;
  if (index >= searchHits.length) index = 0;

  // Deactivate previous
  if (currentHitIndex >= 0 && currentHitIndex < searchHits.length) {
    searchHits[currentHitIndex].classList.remove('active');
  }
  currentHitIndex = index;
  const hit = searchHits[currentHitIndex];
  hit.classList.add('active');

  // Expand any collapsed turn containing this hit
  const turn = hit.closest('.turn');
  if (turn && turn.classList.contains('collapsed-turn')) {
    turn.classList.remove('collapsed-turn');
    turn.querySelector('.turn-arrow').innerHTML = '&#9660;';
  }

  // Expand any collapsed content containing this hit
  const content = hit.closest('.content.collapsed');
  if (content) {
    content.classList.remove('collapsed');
    const btn = content.parentElement.querySelector('.expand-btn');
    if (btn) btn.textContent = 'Show less';
  }

  // Expand any hidden tool list containing this hit
  const toolList = hit.closest('.tool-list.hidden');
  if (toolList) {
    toolList.classList.remove('hidden');
    const btn = toolList.previousElementSibling;
    if (btn) btn.querySelector('.arrow').innerHTML = '&#9660;';
  }

  // Scroll into view
  hit.scrollIntoView({ behavior: 'smooth', block: 'center' });

  // Update counter
  const countEl = document.getElementById('searchCount');
  if (countEl) countEl.textContent = `${currentHitIndex + 1} / ${searchHits.length}`;
}

// Keyboard shortcuts for search navigation
document.addEventListener('keydown', (e) => {
  if (searchHits.length === 0) return;
  if (e.key === 'F3' || (e.ctrlKey && e.key === 'g')) {
    e.preventDefault();
    navigateHit(e.shiftKey ? currentHitIndex - 1 : currentHitIndex + 1);
  }
});

loadProjects();
</script>
</body>
</html>"""
