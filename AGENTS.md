# AGENTS.md — CS3103 Assignment 4: LiveRecall

Guidance for AI coding agents working in this repository. Source of truth: the course
assignment writeup (`miniProject_Assignment4_LiveRecall_System.pdf`) and the starter docs
(`START_HERE.md`, `PROTOCOL_EXAMPLES.md`, `PUBLIC_TESTS.md`, `NETWORKING_PRIMER.md`).

## What this project is

LiveRecall distributes a live, revision-based caption stream over WebSockets:
`caption_source.py` → **LiveRecall server** (`solution_server.py`, ours to implement) →
many browser clients (`client/`). Clients display live captions, keep a short recoverable
history ("What did I miss?"), and reconnect/resume without duplicates. This is a
**networking assignment** — the graded challenges are revision-state correctness, bounded
memory, slow-client isolation, and semantic backpressure under overload. No speech
recognition is required for the mandatory work.

Deadline: 04 Nov 2026. Demo/viva: 06 Nov 2026. Graded on Ubuntu with hidden workloads.

## Hard constraints — never break these

- **Entry point**: the evaluator runs `python solution_server.py --port <port>`. The server
  must accept `--port`, start without interactive input, and be ready within 10 seconds.
  Binding 0.0.0.0 is recommended; clients connect to `ws://127.0.0.1:<port>`.
- **Required repo layout**: `solution_server.py`, `requirements.txt`, `client/`,
  `README.md`, `AI_USAGE.md`, `CONTRIBUTIONS.md`, `report.pdf` at the repo root. Do not
  rename or move these.
- **No external services**: `solution_server.py` must not depend on cloud APIs, Internet
  services, or external databases. All its Python deps go in `requirements.txt`
  (currently only `websockets`). Optional ASR-bonus deps (`bonus_asr/`) must stay out of
  the mandatory path.
- **Do not modify the instructor test harness**: `caption_source.py`, `load_clients.py`,
  `run_demo.py`, `analyse_results.py`, `monitor_server.py`, `submission_check.py`, and the
  measurement logic they contain. Do not tune to assumed hidden-test parameters.
- **Never commit** `venv/` or generated artifacts (see `.gitignore`).

## Protocol compatibility interface (Section 4 of the PDF)

The protocol may be extended, but these fields and meanings must stay exactly compatible:

- Source → server: `SOURCE_HELLO {session:"L1"}`, then `CAPTION_UPDATE` with
  `session`, `segment` (positive int, monotonically increasing from 1), `revision`
  (positive int, increases per newer version of the same segment), `source_ts` (Unix
  seconds), `final` (bool), `text`. `SOURCE_DONE` is test-control and may be ignored.
  **Unrecognized extra JSON fields must be ignored, not rejected.**
- Client → server: `CLIENT_HELLO` with `client_id`, `session`, `last_segment`,
  `last_revision` (both 0 for a fresh client), `history_seconds` (≤ 60).
- Server → client: a `HISTORY {items:[...CAPTION_UPDATE...]}` reply to every
  `CLIENT_HELLO` — items ordered **oldest to newest**, excluding finalized captions the
  client already reported via `last_segment`/`last_revision` — then live `CAPTION_UPDATE`s.

See `PROTOCOL_EXAMPLES.md` for worked message exchanges.

## Invariants the graders test (keep these true at all times)

1. A newer revision of a segment replaces the older one; an **older revision must never
   overwrite a newer one**, on server or client.
2. `final=true` is durable state: once a segment is final, no provisional may overwrite
   it, and its final caption must be delivered to every client exactly once (no duplicate
   finals after reconnect/recovery).
3. **Bounded everything**: server history is a bounded window (enough for 60 s of
   finalized captions, somewhat larger is fine); per-client queues are bounded. Unbounded
   queues/history are treated as correctness violations even if delivery is eventual.
4. **Overload semantics**: under backpressure, obsolete provisional revisions may be
   coalesced/dropped per client, but the final caption of every completed segment and the
   newest revision of the active segment must be preserved. The policy must trigger on
   actual lag and return to full-update delivery when the client catches up — "drop all
   provisionals always" fails the Normal Update Coverage check.
5. Slow clients must not delay or grow memory for normal clients; after an impairment
   ends, clients should converge quickly to the newest useful state rather than draining
   a stale backlog.
6. Final Caption Completeness below 99% caps the hidden-performance grade at 15/30
   (below 95%: 5/30). Correctness beats throughput — never trade completeness for speed.

## Dev environment and testing

- Local dev (macOS is fine for logic work): `python3 -m venv venv`,
  `./venv/bin/pip install -r requirements.txt`,
  `./venv/bin/python solution_server.py --port 8765`.
- Sanity check: `python submission_check.py` (verifies required files and startup).
- Server regressions (from the repository root):
  `./venv/bin/python -m unittest discover -s tests -p 'test_*.py'`.
- Client regressions: `node --test tests/*.cjs`.
- Public tests (run each ≥ 3 times, report the median):
  - T1: `python run_demo.py --clients 20 --duration 20 --runs 3`
  - T2: `python run_demo.py --clients 100 --duration 40 --runs 3`
  - T3: `python run_demo.py --clients 100 --duration 60 --runs 3 --slow-percent 10 --slow-ms 500`
  - T4: `python run_demo.py --clients 100 --duration 60 --runs 3 --disconnect-percent 20 --disconnect-at 15 --disconnect-seconds 5`
  - T5: tc/netem impairments via `network_profiles/*.sh` — **Ubuntu only** (grading
    environment; use a Multipass VM per `UBUNTU_SETUP.md`, server and clients in the same
    VM, impairment on loopback, `reset.sh` afterwards).
- Metrics to watch: P50/P95/P99 Caption Age, Final Caption Completeness, Fresh Caption
  Ratio / Fresh Final Caption Ratio (500 ms public threshold), Normal Update Coverage,
  recovery time. `PUBLIC_TESTS.md` explains how to interpret them.

## Workflow

- `main` is protected: no force pushes; all changes land via pull requests
  (`gh pr create`). Work on feature branches.
- Project todos are tracked as GitHub issues under milestones Week 1–3; reference the
  issue number in PRs. Labels: server, client, protocol, performance, experiments, docs,
  demo-viva.
- Git history is viva evidence for individual contributions — keep commits attributable
  and scoped; don't squash away who did what.

## AI-usage obligations (affects how you work here)

Every member must be able to explain and modify any code in a viva, and AI-generated code
that merely reproduces baseline functionality earns no credit on its own. Therefore:
prefer small, explainable changes over clever opaque ones; leave the "why" of
non-obvious networking decisions (queue bounds, coalescing rules, timeouts) discoverable
in code structure or the report. Notable AI suggestions — especially ones that were wrong
or needed modification — should be recorded in `AI_USAGE.md` while fresh; at least one
such critical evaluation is a compulsory deliverable.
