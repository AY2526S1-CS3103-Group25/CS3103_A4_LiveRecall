/*
 * LiveRecall browser client: live revision display and local recall.
 *
 * Implemented behavior:
 *   - opens a WebSocket
 *   - sends CLIENT_HELLO
 *   - replaces accepted revisions in the same caption row
 *   - distinguishes provisional/final captions and protects finalized text
 *   - removes expired finals and abandoned provisional rows
 *   - recalls 15/30/60 seconds of locally received finalized captions
 *
 * Graded TODOs:
 *   - automatic reconnect + correct resume state
 *   - HISTORY merge/deduplication
 *   - usable final UI
 */

const WS_URL = "ws://127.0.0.1:8765";
// Keep 60 seconds of finals plus a small margin; check expiry even when idle.
const CAPTION_RETENTION_MS = 70_000;
const CLEANUP_INTERVAL_MS = 5_000;
const statusEl = document.getElementById("status");
const liveEl = document.getElementById("live");
const liveEmptyEl = document.getElementById("live-empty");
const recallEl = document.getElementById("recall");
const recallHeadingEl = document.getElementById("recall-heading");
const recallEmptyEl = document.getElementById("recall-empty");
const recallButtons = Array.from(document.querySelectorAll("button[data-seconds]"));
let recallSeconds = 30;

// One accepted caption and DOM row per segment in the single L1 session.
const captionsBySegment = new Map();
// Keep only revision/finality for recently pruned captions, without text or rows.
// These records expire after one extra retention window.
const prunedSegments = new Map();
// Used only for cleanup: older provisionals may be abandoned as the stream moves on.
let latestSegment = 0;

let ws;
let clientId;

function numericClientId() {
  if (clientId !== undefined) return clientId;
  const key = "liverecall-client-id";
  clientId = Math.floor(100000 + Math.random() * 900000);
  try {
    const stored = Number(sessionStorage.getItem(key));
    if (Number.isSafeInteger(stored) && stored > 0) {
      clientId = stored;
    } else {
      sessionStorage.setItem(key, String(clientId));
    }
  } catch {
    // Storage may be blocked. Keep one in-memory ID so hello still succeeds.
  }
  return clientId;
}

function renderCaption(row, msg, stale = false) {
  const state = msg.final ? "FINAL" : stale ? "PROVISIONAL — STALE" : "PROVISIONAL";
  row.className = `caption-line ${msg.final ? "caption-final" : "caption-provisional"}`;
  // Treat source text as text, never as HTML.
  row.textContent = `[${state}] segment ${msg.segment} rev ${msg.revision}: ${msg.text}` +
    (stale ? " (No recent updates have arrived.)" : "");
}

function updateEmptyState() {
  liveEmptyEl.hidden = captionsBySegment.size > 0;
  const message = latestSegment === 0
    ? "Waiting for captions."
    : "No recent captions. Older captions have left the live window.";
  // Avoid repeating the same status announcement on every cleanup tick.
  if (liveEmptyEl.textContent !== message) liveEmptyEl.textContent = message;
}

function renderRecall() {
  // The user chose client receipt time for recall, not source_ts. An accepted
  // final's lastUpdatedAt never changes, so duplicates cannot extend its window.
  const cutoff = performance.now() - recallSeconds * 1000;
  const finals = Array.from(captionsBySegment.values())
    .filter(entry => entry.caption.final && entry.lastUpdatedAt >= cutoff)
    .sort((a, b) => a.caption.segment - b.caption.segment);
  const fragment = document.createDocumentFragment();
  for (const entry of finals) {
    // Separate rows leave the live pane intact; source text stays plain text.
    const row = document.createElement("div");
    row.dataset.segment = String(entry.caption.segment);
    renderCaption(row, entry.caption);
    fragment.appendChild(row);
  }
  const scrollTop = recallEl.scrollTop;
  // One argument avoids the JavaScript argument limit for large local windows.
  recallEl.replaceChildren(fragment);
  recallEl.scrollTop = scrollTop;
  recallEl.hidden = finals.length === 0;
  recallEmptyEl.hidden = finals.length > 0;
  const message = `No finalized captions in the previous ${recallSeconds} seconds.`;
  if (recallEmptyEl.textContent !== message) recallEmptyEl.textContent = message;
}

function selectRecallWindow(seconds) {
  if (![15, 30, 60].includes(seconds)) return;
  recallSeconds = seconds;
  recallHeadingEl.textContent = `Previous ${seconds} seconds`;
  for (const button of recallButtons) {
    button.setAttribute("aria-pressed", String(Number(button.dataset.seconds) === seconds));
  }
  // Filtering is entirely local, including when the socket is disconnected.
  renderRecall();
}

function acceptCaptionUpdate(msg) {
  // Validate before comparisons or state changes; extra fields are ignored.
  if (!Number.isSafeInteger(msg.segment) || msg.segment <= 0 ||
      !Number.isSafeInteger(msg.revision) || msg.revision <= 0 ||
      typeof msg.final !== "boolean") return;

  const current = captionsBySegment.get(msg.segment);
  const previous = current ? current.caption : prunedSegments.get(msg.segment);

  if (previous) {
    // Final text is durable, even if an unexpected newer update arrives.
    if (previous.final) return;
    // Never regress text; an equal-revision final may complete a provisional.
    if (msg.revision < previous.revision ||
        (msg.revision === previous.revision && !msg.final)) return;
  }

  // Match the server's lenient text policy without coercing arbitrary objects.
  const caption = typeof msg.text === "string" ? msg : { ...msg, text: "" };
  const row = current ? current.row : document.createElement("div");
  renderCaption(row, caption);
  if (!current) {
    // Late or restored captions belong in segment order, not arrival order.
    row.dataset.segment = String(msg.segment);
    const nextRow = Array.from(liveEl.children)
      .find(existing => Number(existing.dataset.segment) > msg.segment);
    liveEl.insertBefore(row, nextRow || null);
  }

  // Reuse the row so accepted revisions replace text without moving the line.
  captionsBySegment.set(msg.segment, {
    caption,
    row,
    stale: false,
    // Only accepted updates refresh the clock; finals get their full local window.
    lastUpdatedAt: performance.now()
  });
  // A late final or newer revision can restore a pruned provisional caption.
  prunedSegments.delete(msg.segment);
  latestSegment = Math.max(latestSegment, msg.segment);
  updateEmptyState();
  liveEl.scrollTop = liveEl.scrollHeight;
  if (msg.final) renderRecall();
}

function pruneExpiredCaptions() {
  const now = performance.now();
  const cutoff = now - CAPTION_RETENTION_MS;
  for (const [segment, entry] of prunedSegments) {
    if (entry.expiresAt <= now) prunedSegments.delete(segment);
  }
  for (const [segment, entry] of captionsBySegment) {
    // Preserve the newest active caption, but expire older idle provisionals.
    const canExpire = entry.caption.final || segment < latestSegment;
    if (canExpire && entry.lastUpdatedAt <= cutoff) {
      entry.row.remove();
      captionsBySegment.delete(segment);
      prunedSegments.set(segment, {
        revision: entry.caption.revision,
        final: entry.caption.final,
        expiresAt: now + CAPTION_RETENTION_MS
      });
    } else if (!canExpire && entry.lastUpdatedAt <= cutoff && !entry.stale) {
      // Keep the newest unfinished text, but make its inactivity visible.
      entry.stale = true;
      renderCaption(entry.row, entry.caption, true);
    }
  }
  updateEmptyState();
  // Expire the selected recall view even when the source is idle.
  renderRecall();
}

function connect() {
  statusEl.textContent = "connecting";
  ws = new WebSocket(WS_URL);

  ws.addEventListener("open", () => {
    statusEl.textContent = "connected";
    ws.send(JSON.stringify({
      type: "CLIENT_HELLO",
      client_id: numericClientId(),
      session: "L1",
      last_segment: 0,       // TODO (#4): resume from the latest final held locally.
      last_revision: 0,
      history_seconds: 30
    }));
  });

  ws.addEventListener("message", (event) => {
    let msg;
    try {
      msg = JSON.parse(event.data);
    } catch {
      return;
    }
    if (msg === null || typeof msg !== "object" || Array.isArray(msg)) return;
    if (msg.type === "CAPTION_UPDATE") {
      acceptCaptionUpdate(msg);
    } else if (msg.type === "HISTORY") {
      console.log(`HISTORY with ${msg.items?.length ?? 0} item(s)`, msg);
      // TODO: merge history without stale/duplicate final captions.
    }
  });

  ws.addEventListener("close", () => {
    statusEl.textContent = "disconnected";
    // TODO: automatic reconnect after a short delay, using finalized resume state.
  });

  ws.addEventListener("error", () => {
    statusEl.textContent = "connection error";
  });
}

for (const button of recallButtons) {
  button.addEventListener("click", () => {
    selectRecallWindow(Number(button.dataset.seconds));
  });
}

selectRecallWindow(30);
setInterval(pruneExpiredCaptions, CLEANUP_INTERVAL_MS);
connect();
