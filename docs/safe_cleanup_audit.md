# Safe cleanup audit — 2 November 2026

Baseline commit: `cbbd30e3c181c2bb3c89fd0baf2320777f150910`. Tests ran on macOS, Python 3.11.9 and websockets 17.3, with the repository `venv/bin` first on `PATH`. The system Python lacked websockets. Socket-binding tests ran with sandbox network permission; the initial sandboxed verifier attempt could not bind and was rerun successfully.

Before any edits and again after cleanup, `python submission_check.py`, `python -m unittest discover -s tests -p 'test_*.py'` (54 tests), `node --test tests/*.cjs` (14 tests), and both documented `python tests/probe_reader_recovery.py 4` / `10` probes passed. Both probe runs on both versions delivered all 240 finals to each client, all 720 revisions to the normal client, and all revisions in the final five seconds, with no duplicate or stale errors. The existing ignored recovery result was backed up and restored after testing.

The requested public tests were run once per version, serially, using `python run_demo.py` and the following arguments (with distinct `/tmp` output stems):

- T1: `--clients 20 --duration 20 --runs 1`
- T2: `--clients 100 --duration 40 --runs 1`
- T3: `--clients 100 --duration 60 --runs 1 --slow-percent 10 --slow-ms 500`
- T4: `--clients 100 --duration 60 --runs 1 --disconnect-percent 20 --disconnect-at 15 --disconnect-seconds 5`

Each cell below is before → after. Age values are milliseconds; freshness uses the 500 ms threshold.

| Test | Final completeness | Reported update coverage | Fresh captions | Fresh finals | Age P50 / P95 / P99 (ms) | Duplicate / stale |
| --- | --- | --- | --- | --- | --- | --- |
| T1 | 100% → 100% | 100% → 100% | 100% → 100% | 100% → 100% | 6.09 / 15.47 / 19.45 → 6.58 / 15.09 / 19.79 | 0 / 0 → 0 / 0 |
| T2 | 100% → 100% | 100% → 100% | 100% → 100% | 100% → 100% | 20.28 / 38.44 / 45.13 → 20.00 / 39.35 / 45.69 | 0 / 0 → 0 / 0 |
| T3 | 100% → 100% | 93.90% → 93.92% | 99.81% → 99.79% | 99.61% → 99.54% | 19.45 / 39.48 / 185.03 → 20.14 / 39.73 / 186.37 | 0 / 0 → 0 / 0 |
| T4 | 100% → 100% | 98.33% → 98.33% | 100% → 100% | 98.42% → 98.42% | 20.24 / 37.84 / 42.95 → 20.49 / 37.65 / 43.06 | 0 / 0 → 0 / 0 |

The harness labels its aggregate live-revision ratio “Normal Update Coverage”; T3's value includes slow clients. The 90 normal clients each received all 228 revisions in both runs (20,520/20,520, 100%). T4 recovered 120 history items in each run; HISTORY response P95 was 3.95 → 3.86 ms. All public runs had zero connection errors.

To assess latency variation, identical copies of the instructor harness in `/tmp` ran the original and cleaned servers alternately with the same interpreter. Three additional T2 runs per version gave original versus cleaned median P95 of 39.14 versus 39.12 ms (ranges 38.63–40.86 versus 38.42–39.20) and median P99 of 45.34 versus 45.98 ms (ranges 44.08–45.38 versus 44.61–46.58). All six retained 100% completeness and zero duplicate/stale or connection errors.

For T3, the initial run plus two further alternating runs per version gave the following medians and ranges in milliseconds. Role-specific P99 values were calculated from accepted live caption rows using the harness percentile formula. All six runs retained 100% completeness, zero duplicate/stale and connection errors, and all normal-client revisions.

| T3 latency | Original median (range) | Cleaned median (range) |
| --- | --- | --- |
| Overall P95 | 39.94 (39.48–40.29) | 40.36 (39.73–40.51) |
| Overall P99 | 185.11 (185.03–189.36) | 185.87 (185.41–186.37) |
| Normal P95 / P99 | 37.95 (37.26–38.16) / 43.99 (43.58–44.25) | 38.22 (37.84–38.48) / 43.83 (43.78–44.58) |
| Slow P95 / P99 | 347.78 (322.71–592.82) / 1102.72 (1064.68–1353.29) | 342.28 (328.18–521.92) / 1099.04 (1072.37–1116.18) |

T3 median fresh-caption / fresh-final ratios were 99.81% / 99.61% original and 99.83% / 99.63% cleaned; median reported aggregate update coverage was 93.90% and 93.89%, respectively. The slow tail varies considerably across runs. These limited local samples do not prove exact equivalence or predict Ubuntu grading. T5 network profiles require Ubuntu and were not run on macOS. Raw logs, CSVs and JSON summaries are under `/tmp/liverecall_baseline_*`, `/tmp/liverecall_post_*`, and `/tmp/liverecall_control_*` and are temporary.

The only mandatory dependency is `websockets`; existing Python-version markers were retained. Existing revision, queue, history and disconnect cleanup comments and logic were reviewed; no hot-path debug logging or dead code requiring removal was found. No client change was needed. `git diff HEAD` confirms the protected instructor harness and network profiles are unchanged. No common virtual environment, cache, result, log or model-weight artifacts are tracked. The submission verifier passed, but `report.pdf` is absent even though the repository's `AGENTS.md` requires it; it must be supplied separately before submission.

Original / cleaned server SHA-256: `f864af18a4becc21bffed003e2f7701c5f3d2e42923535d10cde807af7499b7c` / `60db3d84ffd319f1dbfab751d10e603a82ffa56ef4bc7a591c713afafcd9f2ba`.

## Follow-up: documentation reduction

The follow-up began after the first cleanup, from 1060 lines in `solution_server.py`, 400 in `client/app.js`, and 39 in `client/index.html` (1499 total). The final candidate has 998, 373 and 39 lines respectively (1410 total, 89 fewer). It condenses repeated explanations while retaining the revision, reset ambiguity, bounded-state, recovery and duplicate-suppression rationale. The Python ASTs match after removing docstrings, and all non-comment JavaScript lines match. The actual docstring metadata changed; executable statements did not. No executable deduplication was judged worthwhile under the behavior guardrails.

An incremental test was initially invoked with system Python by mistake and failed to import websockets. The server file was immediately restored from the saved first-pass snapshot; the suite passed using `./venv/bin/python`, after which the documentation edit was reapplied. On a subsequent T1 run, completeness remained 100% and duplicates/stale remained zero, but P95/P99 rose from 15.09/19.79 to 15.86/20.66 ms. The candidate was immediately rolled back and the first-pass files restored byte-for-byte. Restored-code T1 reported 15.18/19.10 ms. Three predeclared alternating T1 pairs showed base P95 14.33–15.84 and P99 16.61–24.15 ms versus candidate P95 12.46–14.55 and P99 15.31–18.64 ms, with mixed paired differences and all hard correctness gates passing. At the user's clarification, the candidate was reapplied using controlled timing variation rather than treating every individual latency increase as a causal regression.

The complete verifier, 54 Python tests, 14 Node tests, T1–T4 and the two 4/10-second reader recovery probes then passed on the final candidate. T3 normal clients received every revision (20,520/20,520); each reader-recovery profile received all 240 finals, and the normal reader all 720 updates, with no duplicate/stale errors. The initial pre-first-cleanup results are in the earlier table; this table compares the immediately pre-follow-up first-pass results to the final candidate. Ages are milliseconds and freshness uses 500 ms.

| Test | Final completeness | Reported aggregate update coverage | Fresh captions | Fresh finals | Age P50 / P95 / P99 (ms) | Duplicate / stale |
| --- | --- | --- | --- | --- | --- | --- |
| T1 | 100% → 100% | 100% → 100% | 100% → 100% | 100% → 100% | 6.58 / 15.09 / 19.79 → 6.35 / 15.57 / 21.00 | 0 / 0 → 0 / 0 |
| T2 | 100% → 100% | 100% → 100% | 100% → 100% | 100% → 100% | 20.00 / 39.35 / 45.69 → 20.41 / 38.81 / 45.16 | 0 / 0 → 0 / 0 |
| T3 | 100% → 100% | 93.92% → 93.93% | 99.79% → 99.74% | 99.54% → 99.47% | 20.14 / 39.73 / 186.37 → 20.84 / 38.81 / 187.00 | 0 / 0 → 0 / 0 |
| T4 | 100% → 100% | 98.33% → 98.33% | 100% → 100% | 98.42% → 98.42% | 20.49 / 37.65 / 43.06 → 20.41 / 38.20 / 44.76 | 0 / 0 → 0 / 0 |

T3 normal P95/P99 was 37.84/43.83 → 36.86/42.09 ms; slow P95/P99 was 521.92/1116.18 → 603.72/1351.68 ms. The single-run slow fresh-caption ratio was 94.85% → 93.85%. T4 recovered 120 history items per run; recovery P95 was 3.86 → 4.58 ms.

The final T3 and T4 results prompted fixed, alternating three-pair controls against the saved first-pass server. All 12 runs retained 100% completeness, zero duplicate/stale and connection errors, and all normal T3 clients received every revision. Values below are the three runs in order; age and recovery values are milliseconds.

| Metric | First-pass server | Reduced-documentation server |
| --- | --- | --- |
| T3 overall P95 | 39.90, 39.71, 38.88 | 39.15, 39.24, 38.83 |
| T3 overall P99 | 182.66, 184.98, 181.36 | 179.57, 183.50, 186.26 |
| T3 normal P95 | 38.07, 37.67, 36.83 | 37.46, 37.53, 36.60 |
| T3 slow P95 | 315.68, 347.14, 304.85 | 317.28, 346.27, 599.96 |
| T3 slow P99 | 1059.65, 1099.15, 878.26 | 1066.74, 1101.39, 1347.97 |
| T3 fresh-final % | 99.66, 99.61, 99.71 | 99.68, 99.61, 99.50 |
| T4 overall P95 | 38.38, 38.02, 37.76 | 37.91, 38.02, 38.08 |
| T4 overall P99 | 43.82, 44.21, 43.87 | 43.33, 43.71, 43.56 |
| T4 recovery P95 | 6.47, 9.62, 8.32 | 9.72, 8.72, 8.96 |

T3's third candidate slow-reader tail was high; an earlier first-pass original-server T3 control reached 592.82/1353.29 ms slow P95/P99. Overall T3 and T4 age differences were mixed across pairs. All T4 controls had 98.33% reported update coverage, 100% caption freshness and 98.42% final freshness. These samples show substantial slow-reader variability but do not prove equality or rule out a regression under other conditions.

All runs are local macOS measurements and cannot prove hidden-workload or Ubuntu/netem behavior. The first-pass audit's absence of `report.pdf` remains an outstanding submission requirement. Follow-up raw output and the reviewable candidate patch are in `/tmp/liverecall_secondpass_*` and are temporary.

## Further pass: shared resume-head advancement

Before this pass, `solution_server.py`, `client/app.js`, and `client/index.html` had 998, 373 and 39 lines respectively (1410 total). They now have 998, 367 and 39 (1404 total, six fewer). Relative to the pre-follow-up 1499-line snapshot, the target files are 95 lines shorter. This pass extracts the same resume-head assignment, pending-final deletion, contiguous fold, and gap-age update into one client helper. The first-final anchor path and operation order remain unchanged. A 19-line regression was added to `tests/client.test.cjs`, so the full repository line count increased even though the requested target files became shorter.

The new regression verifies that an expired gap advances over parked finals 4 and 5 but stops before a second gap at 6. It passed against both the pre-pass client and the refactored client. The verifier, 54 Python tests, 15 Node tests, T1–T4, and both documented reader-recovery probes passed after the edit. All public runs retained 100% final completeness, zero duplicate/stale and zero connection errors. Each probe delivered all 240 finals per client, all 720 normal-client revisions and full tail coverage without duplicate/stale errors.

The table compares the immediately pre-pass measurements with this pass's measurements, one run each. The original pre-first-cleanup baseline is in the first table of this report. Ages are milliseconds and freshness uses 500 ms.

| Test | Final completeness | Reported aggregate update coverage | Fresh captions | Fresh finals | Age P50 / P95 / P99 (ms) | Duplicate / stale |
| --- | --- | --- | --- | --- | --- | --- |
| T1 | 100% → 100% | 100% → 100% | 100% → 100% | 100% → 100% | 6.35 / 15.57 / 21.00 → 6.48 / 15.21 / 18.35 | 0 / 0 → 0 / 0 |
| T2 | 100% → 100% | 100% → 100% | 100% → 100% | 100% → 100% | 20.41 / 38.81 / 45.16 → 20.12 / 38.86 / 43.96 | 0 / 0 → 0 / 0 |
| T3 | 100% → 100% | 93.93% → 93.89% | 99.74% → 99.86% | 99.47% → 99.68% | 20.84 / 38.81 / 187.00 → 19.76 / 38.97 / 184.52 | 0 / 0 → 0 / 0 |
| T4 | 100% → 100% | 98.33% → 98.33% | 100% → 100% | 98.42% → 98.68% | 20.41 / 38.20 / 44.76 → 20.01 / 38.19 / 44.03 | 0 / 0 → 0 / 0 |

T3's normal clients received 20,520/20,520 revisions in both runs. Normal P95/P99 was 36.86/42.09 → 36.79/42.62 ms; slow P95/P99 was 603.72/1351.68 → 319.28/1072.19 ms. T4 recovered 120 history items in each run; recovery P95 was 4.58 → 5.98 ms. Run-to-run variation is visible; no exact performance-equivalence or hidden-workload guarantee is inferred.

The server was byte-identical to its pre-pass snapshot (SHA-256 `0088850e6857ae5c9c6b1284a8cc658a40150a9137c8f92a7e4090126c449fca`) and the protected instructor harness matched HEAD. `run_demo.py` launches Python `load_clients.py`, not the browser client, so the changed JavaScript was not on the public benchmark path. Raw logs and results are temporary under `/tmp/liverecall_thirdpass_*`. Ubuntu T5 was not run, and `report.pdf` remains absent.

## Pull request readiness: exact-size HISTORY failure

A later readiness run passed `submission_check.py` but the 54-test Python suite had one error: `test_large_history_fits_real_receiver_then_continues_live` received a WebSocket 1009 close at the 1 MiB receive limit. The 15 Node tests passed separately. Earlier full-suite passes above remain valid observations; they do not erase this failure.

The focused Python test then passed 15/15 repetitions on the original HEAD server and 15/15 on the cleanup candidate. An isolated real-WebSocket probe constructed a valid HISTORY batch of exactly 1,048,576 UTF-8 bytes by padding one caption. Both servers deterministically closed the receiver with the same error: `frame with 1 bytes after reading 1048576 bytes exceeds limit of 1048576 bytes`. Normalized server executable ASTs match after accounting for same-value history constants, socket annotations, and docstrings. This establishes that the observed exact-boundary problem exists in the original server as well as the cleanup candidate; the cleanup does not fix it. The probe and logs were saved temporarily under `/tmp/liverecall_exact_*` and `/tmp/liverecall_exact_limit_probe.py`.

At that point this was a correctness blocker, so the cleanup pull request remained a draft pending a separate fix. The earlier T1–T4 and recovery-probe results did not exercise this constructed boundary. The section below records the subsequent fix and verification.

## Exact-limit framing fix

After the readiness failure above, the exact-size condition was fixed in the common client sender. When the UTF-8 JSON payload is exactly 1,048,576 bytes, the sender uses the existing unfragmented send path; all other messages retain the original fragmentation decision. The existing checkpoint predicate remains in its original evaluation order. Queue bounds, recovery, retention, protocol fields and oversize policy are unchanged.

Two new real-WebSocket regressions construct exact-size UTF-8 JSON and force the modern fragmentation decision. Before the fix, both HISTORY and live CAPTION_UPDATE tests failed with the same WebSocket 1009 error; after the fix, both passed. The live test covers deflate and no compression. The HISTORY test deliberately blocks one sender, checks its pending count/bytes remain bounded, and confirms a normal client receives HISTORY and the next final before the blocked sender resumes and receives both. This is a controlled blocked-sender test, not a simulation of network congestion. A third regression checks HISTORY-wrapper sizes one byte below and above the limit and confirms that a standalone caption one byte above the limit retains the existing rejection behavior. The fragmentation-specific tests skip on receiver library versions below 17.3, which use the existing compatibility path. All three new tests and the original large-history test passed 10 consecutive repetitions after the fix.

The latest pre-fix full Python suite had 53 of 54 tests pass, with the large-HISTORY case failing at the boundary; 15 Node tests passed. After the fix, the verifier, 57 Python tests, 15 Node tests, T1–T4 and the 4/10-second real-reader recovery probes passed. The following compares the immediately pre-fix public run with the fixed run, one run per scenario. Ages are milliseconds; freshness uses 500 ms.

| Test | Final completeness | Reported aggregate update coverage | Fresh captions | Fresh finals | Age P50 / P95 / P99 (ms) | Duplicate / stale |
| --- | --- | --- | --- | --- | --- | --- |
| T1 | 100% → 100% | 100% → 100% | 100% → 100% | 100% → 100% | 6.48 / 15.21 / 18.35 → 6.86 / 15.21 / 18.10 | 0 / 0 → 0 / 0 |
| T2 | 100% → 100% | 100% → 100% | 100% → 100% | 100% → 100% | 20.12 / 38.86 / 43.96 → 20.91 / 38.79 / 45.28 | 0 / 0 → 0 / 0 |
| T3 | 100% → 100% | 93.89% → 93.90% | 99.86% → 99.81% | 99.68% → 99.61% | 19.76 / 38.97 / 184.52 → 20.40 / 39.22 / 184.77 | 0 / 0 → 0 / 0 |
| T4 | 100% → 100% | 98.33% → 98.33% | 100% → 100% | 98.68% → 98.42% | 20.01 / 38.19 / 44.03 → 19.80 / 37.95 / 43.97 | 0 / 0 → 0 / 0 |

T3 normal clients received all 20,520 expected revisions in both runs; slow-client P95 was 319.28 → 344.56 ms. T4 recovered 120 HISTORY items in each run; recovery P95 was 5.98 → 3.52 ms. The paired controls in the prior section establish substantial run-to-run variation; these single runs do not prove identical performance or predict hidden workloads. The new exception handles the demonstrated configured 1 MiB boundary, not every possible client-specific limit or every case where normalization could produce a larger outgoing payload. Ubuntu T5 remains unrun, and `report.pdf` remains absent.
