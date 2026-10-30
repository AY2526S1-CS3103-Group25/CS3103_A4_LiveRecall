/*
 * LiveRecall browser client: live revision display, local recall, and
 * automatic reconnect with visible connection state.
 *
 * Implemented behavior:
 *   - opens a WebSocket
 *   - sends CLIENT_HELLO, resuming from the highest contiguous finalized caption held locally
 *   - replaces accepted revisions in the same caption row
 *   - distinguishes provisional/final captions and protects finalized text
 *   - removes expired finals and abandoned provisional rows
 *   - recalls 15/30/60 seconds of locally received finalized captions
 *   - reconnects automatically with backoff and reports connection state
 *
 * Graded TODOs:
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
const recallHeadingEl = document.getElementById("recall-heading");
const recallEmptyEl = document.getElementById("recall-empty");
const recallButtons = Array.from(document.querySelectorAll("button[data-seconds]"));
let recallSeconds = 30;
let renderedRecallEntries = null;

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
// pendingFinals holds finals parked *above* a gap (the missing segment just
// past the run head). Each entry keeps the time it was parked:
//   - an entry older than the local retention window is dropped on its own
//     age (its final is past the 60 s this client displays and past the
//     server's window too), which alone bounds the map to one window of the
//     stream even when gaps recur;
//   - the gap itself is abandoned once its own age passes the window, using
//     real observation times only. The age is never reset to "now" for
//     already-old gaps, so recurring gaps cannot restart the retention
//     period and drag the head further behind.
const pendingFinals = new Map(); // segment -> { revision, parkedAt }
let contiguousFinal = { segment: 0, revision: 0 };
let sawFirstFinal = false;
// When the missing segment just past the run head was first observed (set
// only from real observation times; 0 means no gap is currently observed).
let gapSince = 0;

function minParkedAt() {
  // The gap's age is the age of its first observable evidence: the oldest
  // parked entry above it. All parked entries sit above the current gap.
  let min = Infinity;
  for (const held of pendingFinals.values()) {
    if (held.parkedAt < min) min = held.parkedAt;
  }
  return min;
}

function oldestParked() {
  let segment = 0;
  let entry = null;
  for (const [seg, held] of pendingFinals) {
    if (entry === null || seg < segment) {
      segment = seg;
      entry = held;
    }
  }
  return entry === null ? null : { segment, entry };
}

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
      contiguousFinal = { segment: next, revision: pendingFinals.get(next).revision };
      pendingFinals.delete(next);
    }
    // The run moved: any remaining gap was first observed when its oldest
    // parked entry was parked, so keep that real age rather than re-aging it.
    gapSince = pendingFinals.size === 0 ? 0 : minParkedAt();
  } else {
    // Gap above the head: hold the final until (if ever) the missing segment
    // arrives; the resume report stays at the run head meanwhile.
    if (gapSince === 0) gapSince = performance.now();
    pendingFinals.set(segment, { revision, parkedAt: performance.now() });
  }
}

let ws;
let clientId;
// Reconnect bookkeeping. The timer is cleared before each new dial so a
// stale schedule can never open a duplicate connection. openedAt is the
// time of the last successful open (-1 if the current socket never opened),
// so a connection must actually survive before it may reset the backoff.
let reconnectAttempts = 0;
let reconnectTimer = null;
let openedAt = -1;

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
  recallEl.hidden = finals.length === 0;
  recallEmptyEl.hidden = finals.length > 0;
  const message = `No finalized captions in the previous ${recallSeconds} seconds.`;
  if (recallEmptyEl.textContent !== message) recallEmptyEl.textContent = message;

  // Accepted finals are immutable. Keep their DOM nodes when the ordered
  // entries are unchanged, preserving selection across idle cleanup ticks.
  if (renderedRecallEntries && finals.length === renderedRecallEntries.length &&
      finals.every((entry, index) => entry === renderedRecallEntries[index])) return;

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
  renderedRecallEntries = finals;
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
  // Resume state follows finalized captions only; recordFinalForResume keeps
  // a contiguous run, so the reported point is never past a segment this
  // client does not actually hold.
  if (msg.final) recordFinalForResume(msg.segment, msg.revision);
  updateEmptyState();
  liveEl.scrollTop = liveEl.scrollHeight;
  if (msg.final) renderRecall();
}

function pruneExpiredCaptions() {
  const now = performance.now();
  const cutoff = now - CAPTION_RETENTION_MS;
  // Drop parked finals on their own age: past the retention window their
  // final is older than the 60 s this client can display and the server's
  // window is gone too, so it is unreachable. This alone bounds the map to
  // one window of the stream, even with recurring gaps.
  for (const [segment, held] of pendingFinals) {
    if (held.parkedAt <= cutoff) pendingFinals.delete(segment);
  }
  // No parked entry left means no gap is observed any more: drop the stale
  // observation time so a later gap is aged from its own first evidence
  // instead of inheriting an already-expired age.
  if (pendingFinals.size === 0) gapSince = 0;
  // Abandon the gap itself once its own age passed the window: resume from
  // the oldest parked final instead of letting the head stick. gapSince is
  // only ever set from real observation times, never reset to "now".
  if (gapSince !== 0 && pendingFinals.size > 0 && now - gapSince > CAPTION_RETENTION_MS) {
    const { segment, entry } = oldestParked();
    contiguousFinal = { segment, revision: entry.revision };
    pendingFinals.delete(segment);
    while (pendingFinals.has(contiguousFinal.segment + 1)) {
      const next = contiguousFinal.segment + 1;
      contiguousFinal = { segment: next, revision: pendingFinals.get(next).revision };
      pendingFinals.delete(next);
    }
    // The new gap's age is the real age of its oldest parked entry.
    gapSince = pendingFinals.size === 0 ? 0 : minParkedAt();
  }
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

function setStatus(text, connected = false) {
  statusEl.textContent = text;
  // Colour lives in the stylesheet (see .status-* rules in index.html),
  // matching how the caption rows are styled.
  statusEl.className = connected ? "status-connected" : "status-offline";
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
    openedAt = performance.now();
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
      // TODO(#8): route each item through acceptCaptionUpdate (which calls
      // recordFinalForResume) so replayed finals render, advance the resume
      // run over segments missed during a disconnect, and dedupe against
      // already-held state. Until then, a disconnect gap clears only via the
      // retention-based abandon in pruneExpiredCaptions.
    }
  });

  socket.addEventListener("close", () => {
    // A late close from a socket that has already been replaced must not
    // reschedule a second, duplicate reconnection.
    if (socket !== ws) return;
    // Only a connection that actually survived earns a fresh backoff. A
    // flash open (accepted, then immediately dropped) must not reset the
    // counter, or a failing server would be hammered at a steady 1 Hz.
    if (openedAt >= 0 && performance.now() - openedAt > RECONNECT_MAX_MS) {
      reconnectAttempts = 0;
    }
    const attempt = ++reconnectAttempts;
    setStatus(`disconnected - reconnecting (attempt ${attempt})`);
    // Jitter the delay between half and full so clients that lost the server
    // at the same instant do not all redial on the same 1s/2s/4s/... schedule.
    const base = Math.min(RECONNECT_BASE_MS * 2 ** (attempt - 1), RECONNECT_MAX_MS);
    const delay = base * (0.5 + Math.random() / 2);
    reconnectTimer = setTimeout(connect, delay);
  });

  socket.addEventListener("error", () => {
    // Browsers always deliver a close event after an error, and the close
    // handler owns the reconnect schedule; this only reflects the state.
    if (socket === ws) setStatus("connection error");
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
