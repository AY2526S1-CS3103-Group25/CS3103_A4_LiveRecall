const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { test } = require("node:test");
const vm = require("node:vm");

// Run the actual client with a small DOM/socket adapter and a controlled clock.
function client() {
  function element() {
    return {
      children: [], dataset: {}, textContent: "", hidden: false, style: {},
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
  const sockets = [];
  const storage = {};
  // Fake timers on the controlled clock so reconnect scheduling is testable.
  let timerSeq = 0;
  const timers = new Map();
  class WebSocket {
    constructor() {
      socket = this;
      sockets.push(this);
      this.listeners = {};
      this.sent = [];
    }
    addEventListener(type, listener) { this.listeners[type] = listener; }
    send(data) { this.sent.push(JSON.parse(data)); }
    open() { this.listeners.open?.(); }
    close() { this.listeners.close?.(); }
    error() { this.listeners.error?.(); }
  }
  vm.runInNewContext(readFileSync(require.resolve("../client/app.js"), "utf8"), {
    document: {
      getElementById: id => elements[id],
      createElement: element,
      querySelectorAll: () => []
    },
    performance: { now: () => now },
    setInterval: (callback, delay) => { cleanup = callback; interval = delay; },
    setTimeout: (callback, delay) => {
      timers.set(++timerSeq, { at: now + delay, cb: callback });
      return timerSeq;
    },
    clearTimeout: id => timers.delete(id),
    sessionStorage: {
      getItem: key => (key in storage ? storage[key] : null),
      setItem: (key, value) => { storage[key] = String(value); }
    },
    WebSocket,
    console
  });
  assert.equal(interval, 5000);
  return {
    empty: elements["live-empty"],
    status: elements["status"],
    sockets,
    rows: () => elements.live.children,
    raw: data => socket.listeners.message({ data }),
    send(segment, revision, final = false, text = "caption", extra = {}) {
      this.raw(JSON.stringify({ type: "CAPTION_UPDATE", segment, revision, final, text, ...extra }));
    },
    open: () => socket.open(),
    close: () => socket.close(),
    advance(ms) {
      // Walk the clock to the next event (cleanup tick or timer deadline),
      // fire what is due, and repeat until the target time is reached.
      const target = now + ms;
      while (now < target) {
        let next = target;
        const tick = (Math.floor(now / interval) + 1) * interval;
        if (tick < next) next = tick;
        for (const timer of timers.values()) {
          if (timer.at > now && timer.at < next) next = timer.at;
        }
        now = next;
        for (const [id, timer] of [...timers]) {
          if (timer.at <= now) {
            timers.delete(id);
            timer.cb();
          }
        }
        if (now % interval === 0) cleanup();
      }
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

test("reconnect uses capped exponential backoff and a successful open resets it", () => {
  const c = client();
  c.close();
  assert.match(c.status.textContent, /disconnected - reconnecting \(attempt 1\)/);
  c.advance(999);
  assert.equal(c.sockets.length, 1);
  c.advance(1);
  assert.equal(c.sockets.length, 2);
  assert.equal(c.status.textContent, "connecting");

  // Never opened, so each close escalates: 2 s, then 4 s, then 8 s.
  c.close();
  assert.match(c.status.textContent, /reconnecting \(attempt 2\)/);
  c.advance(1999);
  assert.equal(c.sockets.length, 2);
  c.advance(1);
  assert.equal(c.sockets.length, 3);
  c.close();
  assert.match(c.status.textContent, /reconnecting \(attempt 3\)/);
  c.advance(4000);
  assert.equal(c.sockets.length, 4);
  c.close();
  assert.match(c.status.textContent, /reconnecting \(attempt 4\)/);
  c.advance(8000);
  assert.equal(c.sockets.length, 5);

  // The next delay (16 s) is capped at 10 s.
  c.close();
  assert.match(c.status.textContent, /reconnecting \(attempt 5\)/);
  c.advance(9999);
  assert.equal(c.sockets.length, 5);
  c.advance(1);
  assert.equal(c.sockets.length, 6);

  // A successful open resets the counter back to attempt 1.
  c.open();
  assert.equal(c.status.textContent, "connected");
  c.close();
  assert.match(c.status.textContent, /reconnecting \(attempt 1\)/);
});

test("a late close from a replaced socket cannot schedule a duplicate reconnect", () => {
  const c = client();
  c.close(); // socket 1 -> attempt 1, timer at +1 s
  c.advance(1000); // the timer dials socket 2
  assert.equal(c.sockets.length, 2);
  c.sockets[0].close(); // stale close event from the replaced socket 1
  c.advance(10000);
  assert.equal(c.sockets.length, 2); // it must not have scheduled a dial
  // The live socket's own close still schedules normally.
  c.close();
  assert.match(c.status.textContent, /reconnecting \(attempt 2\)/);
});

test("hello resumes from the contiguous final run, not a high-water mark", () => {
  const c = client();
  c.open();
  const fresh = c.sockets[0].sent[0];
  assert.equal(fresh.type, "CLIENT_HELLO");
  assert.equal(fresh.last_segment, 0);
  assert.equal(fresh.last_revision, 0);
  assert.equal(fresh.history_seconds, 30);

  c.send(1, 3, true);
  c.send(2, 1, false); // provisionals do not count toward the resume run
  c.send(3, 2, true); // gap at 2: 3 is parked, not reported
  c.close();
  c.advance(1000);
  c.open();
  const hello = c.sockets[1].sent[0];
  assert.deepEqual([hello.last_segment, hello.last_revision], [1, 3]);

  c.send(2, 4, true); // fills the gap: the run is now 1..3
  c.close();
  c.advance(1000);
  c.open();
  const resumed = c.sockets[2].sent[0];
  assert.deepEqual([resumed.last_segment, resumed.last_revision], [3, 2]);
});

test("recurring gaps stay bounded: parked finals expire on their own age", () => {
  const c = client();
  c.open();
  // Finalize every other segment, one second apart: each new final parks
  // over a new persistent gap that no later delivery will ever fill.
  for (let segment = 1; segment <= 299; segment += 2) {
    c.send(segment, 1, true);
    c.advance(1000);
  }
  // Read the resume cursor by reconnecting and inspecting the hello.
  const cursorLag = latest => {
    c.close();
    c.advance(1000);
    c.open();
    return latest - c.sockets[c.sockets.length - 1].sent[0].last_segment;
  };
  // Steady state: parked entries drop on their own retention age, so the
  // cursor lags by about one retention window of stream instead of sticking
  // at segment 1 or accumulating an ever-growing backlog.
  const lag1 = cursorLag(299);
  assert.ok(lag1 <= 150, `resume cursor lagged ${lag1} segments after 299 finals`);
  // The lag must not keep growing as gaps keep recurring (the old re-aging
  // bug grew the backlog by roughly one entry per second).
  for (let segment = 301; segment <= 399; segment += 2) {
    c.send(segment, 1, true);
    c.advance(1000);
  }
  const lag2 = cursorLag(399);
  assert.ok(lag2 <= lag1 + 10, `cursor lag grew from ${lag1} to ${lag2}`);
  // The live window shows only recent rows, not an accumulated backlog.
  assert.ok(c.rows().length <= 75, `live window holds ${c.rows().length} rows`);
});
