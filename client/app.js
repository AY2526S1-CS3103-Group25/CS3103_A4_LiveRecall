/*
 * LiveRecall browser client: live revision display (issue #3).
 *
 * Implemented behavior:
 *   - opens a WebSocket
 *   - sends CLIENT_HELLO
 *   - replaces accepted revisions in the same caption row
 *   - distinguishes provisional/final captions and protects finalized text
 *   - removes expired finals and abandoned provisional rows
 *
 * Graded TODOs:
 *   - implement 30 s default recall + 15/30/60 s views
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
const recallEl = document.getElementById("recall");

// One accepted caption and DOM row per segment in the single L1 session.
const captionsBySegment = new Map();
// Keep only revision/finality for recently pruned captions, without text or rows.
// These records expire after one extra retention window.
const prunedSegments = new Map();
// Used only for cleanup: older provisionals may be abandoned as the stream moves on.
let latestSegment = 0;

let ws;

function numericClientId() {
  const key = "liverecall-client-id";
  let value = sessionStorage.getItem(key);
  if (!value) {
    value = String(Math.floor(100000 + Math.random() * 900000));
    sessionStorage.setItem(key, value);
  }
  return Number(value);
}

function renderCaption(row, msg) {
  const state = msg.final ? "FINAL" : "PROVISIONAL";
  row.className = `caption-line ${msg.final ? "caption-final" : "caption-provisional"}`;
  // Treat source text as text, never as HTML.
  row.textContent = `[${state}] segment ${msg.segment} rev ${msg.revision}: ${msg.text}`;
}

function acceptCaptionUpdate(msg) {
  const current = captionsBySegment.get(msg.segment);
  const previous = current ? current.caption : prunedSegments.get(msg.segment);

  if (previous) {
    // Final text is durable, even if an unexpected newer update arrives.
    if (previous.final) return;
    // Never regress text; an equal-revision final may complete a provisional.
    if (msg.revision < previous.revision ||
        (msg.revision === previous.revision && !msg.final)) return;
  }

  const row = current ? current.row : document.createElement("div");
  renderCaption(row, msg);
  if (!current) {
    // Late or restored captions belong in segment order, not arrival order.
    row.dataset.segment = String(msg.segment);
    const nextRow = Array.from(liveEl.children)
      .find(existing => Number(existing.dataset.segment) > msg.segment);
    liveEl.insertBefore(row, nextRow || null);
  }

  // Reuse the row so accepted revisions replace text without moving the line.
  captionsBySegment.set(msg.segment, {
    caption: msg,
    row,
    // Only accepted updates refresh the clock; finals get their full local window.
    lastUpdatedAt: performance.now()
  });
  // A late final or newer revision can restore a pruned provisional caption.
  prunedSegments.delete(msg.segment);
  latestSegment = Math.max(latestSegment, msg.segment);
  liveEl.scrollTop = liveEl.scrollHeight;
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
    }
  }
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
    const msg = JSON.parse(event.data);
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

for (const button of document.querySelectorAll("button[data-seconds]")) {
  button.addEventListener("click", () => {
    const seconds = Number(button.dataset.seconds);
    recallEl.textContent = `TODO: show finalized captions from the previous ${seconds} seconds using local state.`;
  });
}

setInterval(pruneExpiredCaptions, CLEANUP_INTERVAL_MS);
connect();
