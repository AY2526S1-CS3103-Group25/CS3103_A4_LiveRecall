const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { test } = require("node:test");
const vm = require("node:vm");

// Run the actual client with a small DOM/socket adapter and a controlled clock.
function client() {
  function element() {
    return {
      children: [], dataset: {}, textContent: "", hidden: false,
      insertBefore(row, next) {
        const index = next ? this.children.indexOf(next) : this.children.length;
        this.children.splice(index, 0, row);
        row.remove = () => this.children.splice(this.children.indexOf(row), 1);
      }
    };
  }
  const elements = Object.fromEntries(
    ["status", "live", "live-empty", "recall"].map(id => [id, element()])
  );
  elements["live-empty"].textContent = "Waiting for captions.";
  let now = 0;
  let cleanup;
  let interval;
  let socket;
  class WebSocket {
    constructor() { socket = this; this.listeners = {}; }
    addEventListener(type, listener) { this.listeners[type] = listener; }
  }
  vm.runInNewContext(readFileSync(require.resolve("../client/app.js"), "utf8"), {
    document: {
      getElementById: id => elements[id],
      createElement: element,
      querySelectorAll: () => []
    },
    performance: { now: () => now },
    setInterval: (callback, delay) => { cleanup = callback; interval = delay; },
    WebSocket,
    console
  });
  assert.equal(interval, 5000);
  return {
    empty: elements["live-empty"],
    rows: () => elements.live.children,
    raw: data => socket.listeners.message({ data }),
    send(segment, revision, final = false, text = "caption", extra = {}) {
      this.raw(JSON.stringify({ type: "CAPTION_UPDATE", segment, revision, final, text, ...extra }));
    },
    advance(ms) {
      const target = now + ms;
      for (let tick = Math.floor(now / interval) * interval + interval; tick <= target; tick += interval) {
        now = tick;
        cleanup();
      }
      now = target;
    }
  };
}

test("newest provisional becomes stale only after accepted-update inactivity", () => {
  const c = client();
  c.send(1, 2, false, "unfinished");
  c.advance(65000);
  c.send(1, 1); // Older updates must not postpone staleness.
  c.send(1, 2);
  assert.doesNotMatch(c.rows()[0].textContent, /STALE/);
  c.advance(5000);
  assert.match(c.rows()[0].textContent, /PROVISIONAL — STALE.*unfinished.*No recent updates/);
  c.send(1, 1, true);
  c.send(1, 2);
  c.send(1, 9, "true");
  assert.match(c.rows()[0].textContent, /STALE/);
  c.advance(140000);
  assert.equal(c.rows().length, 1);
  c.send(1, 3, false, "resumed");
  assert.doesNotMatch(c.rows()[0].textContent, /STALE|No recent updates/);
  c.advance(70000);
  assert.match(c.rows()[0].textContent, /STALE/);
  c.send(1, 3, true, "complete");
  assert.match(c.rows()[0].textContent, /^\[FINAL\].*complete$/);
});

test("malformed messages cannot corrupt state or stop subsequent valid updates", () => {
  const c = client();
  for (const raw of ["{", "null", "[]", "42", '"caption"', "true"]) c.raw(raw);
  c.send(1, 2, false, "accepted");
  const before = c.rows()[0].textContent;
  const invalid = [undefined, null, "3", 0, -1, 1.5, Number.MAX_SAFE_INTEGER + 1];
  for (const value of invalid) {
    c.send(value, 3);
    c.send(1, value);
  }
  for (const value of [undefined, null, "false", "true", 0, 1, {}, []]) {
    c.send(1, 3, false, "bad", { final: value });
  }
  assert.equal(c.rows().length, 1);
  assert.equal(c.rows()[0].textContent, before);
  c.send(1, 1, true, "older final");
  assert.equal(c.rows()[0].textContent, before);
  c.send(1, 3, false, "<img src=x onerror=alert(1)>", { unknown: { ignored: true } });
  assert.match(c.rows()[0].textContent, /rev 3: <img/);
});

test("accepted updates reset inactivity and cleanup handles an off-tick deadline", () => {
  const c = client();
  c.send(1, 1);
  c.advance(22000);
  c.send(1, 2);
  c.advance(70000); // Last cleanup was at 90 s: only 68 s since revision 2.
  assert.doesNotMatch(c.rows()[0].textContent, /STALE/);
  c.advance(3000); // Next cleanup is at 95 s: 73 s since revision 2.
  assert.match(c.rows()[0].textContent, /STALE/);
});

test("revision replacement, final promotion and final protection reuse the row", () => {
  const c = client();
  c.send(1, 1);
  const row = c.rows()[0];
  c.send(1, 2, false, "newer");
  c.send(1, 1, true, "old final");
  c.send(1, 2, false, "duplicate");
  assert.equal(c.rows()[0], row);
  assert.match(row.textContent, /newer$/);
  c.send(1, 2, true, "final");
  for (const final of [false, true]) c.send(1, 3, final, "overwrite");
  assert.equal(c.rows().length, 1);
  assert.match(row.textContent, /^\[FINAL\].*final$/);
});

test("older provisionals expire and late finals return in segment order", () => {
  const c = client();
  c.send(2, 2);
  c.send(1, 2);
  c.send(3, 1);
  c.advance(70000);
  assert.deepEqual(c.rows().map(row => row.dataset.segment), ["3"]);
  c.send(1, 1, true); // Pruned provisional still rejects an older final.
  c.send(1, 2); // Equal provisional is still rejected after pruning.
  assert.equal(c.rows().length, 1);
  c.send(2, 3, false, "restored revision");
  c.send(1, 2, true, "late final");
  assert.deepEqual(c.rows().map(row => row.dataset.segment), ["1", "2", "3"]);
  assert.match(c.rows()[0].textContent, /^\[FINAL\].*late final$/);
  c.advance(70000);
  assert.deepEqual(c.rows().map(row => row.dataset.segment), ["3"]);
  c.send(1, 99, true); // Final guard lasts one extra retention window.
  assert.equal(c.rows().length, 1);
  c.advance(70000);
  c.send(1, 99, true); // Protection is intentionally temporary.
  assert.deepEqual(c.rows().map(row => row.dataset.segment), ["1", "3"]);
});

test("empty status distinguishes initial waiting from an expired live window", () => {
  const c = client();
  assert.equal(c.empty.hidden, false);
  assert.equal(c.empty.textContent, "Waiting for captions.");
  c.advance(75000);
  assert.equal(c.empty.textContent, "Waiting for captions.");
  c.send(1, 1, true);
  assert.equal(c.empty.hidden, true);
  c.advance(65000);
  assert.equal(c.empty.hidden, true);
  c.advance(5000);
  assert.equal(c.rows().length, 0);
  assert.equal(c.empty.hidden, false);
  assert.equal(c.empty.textContent, "No recent captions. Older captions have left the live window.");
  c.send(2, 1);
  assert.equal(c.empty.hidden, true);
});
