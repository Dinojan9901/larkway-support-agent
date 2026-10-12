/* Larkway support agent — chat UI with live tool trace, confirmation gate
   and a trust panel showing what the answer was built from. */

const state = {
  users: [],
  tools: [],
  userId: null,
  sessionId: crypto.randomUUID(),
  chatEnabled: false,
  busy: false,
};

const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};

const SUGGESTIONS = {
  customer: [
    "Can I cancel SHP-5001 without a cancellation fee? Explain why.",
    "A pickup was two hours late because of carrier fault. Should I get a service credit?",
    "What's my first-response time if we hit a complete outage?",
    "Show me the orders on my account.",
    "What's the row limit on bulk upload, and why do my 3,000-row files time out?",
  ],
  internal: [
    "What needs my attention right now?",
    "Can Halcyon Foods cancel SHP-5001 free of charge? Check for past guidance that conflicts.",
    "Which open tickets have breached SLA, and against which contract?",
    "SHP-6002 missed its pickup — work out what Brightmoor is owed and prepare the credit.",
    "Have we given any customer incorrect advice?",
  ],
};

/* ---------------------------------------------------------------- bootstrap */
async function boot() {
  const res = await fetch("/api/bootstrap");
  const data = await res.json();
  state.users = data.users;
  state.tools = data.tools;
  state.chatEnabled = data.chat_enabled;
  state.chatDisabledReason = data.chat_disabled_reason || "No model is configured";

  const select = $("#user-select");
  const groups = { customer: "Customers", internal: "Larkway staff" };
  for (const ctx of ["customer", "internal"]) {
    const og = el("optgroup");
    og.label = groups[ctx];
    data.users.filter((u) => u.context === ctx).forEach((u) => {
      const opt = el("option", null, u.name);
      opt.value = u.user_id;
      og.appendChild(opt);
    });
    select.appendChild(og);
  }
  select.value = data.users[0].user_id;
  state.userId = select.value;

  $("#model-badge").textContent = data.chat_enabled
    ? data.model
    : "chat disabled — no model configured";
  $("#dataset-note").textContent =
    `Snapshot ${data.dataset.snapshot_at} · ${data.dataset.accounts} accounts · ` +
    `${data.dataset.orders} orders · ${data.dataset.tickets} tickets` +
    (data.policy_pack_ok ? " · policy pack verified" : " · POLICY PACK MISMATCH");

  select.addEventListener("change", () => {
    state.userId = select.value;
    state.sessionId = crypto.randomUUID();
    $("#transcript").innerHTML = "";
    renderUser();
    renderTools();
    renderSuggestions();
    renderEmpty();
    if (currentView() === "ops") loadOps();
  });

  renderUser();
  renderTools();
  renderSuggestions();
  renderEmpty();
  wireComposer();
  wireTabs();
}

const currentUser = () => state.users.find((u) => u.user_id === state.userId);
const currentView = () => ($("#view-ops").classList.contains("active") ? "ops" : "chat");

function renderUser() {
  const u = currentUser();
  const box = $("#user-detail");
  box.innerHTML = "";
  const role = el("div", null, u.role.replace("_", " "));
  role.style.textTransform = "capitalize";
  box.appendChild(role);
  const chips = el("div");
  if (u.account_id) chips.appendChild(el("span", "chip", u.account_id));
  if (u.context === "internal") chips.appendChild(el("span", "chip internal", "all accounts"));
  u.labels.forEach((l) => chips.appendChild(el("span", "chip", l)));
  box.appendChild(chips);
  $("#ops-tab").disabled = u.context !== "internal";
}

function renderTools() {
  const internal = currentUser().context === "internal";
  const list = $("#tool-list");
  list.innerHTML = "";
  state.tools.forEach((t) => {
    const available = !t.internal_only || internal;
    const row = el("div", `tool-row${available ? "" : " dim"}`);
    row.appendChild(el("span", `dot ${t.family}`));
    row.appendChild(el("span", null, t.name));
    row.title = available ? t.label : "Internal users only";
    list.appendChild(row);
  });
}

function renderSuggestions() {
  const box = $("#suggestions");
  box.innerHTML = "";
  SUGGESTIONS[currentUser().context].forEach((text) => {
    const b = el("button", "suggestion", text);
    b.type = "button";
    b.addEventListener("click", () => {
      $("#input").value = text;
      $("#input").focus();
      autosize();
    });
    box.appendChild(b);
  });
}

function renderEmpty() {
  const u = currentUser();
  const t = $("#transcript");
  t.innerHTML = "";
  if (!state.chatEnabled) {
    const banner = el("div", "banner",
      `${state.chatDisabledReason}, so chat is disabled. The operations dashboard, ` +
      "the policy engine and the eval harness still work — see the README.");
    t.appendChild(banner);
  }
  const box = el("div", "empty");
  box.appendChild(el("h2", null,
    u.context === "internal"
      ? `Internal support console — ${u.name}`
      : `Hello ${u.name.split(" ")[0]}`));
  box.appendChild(el("p", null,
    u.context === "internal"
      ? "Ask about any account, or start with what needs attention right now."
      : `Ask about your orders, your agreement, or Larkway's policies. You'll only ever see ${u.account_id} data.`));
  box.appendChild(el("p", null, "Every answer shows the tools it used and the sources it relied on."));
  t.appendChild(box);
}

/* ------------------------------------------------------------------- chat */
function wireComposer() {
  const form = $("#composer");
  const input = $("#input");
  input.addEventListener("input", autosize);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); }
  });
  form.addEventListener("submit", (e) => { e.preventDefault(); send(); });
  $("#reset-btn").addEventListener("click", async () => {
    await fetch("/api/reset", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: state.sessionId, user_id: state.userId }),
    });
    state.sessionId = crypto.randomUUID();
    renderEmpty();
  });
}

function autosize() {
  const i = $("#input");
  i.style.height = "auto";
  i.style.height = Math.min(i.scrollHeight, 180) + "px";
}

function wireTabs() {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      if (tab.disabled) return;
      document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
      document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
      tab.classList.add("active");
      $("#view-" + tab.dataset.view).classList.add("active");
      if (tab.dataset.view === "ops") loadOps();
    });
  });
}

async function send() {
  if (state.busy) return;
  const input = $("#input");
  const text = input.value.trim();
  if (!text) return;

  const transcript = $("#transcript");
  transcript.querySelector(".empty")?.remove();

  const userTurn = el("div", "turn user");
  userTurn.appendChild(el("div", "bubble", text));
  transcript.appendChild(userTurn);

  input.value = "";
  autosize();
  setBusy(true);

  const turn = el("div", "turn");
  const answer = el("div", "answer");
  const trace = el("div", "trace");
  const body = el("div", "answer-text");
  answer.appendChild(trace);
  answer.appendChild(body);
  turn.appendChild(answer);
  transcript.appendChild(turn);
  scroll();

  const traceNodes = new Map();
  let raw = "";
  let hasText = false;

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, user_id: state.userId, session_id: state.sessionId }),
    });
    if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const chunks = buffer.split("\n\n");
      buffer = chunks.pop() ?? "";

      for (const chunk of chunks) {
        const line = chunk.split("\n").find((l) => l.startsWith("data: "));
        if (!line) continue;
        let ev;
        try { ev = JSON.parse(line.slice(6)); } catch { continue; }

        switch (ev.type) {
          case "tool_start": {
            const node = el("div", "trace-item running");
            node.appendChild(el("span", `dot ${ev.family}`));
            const main = el("div", "trace-main");
            main.appendChild(el("div", "trace-label", ev.label));
            if (ev.detail) main.appendChild(el("div", "trace-detail", ev.detail));
            node.appendChild(main);
            trace.appendChild(node);
            traceNodes.set(ev.tool_use_id, { node, main });
            scroll();
            break;
          }
          case "tool_end": {
            const entry = traceNodes.get(ev.tool_use_id);
            if (entry) {
              entry.node.classList.remove("running");
              if (!ev.ok) entry.node.classList.add("failed");
              entry.main.appendChild(el("div", "trace-result", ev.summary));
            }
            scroll();
            break;
          }
          case "text":
            raw += ev.text;
            hasText = true;
            body.innerHTML = markup(raw);
            scroll();
            break;
          case "proposal":
            answer.appendChild(proposalCard(ev.action));
            scroll();
            break;
          case "trust":
            if (hasTrustContent(ev.trust)) answer.appendChild(trustPanel(ev.trust));
            scroll();
            break;
          case "error":
            answer.appendChild(el("div", "banner", ev.message));
            scroll();
            break;
        }
      }
    }
    if (!hasText) body.remove();
  } catch (err) {
    answer.appendChild(el("div", "banner", `Request failed: ${err.message}`));
  } finally {
    setBusy(false);
  }
}

function setBusy(v) {
  state.busy = v;
  $("#send").disabled = v;
  $("#input").disabled = v;
  if (!v) $("#input").focus();
}

function scroll() {
  const t = $("#transcript");
  t.scrollTop = t.scrollHeight;
}

/* Minimal, escaped markdown: bold, inline code, and dash lists. */
function markup(text) {
  const esc = text
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const lines = esc.split("\n");
  let html = "";
  let inList = false;
  for (const line of lines) {
    const item = line.match(/^\s*[-*•]\s+(.*)$/);
    if (item) {
      if (!inList) { html += "<ul>"; inList = true; }
      html += `<li>${inline(item[1])}</li>`;
    } else {
      if (inList) { html += "</ul>"; inList = false; }
      html += inline(line) + "\n";
    }
  }
  if (inList) html += "</ul>";
  return html.replace(/\n{3,}/g, "\n\n").trimEnd();
}
function inline(s) {
  return s
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
}

/* ------------------------------------------------------- confirmation gate */
function proposalCard(action) {
  const card = el("div", "proposal");
  const head = el("div", "proposal-head");
  head.appendChild(el("span", "proposal-tag", "Needs your confirmation"));
  head.appendChild(el("span", "proposal-title", action.label));
  card.appendChild(head);

  card.appendChild(el("pre", null, action.preview));
  (action.warnings || []).forEach((w) => card.appendChild(el("div", "warn-line", `! ${w}`)));

  const actions = el("div", "proposal-actions");
  const confirm = el("button", "btn confirm", "Confirm and run");
  const cancel = el("button", "btn cancel", "Discard");
  actions.appendChild(confirm);
  actions.appendChild(cancel);
  card.appendChild(actions);

  const settle = (cls, message) => {
    actions.remove();
    card.classList.add("settled");
    head.querySelector(".proposal-tag").remove();
    card.appendChild(el("div", `outcome ${cls}`, message));
  };

  confirm.addEventListener("click", async () => {
    confirm.disabled = cancel.disabled = true;
    confirm.textContent = "Running…";
    const res = await fetch(`/api/actions/${action.action_id}/confirm`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: state.userId }),
    });
    const data = await res.json();
    if (!res.ok) { settle("void", `Blocked: ${data.detail}`); return; }
    const ref = data.action?.result?.reference || action.action_id;
    settle("done", `Executed — reference ${ref}`);
  });

  cancel.addEventListener("click", async () => {
    confirm.disabled = cancel.disabled = true;
    await fetch(`/api/actions/${action.action_id}/cancel`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: state.userId }),
    });
    settle("void", "Discarded — nothing was changed.");
  });

  return card;
}

/* ------------------------------------------------------------ trust panel */
function hasTrustContent(t) {
  return t && (t.citations?.length || t.conflicts?.length || t.unknowns?.length ||
    t.assumptions?.length || t.escalation_reasons?.length);
}

function trustPanel(t) {
  const panel = el("div", "trust");
  const head = el("div", "trust-head");
  head.appendChild(el("span", `conf ${t.confidence}`, `${t.confidence} confidence`));

  const bits = [];
  if (t.citations?.length) bits.push(`${t.citations.length} source${t.citations.length > 1 ? "s" : ""}`);
  if (t.conflicts?.length) bits.push(`${t.conflicts.length} conflict${t.conflicts.length > 1 ? "s" : ""} resolved`);
  if (t.unknowns?.length) bits.push(`${t.unknowns.length} unknown${t.unknowns.length > 1 ? "s" : ""}`);
  if (t.escalation_reasons?.length) bits.push("human review advised");
  head.appendChild(el("span", "trust-summary", bits.join(" · ")));
  head.appendChild(el("span", "trust-caret", "▶"));
  panel.appendChild(head);

  const bodyEl = el("div", "trust-body");

  if (t.conflicts?.length) {
    const g = el("div");
    g.appendChild(el("div", "trust-group-title", "Conflicts detected and resolved"));
    t.conflicts.forEach((c) => {
      const box = el("div", "conflict");
      box.appendChild(el("div", "conflict-type", (c.type || "").replace(/_/g, " ")));
      if (c.winning_source) {
        const line = el("div", "line");
        line.innerHTML = `<b>Governs:</b> ${escapeHtml(c.winning_source)}`;
        box.appendChild(line);
      }
      if (c.losing_source) {
        const line = el("div", "line");
        line.innerHTML = `<b>Set aside:</b> ${escapeHtml(c.losing_source)}`;
        box.appendChild(line);
      }
      if (c.source) {
        const line = el("div", "line");
        line.innerHTML = `<b>Past guidance (${escapeHtml(c.source)}):</b> ${escapeHtml(c.claim || "")}`;
        box.appendChild(line);
      }
      if (c.resolution) box.appendChild(el("div", "line", c.resolution));
      g.appendChild(box);
    });
    bodyEl.appendChild(g);
  }

  if (t.citations?.length) {
    const g = el("div");
    g.appendChild(el("div", "trust-group-title", "Sources used"));
    t.citations.forEach((c) => {
      const box = el("div", "cite");
      const h = el("div", "cite-head");
      h.appendChild(el("span", `tier t${c.authority_tier}`, `T${c.authority_tier}`));
      h.appendChild(el("span", "cite-doc", c.document));
      if (c.section) h.appendChild(el("span", "cite-sec", `· ${c.section}`));
      if (c.status && c.status !== "CURRENT" && c.status !== "ACTIVE") {
        h.appendChild(el("span", "status-tag deprecated", c.status));
      }
      box.appendChild(h);
      if (c.quote) box.appendChild(el("div", "cite-quote", `“${c.quote}”`));
      g.appendChild(box);
    });
    bodyEl.appendChild(g);
  }

  const noteGroup = (title, items) => {
    if (!items?.length) return;
    const g = el("div");
    g.appendChild(el("div", "trust-group-title", title));
    const ul = el("ul", "note-list");
    items.forEach((i) => ul.appendChild(el("li", null, i)));
    g.appendChild(ul);
    bodyEl.appendChild(g);
  };
  noteGroup("Unknown facts", t.unknowns);
  noteGroup("Assumptions", t.assumptions);
  noteGroup("Why human review is advised", t.escalation_reasons);

  panel.appendChild(bodyEl);
  head.addEventListener("click", () => panel.classList.toggle("open"));
  return panel;
}

function escapeHtml(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

/* ------------------------------------------------------- ops dashboard */
async function loadOps() {
  const body = $("#ops-body");
  if (currentUser().context !== "internal") {
    body.innerHTML = "";
    const locked = el("div", "locked");
    locked.appendChild(el("h3", null, "Internal view"));
    locked.appendChild(el("p", null,
      "Operations signals are restricted to Larkway staff. Switch to a staff user to view them."));
    body.appendChild(locked);
    return;
  }

  body.innerHTML = "";
  const inner = el("div", "ops-inner");
  body.appendChild(inner);
  inner.appendChild(el("div", "ops-sub", "Loading…"));

  const res = await fetch(`/api/signals?user_id=${encodeURIComponent(state.userId)}`);
  if (!res.ok) {
    inner.innerHTML = "";
    inner.appendChild(el("div", "banner", `Could not load signals (HTTP ${res.status}).`));
    return;
  }
  const data = await res.json();
  inner.innerHTML = "";

  const head = el("div", "ops-head");
  head.appendChild(el("h2", null, "What needs attention"));
  inner.appendChild(head);
  inner.appendChild(el("div", "ops-sub",
    `Computed from the full support surface at ${data.summary.as_of}. ` +
    `Nobody has to ask — these are found by sweeping every ticket, order and past resolution.`));

  const stats = el("div", "stat-row");
  const p = data.summary.by_priority || {};
  const mk = (value, label, cls) => {
    const s = el("div", `stat ${cls || ""}`);
    s.appendChild(el("div", "stat-value", String(value)));
    s.appendChild(el("div", "stat-label", label));
    return s;
  };
  stats.appendChild(mk(data.summary.total, "signals", ""));
  stats.appendChild(mk(p.critical || 0, "critical", "critical"));
  stats.appendChild(mk(p.high || 0, "high", "high"));
  stats.appendChild(mk((data.summary.accounts_affected || []).length, "accounts affected", ""));
  inner.appendChild(stats);

  data.signals.forEach((s) => inner.appendChild(signalCard(s)));
}

function signalCard(s) {
  const card = el("div", `sig ${s.priority}`);
  const top = el("div", "sig-top");
  top.appendChild(el("span", `pri ${s.priority}`, s.priority));
  top.appendChild(el("span", "cat", s.category.replace(/_/g, " ")));
  card.appendChild(top);
  card.appendChild(el("div", "sig-title", s.title));
  card.appendChild(el("div", "sig-detail", s.detail));

  const meta = el("div", "sig-meta");
  (s.evidence || []).forEach((e) => meta.appendChild(el("span", "meta-chip", e)));
  Object.entries(s.metrics || {}).forEach(([k, v]) => {
    if (v === null || v === undefined || v === "" || Array.isArray(v)) return;
    meta.appendChild(el("span", "meta-chip", `${k.replace(/_/g, " ")}: ${v}`));
  });
  card.appendChild(meta);

  if (s.recommended_action) {
    const a = s.recommended_action;
    const fields = Object.entries(a.payload || {})
      .map(([k, v]) => `${k.replace(/_/g, " ")} ${v}`)
      .join(", ");
    const rec = el("div", "sig-rec");
    rec.innerHTML = `<b>Suggested next step:</b> ${escapeHtml(
      a.action_type.replace(/_/g, " "))}${fields ? " — " + escapeHtml(fields) : ""}`;
    card.appendChild(rec);
  }
  return card;
}

boot();
