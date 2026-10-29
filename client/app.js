/*
 * LiveRecall browser client: live revision display (issue #3) and
 * automatic reconnect with visible connection state (issue #4).
 *
 * Implemented behavior:
 *   - opens a WebSocket
 *   - sends CLIENT_HELLO, resuming from the highest contiguous finalized caption held locally
 *   - replaces accepted revisions in the same caption row
 *   - distinguishes provisional/final captions and protects finalized text
 *   - removes expired finals and abandoned provisional rows
 *   - reconnects automatically with backoff and reports connection state
 *
 * Graded TODOs:
 *   - implement 30 s default recall + 15/30/60 s views
 *   - HISTORY merge/deduplication
 *   - usable final UI
 */

const WS_URL = "ws://127.0.0.1:8765";
// Keep 60 seconds of finals plus a small margin; check expiry even when idle.
const CAPTION_RETENTION_MS = 70_000;
const CLEANUP_INTERVAL_MS = 5_000;
// Reconnect backoff: double the wait each failed attempt, capped, so a server
// that is still down is not hammered; a successful open resets the counter.
const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 10000;
const statusEl = document.getElementById("status");
const liveEl = document.getElementById("live");
const liveEmptyEl = document.getElementById("live-empty");
const recallEl = document.getElementById("recall");

// One accepted caption and DOM row per segment in the single L1 session.
const captionsBySegment = new Map();
// Keep only revision/finality for recently pruned captions, without text or rows.
// These records expire after one extra retention window.
const prunedSegments = new Map();
// Used only for cleanup: older provisionals may be abandoned as the stream moves on.
let latestSegment = 0;
// Resume state for CLIENT_HELLO: the highest segment N such that this client
// holds every finalized caption from its first received final through N.
// We report this contiguous run rather than a high-water mark: the server may
// skip everything at or below the reported point, so a mark that jumps over a
// segment we never got would make that segment unreachable.
// pendingFinals only ever holds segments *above* the run head (gaps), so its
// size is bounded by the size of any out-of-order delivery window.
const pendingFinals = new Map(); // segment -> revision of held, not-yet-contiguous finals
let contiguousFinal = { segment: 0, revision: 0 };
let sawFirstFinal = false;

function recordFinalForResume(segment, revision) {
  if (!sawFirstFinal) {
    // The first final this tab accepts anchors the run; the live stream is
    // ordered, so it is the smallest segment this client will ever hold.
    sawFirstFinal = true;
    contiguousFinal = { segment, revision };
    return;
  }
  if (segment <= contiguousFinal.segment) return; // at/below the head: already covered
  if (segment === contiguousFinal.segment + 1) {
    contiguousFinal = { segment, revision };
    pendingFinals.delete(segment);
    // Fold in any higher finals already held, in order.
    while (pendingFinals.has(contiguousFinal.segment + 1)) {
      const next = contiguousFinal.segment + 1;
      contiguousFinal = { segment: next, revision: pendingFinals.get(next) };
      pendingFinals.delete(next);
    }
  } else {
    // Gap above the head: hold the final until (if ever) the missing segment
    // arrives; the resume report stays at the run head meanwhile.
    pendingFinals.set(segment, revision);
  }
}

let ws;
// Reconnect bookkeeping. The timer is cleared before each new dial so a
// stale schedule can never open a duplicate connection.
let reconnectAttempts = 0;
let reconnectTimer = null;

function numericClientId() {
  const key = "liverecall-client-id";
  let value = sessionStorage.getItem(key);
  if (!value) {
    value = String(Math.floor(100000 + Math.random() * 900000));
    sessionStorage.setItem(key, value);
  }
  return Number(value);
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
    stale: false,
    // Only accepted updates refresh the clock; finals get their full local window.
    lastUpdatedAt: performance.now()
  });
  // A late final or newer revision can restore a pruned provisional caption.
  prunedSegments.delete(msg.segment);
  latestSegment = Math.max(latestSegment, msg.segment);
  // Resume state follows finalized captions only; recordFinalForResume keeps
  // a contiguous run, so the reported point is never past a segment this
  // client does not actually hold.
  if (msg.final) recordFinalForResume(msg.segment, msg.revision);
  updateEmptyState();
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
    } else if (!canExpire && entry.lastUpdatedAt <= cutoff && !entry.stale) {
      // Keep the newest unfinished text, but make its inactivity visible.
      entry.stale = true;
      renderCaption(entry.row, entry.caption, true);
    }
  }
  updateEmptyState();
}

function setStatus(text, connected = false) {
  statusEl.textContent = text;
  statusEl.style.color = connected ? "green" : "tomato";
}

function connect() {
  // A previous schedule must not fire after we have already dialed again.
  if (reconnectTimer !== null) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  setStatus("connecting");
  const socket = new WebSocket(WS_URL);
  ws = socket;

  socket.addEventListener("open", () => {
    reconnectAttempts = 0;
    setStatus("connected", true);
    socket.send(JSON.stringify({
      type: "CLIENT_HELLO",
      client_id: numericClientId(),
      session: "L1",
      last_segment: contiguousFinal.segment,
      last_revision: contiguousFinal.revision,
      history_seconds: 30
    }));
  });

  socket.addEventListener("message", (event) => {
    let msg;
    try {
      msg = JSON.parse(event.data);
    } catch {
      return;
    }
    if (msg === null || typeof msg !== "object" || Array.isArray(msg)) return;
    if (msg.type === "CAPTION_UPDATE") {
      // No socket-identity guard here on purpose: a caption that arrives just
      // as a socket dies is still valid stream data, and the revision checks
      // in acceptCaptionUpdate already reject stale/duplicate state.
      acceptCaptionUpdate(msg);
    } else if (msg.type === "HISTORY") {
      console.log(`HISTORY with ${msg.items?.length ?? 0} item(s)`, msg);
      // TODO: merge history without stale/duplicate final captions.
    }
  });

  socket.addEventListener("close", () => {
    // A late close from a socket that has already been replaced must not
    // reschedule a second, duplicate reconnection.
    if (socket !== ws) return;
    const attempt = ++reconnectAttempts;
    setStatus(`disconnected - reconnecting (attempt ${attempt})`);
    const delay = Math.min(RECONNECT_BASE_MS * 2 ** (attempt - 1), RECONNECT_MAX_MS);
    reconnectTimer = setTimeout(connect, delay);
  });

  socket.addEventListener("error", () => {
    // Browsers always deliver a close event after an error, and the close
    // handler owns the reconnect schedule; this only reflects the state.
    if (socket === ws) setStatus("connection error");
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
