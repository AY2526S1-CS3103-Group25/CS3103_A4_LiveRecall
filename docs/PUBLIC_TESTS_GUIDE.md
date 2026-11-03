# LiveRecall Public Tests (T1-T5) - Verified Ubuntu Guide

This guide accompanies the instructor's `PUBLIC_TESTS.md`. It records the team's
Ubuntu measurements, explains their meaning and gives commands to reproduce them.
The instructor's measurement scripts are unchanged.

> **Committed copy.** Raw per-run CSV/JSON files referenced below as
> `results/...` live in the team's external evidence archive, which is not
> committed (the repository ignores `results/`). The small verification
> summaries are committed next to this guide: `docs/evidence/ubuntu/`
> (`verified_medians.json`, `environment.txt`) and
> `docs/evidence/before-after-20261103/` (`manifest.json`,
> `comparison_summary.json`).

## Included evidence and environment

The `results/ubuntu/` folder contains three CSV/JSON pairs for every test (15
runs total), the supplied `environment.txt`, and regenerated
`verified_medians.json`. All 15 saved summaries matched a fresh calculation
from their raw CSVs using the supplied analyser and the recorded source totals.
Each client's exact final segment set was also checked.

The environment record identifies:

- Ubuntu 24.04.1 LTS, kernel 6.8.0-41-generic.
- Python 3.12.3 and `websockets==17.4`.
- Commit `86b2bb13b6801e0b81fc59abc5b64064f20012f6` (PR #39 merge).
- Only untracked T5 outputs in the captured Git status; no tracked changes
  are reported at capture time.

The team reports running inside VMware with the prescribed T1-T5 commands,
including `delay_loss.sh lo` for T5. The archive does not record VM CPU/RAM,
exact command transcripts, active `tc` configuration or runner logs. Keep those
with the experiment record where available. The CSVs verify delivery outcomes;
they do not independently prove which impairment was applied.

## Median results

Each cell is the median of that metric's three independent run values. Caption
Age is in milliseconds. These values are not percentiles of pooled CSVs, and a
row need not correspond to one particular run. Include the exceptions below
alongside the table; 100% median completeness is not 100% in every run.

| Test | P50 | P95 | P99 | Fresh updates | Fresh finals | Final completeness | Live coverage |
|---|---:|---:|---:|---:|---:|---:|---:|
| T1 | 6.20 | 13.03 | 23.03 | 100.00% | 100.00% | 100.00% | 100.00% |
| T2 | 13.23 | 23.55 | 27.33 | 100.00% | 100.00% | 100.00% | 100.00% |
| T3 | 15.33 | 30.46 | 163.70 | 99.79% | 99.57% | 100.00% | 93.90% |
| T4 | 9.03 | 22.41 | 26.92 | 100.00% | 98.42% | 100.00% | 98.33% |
| T5 | 311.53 | 800.02 | 1102.49 | 78.61% | 76.16% | 100.00% | 89.00% |

All 15 runs recorded zero duplicate/stale caption deliveries, invalid non-final
HISTORY items and connection errors. All clients received every expected final
except client 35 in T5 run 03, which lacked segment 49 at collection end.

## Metric definitions

| Field | Meaning in the unchanged instructor analyser |
|---|---|
| `p50_ms`, `p95_ms`, `p99_ms` | Percentiles of accepted live CAPTION_UPDATE age: client receive time minus `source_ts`. HISTORY items and browser rendering are excluded. |
| `fresh_pct` | Percentage of accepted live updates received within 500 ms. Missing updates and HISTORY are excluded from the denominator. |
| `fresh_final_pct` | Percentage of expected client-final deliveries received within 500 ms, using earliest correct live or recovered delivery. Missing finals count against this metric when expected totals are supplied. |
| `final_completeness_pct` | Unique finals received per client, including HISTORY recovery, divided by expected finals per client times client count. |
| `normal_update_coverage_pct` | Unique accepted live segment/revision deliveries divided by expected source revisions times client count. HISTORY is excluded. The field also appears during impaired tests. |
| `duplicates`, `stale` | Caption deliveries classified as duplicate or stale by the load client. Target: zero. Browser durability and display semantics need separate checks. |
| `connection_errors` | Logged connection/transport errors. Target: zero; interpret alongside impairment and completeness. |
| `normal_clients`, `slow_clients` | Cohort statistics for accepted live updates, split by the simulated slow-reader flag. In T4, the normal cohort includes reconnecting clients. |
| `recovery_p50_ms`, `recovery_p95_ms` | Connection-to-HISTORY-response time for reconnect or late join. This is not independent proof of complete recovery across multiple batches. |

The public freshness threshold is 500 ms. The handout's hidden-performance
section is capped at 15/30 below 99% final completeness and at 5/30 below 95%.
Public measurements do not guarantee results on the hidden workloads.

## T1 - Basic correctness

Twenty clients, normal network, 20 seconds. Each run generated 22 finals and
66 source revisions per client. Every client received all finals in all runs.

**Run 01 is an exception to full revision coverage:** only 1250/1320 updates
arrived (94.70%). The 70 missing deliveries were provisional revisions 1/2 of
segments 2-7 across six clients. All connections opened within about 3 ms of
each other. The logs do not establish the internal trigger for suppression.
Runs 02/03 had 100% coverage, making the median 100%; retain and investigate
the first run rather than implying that normal coverage was perfect throughout.

## T2 - Public scale

One hundred clients, normal network, 40 seconds. Every client received all
49 finals and 147 revisions in every run, with 100% freshness and coverage.
Median P95 was 23.55 ms. This supplies the normal-network comparison for
T3 and the impairment tests on this environment.

## T3 - Slow-reader isolation

One hundred clients, including ten that sleep 500 ms after each live update,
for 60 seconds. Their nominal two-message-per-second consumption is below the
default source's roughly four-update-per-second rate. Obsolete provisionals may
be suppressed under lag; finals and the newest useful active state must remain
recoverable. Full-update delivery should resume when lag clears.

All clients received all 76 finals in each run. All 90 normal clients received
all 228 source revisions with 100% freshness. Median cohort results:

| Cohort | P50 | P95 | Fresh updates |
|---|---:|---:|---:|
| Normal (90 clients) | 15.18 ms | 29.32 ms | 100.00% |
| Slow (10 clients) | 23.13 ms | 362.74 ms | 95.06% |

The slow cohort received 891, 890 and 886 updates across the runs, including
all 760 expected slow-client finals each time. This supports semantic delivery
under this workload. Normal-client P95 was 23.55, 29.32 and 30.93 ms, so there
was some latency overhead relative to T2, despite full normal update coverage.
Slow-client P95 was 362.74, 603.75 and 303.30 ms.

Report both cohorts and Fresh Final Caption Ratio. Suppression changes the
cohorts' shares of accepted updates, so aggregate P95 alone can hide slow-client
delay. This test does not include removing the slow-reader condition mid-run;
received revision coverage after removal needs a separate recovery experiment.

## T4 - Reconnect

One hundred clients, with 20% disconnecting at 15 seconds for five seconds,
for a total of 60 seconds. Every client held finals 1-76 in every run. Each
run recovered 120 finals, six per reconnecting client. Median HISTORY response
P50/P95 was 6.58/7.01 ms.

Final completeness was 100%, while Fresh Final Caption Ratio was 98.42%:
120 recovered finals exceeded 500 ms in each run. Live coverage was 98.33%
(22420/22800); its missing live deliveries comprise 260 provisionals plus
120 finals recovered through HISTORY. The coverage metric excludes HISTORY,
whereas completeness includes it. Live freshness was 100% because it excludes
those delayed recovered captions. Recovery-response timing should be read with
the recovered counts and exact per-client final sets.

## T5 - Delay, jitter and loss

The team reports applying the supplied `delay_loss.sh lo` profile to Ubuntu
loopback: `delay 120ms 30ms distribution normal loss 3%`, with server and load
clients in the same VM. Each run generated 49 finals per client.

Completeness was **100%, 100%, 99.9796%**. In run 03, client 35 lacked final
segment 49 at collection end (4899/4900 expected client-final deliveries).
Keep this shortfall visible even though the median is 100%. The logs do not
observe arrival after the deadline or establish that the final would arrive.

Initial connections spread over approximately 1.27-4.07 seconds across the
runs; HISTORY supplied 2, 9 and 2 finals respectively. Reduced live coverage
therefore includes some initial context recovery as well as provisional
suppression. Do not claim every absent live update was provisional.

An update traverses source-to-server and server-to-client delivery legs. With
120 ms nominal egress delay on each leg, about 240 ms of added delay is a
first approximation, not a measured lower bound. Jitter, scheduling, queuing
and retransmissions affect actual ages. The 500 ms threshold remains reachable.
TCP retransmits lost packets and preserves order, increasing tail latency and
head-of-line blocking. Reliable transport does not guarantee receipt before a
finite experiment deadline. P99 alone cannot identify a fixed retransmission
count or cost without packet traces. These runs also do not establish catch-up
after impairment removal, because that transition was not deliberately tested.

## Reproduce T1-T5 inside Ubuntu

Use the submitted implementation and record its commit and any local changes.
Do not modify the instructor harness or tune to assumed hidden-test settings.
The runner starts the source, server and simulated clients in the same VM and
restarts the server for each independent run. The default source emits two
provisionals and one final per segment; server correctness must not assume a
fixed revision count. A bare-TCP readiness probe can produce an expected failed
WebSocket handshake in server logs; distinguish it from actual client errors.

From the project root, activate Ubuntu's venv and run these commands one at a
time. Each is a single line; use a new output stem to preserve earlier evidence.

```bash
source venv/bin/activate
mkdir -p results/ubuntu-next
python submission_check.py
```

```bash
python run_demo.py --clients 20 --duration 20 --runs 3 --output-stem results/ubuntu-next/t1
```

```bash
python run_demo.py --clients 100 --duration 40 --runs 3 --output-stem results/ubuntu-next/t2
```

```bash
python run_demo.py --clients 100 --duration 60 --runs 3 --slow-percent 10 --slow-ms 500 --output-stem results/ubuntu-next/t3
```

```bash
python run_demo.py --clients 100 --duration 60 --runs 3 --disconnect-percent 20 --disconnect-at 15 --disconnect-seconds 5 --output-stem results/ubuntu-next/t4
```

Run the following complete block in bash for T5. It resets the network profile
on normal exit, command failure or interruption:

```bash
(
  set -e
  trap 'sudo bash ./network_profiles/reset.sh lo' EXIT
  sudo bash ./network_profiles/delay_loss.sh lo
  python run_demo.py --clients 100 --duration 40 --runs 3 --output-stem results/ubuntu-next/t5
)
```

Save the environment, exact runner commands/output, applied profile and VM
CPU/RAM allocation. After the T5 block, confirm the profile was reset.

```bash
{
  lsb_release -ds
  uname -r
  python -VV
  pip freeze
  git rev-parse HEAD
  git status --short
} > results/ubuntu-next/environment.txt
```

## Additional memory evidence

T3 RSS sampling is supporting evidence, not a sixth required public test.
The original T1-T5 archive contains no RSS capture. The controlled comparison
below now adds six Ubuntu captures in `results/before-after-20261103/`; these
belong to those separate trials. The earlier macOS capture discussed by the
team is a separate experiment and is not included in this bundle.

For a future Ubuntu capture, identify the actual server PID during one chosen
T3 run and use the unmodified helper in another terminal:

```text
python monitor_server.py --pid <server_pid> --duration 60 --output results/ubuntu-next/t3_memory.csv
```

Replace `<server_pid>` with the numeric PID before running. The helper follows
one process, so repeat for restarted runs as needed. Record the PID and command
line to avoid observing a wrapper shell. Stable RSS supports observed memory
stability but is not a proof of queue occupancy or a universal memory bound.
Explain code-level queue limits, history pruning and transport buffers too.
A 60-second run cannot demonstrate expiry of a 70-second history window; longer
stress observations strengthen long-duration claims.

## Controlled before/after comparison (Ubuntu T3)

The team ran three independent trials of each implementation in the same
Ubuntu VM, alternating before/after for each pair. This compares the server
changes between two commits; it does not isolate one particular change.

- **Before:** `a9d5edf` (full commit recorded in the manifest), which already
  has bounded queues and semantic coalescing but lacks the later checkpoints.
- **After:** `86b2bb1`, the version used in the main Ubuntu T1-T5 result set.
- **Fixed environment:** Ubuntu 24.04.1, Python 3.12.3, websockets 17.4,
  four vCPUs and approximately 3902 MiB guest RAM. Both configurations use
  the same Python executable and installed dependencies.
- **Fixed workload:** 100 clients, 60 seconds, 10% slow readers, 500 ms
  per-caption sleep, seed 3103 and 500 ms freshness threshold. Each run used
  `--runs 1`, restarting the server; three independent runs were collected
  per configuration. Only one experiment ran at a time.
- **Network:** `tc qdisc show dev lo` recorded `noqueue` before and after.
  No netem impairment was applied. The runner, caption source, load client,
  analyser and memory helper have identical SHA-256 hashes between versions.
- **Observation:** the unmodified `monitor_server.py` followed each runner's
  actual server child PID at one-second intervals. Extra observation work was
  applied to both configurations; original T1-T5 runs did not include it.

Raw data, commands, commits, source hashes, environment and execution order
are in `results/before-after-20261103/manifest.json`, `before/` and `after/`.
`comparison_summary.json` contains the recomputed summaries, per-run missing
final sets, CSV hashes and memory observations. Every saved summary matched
its raw CSV using 76 expected finals and 228 revisions per client.

### Median results across three runs per configuration

| Metric | Before | After | After minus before |
|---|---:|---:|---:|
| Final completeness | 95.13% | 100.00% | +4.87 percentage points |
| Fresh finals | 90.00% | 99.50% | +9.50 percentage points |
| Live revision coverage | 95.18% | 93.92% | -1.25 percentage points |
| Normal-client P95 | 17.62ms | 31.11ms | +13.50 ms |
| Normal-client freshness | 100.00% | 100.00% | +0.00 percentage points |
| Slow-client P95 | 28109.67ms | 594.17ms | -27515.50 ms |
| Slow-client freshness | 1.69% | 94.30% | +92.60 percentage points |
| Overall P95 | 2255.65ms | 31.94ms | -2223.71 ms |
| Overall P99 | 24053.13ms | 172.44ms | -23880.69 ms |

Slow-client median P95 fell by **97.89%**, while normal-client
median P95 increased by **76.60%**. Both populations must be
reported: the change preserves more useful final state for slow readers, with
additional protocol/framing work and higher normal-client latency.

**Before per-run checks:**

| Run | Final completeness | Normal P95 | Slow P95 | Fresh finals | Missing final deliveries |
|---|---:|---:|---:|---:|---:|
| 1 | 95.13% | 18.79 ms | 28112.79 ms | 90.00% | 370 |
| 2 | 95.13% | 17.62 ms | 28108.52 ms | 90.00% | 370 |
| 3 | 95.13% | 17.53 ms | 28109.67 ms | 90.00% | 370 |

**After per-run checks:**

| Run | Final completeness | Normal P95 | Slow P95 | Fresh finals | Missing final deliveries |
|---|---:|---:|---:|---:|---:|
| 1 | 100.00% | 32.19 ms | 588.60 ms | 99.53% | 0 |
| 2 | 100.00% | 30.13 ms | 838.14 ms | 99.32% | 0 |
| 3 | 100.00% | 31.11 ms | 594.17 ms | 99.50% | 0 |

All normal clients received every source revision with 100% freshness in
all six trials. All six trials recorded zero duplicate/stale messages and
connection errors. Baseline missing finals remain counted as missing at the
collection deadline; their eventual arrival was not observed. The after
configuration delivered all expected finals to every client in all three runs.
Lower overall revision coverage in the after configuration should be interpreted
alongside the increased final completeness: more obsolete provisional deliveries
were suppressed rather than queued for slow application consumption.

### Memory observations

The CSV field is named `rss_mb`, but the helper reports MiB. The following
compares the median per-run maximum during the first 60 seconds after each
observer started. Sampling begins shortly after server creation, so it is an
approximate matched observation window rather than exact source timestamps.

| Observation | Before | After |
|---|---:|---:|
| Median peak RSS, first 60 seconds | 32.38 MiB | 33.09 MiB |

The original per-run memory CSVs and logs retain the full observations.
Some baseline shutdowns took longer, so full observation durations differ;
the summary limits peak comparison to the first 60 seconds. Peak RSS alone
does not establish a leak, queue occupancy or which bound activated. Both
implementations already bound their application queues; the baseline must not
be described as an unbounded-queue design. A one-minute observation cannot
establish long-duration history stability or expiry of a 70-second window.

### Interpretation and limits

In the earlier design, socket sends can complete while a slow application is
still draining buffered revisions. Bounded application queues alone therefore
do not guarantee fresh useful delivery at the receiver. The later implementation
adds receiver-congestion feedback and provisional pacing/checkpoint changes.
The observed improvement is consistent with these mechanisms, but this
commit-to-commit experiment includes multiple server changes and is not an
ablation proving which individual mechanism caused each effect.

These results apply to the tested Python/websockets 17.4 receiver path. They
do not establish identical performance for browsers or older/unknown receivers,
other loads, constrained bandwidth or hidden workloads. This T3 experiment
keeps slow reading active; it does not test catch-up after that condition ends.
The normal-client latency increase and receiver-compatibility limits belong
in the report alongside the completeness and slow-client freshness gains.

This completes the required controlled public stress-test comparison evidence.
The separate T1 coverage dip and T5 missing final in the main Ubuntu results
must still be discussed. Record any later code changes and validate the final
submitted implementation, then complete the report and browser demonstration.

## Files and verification

`verified_medians.json` retains `t1` through `t5`, each with:

- `runs`: all three recomputed instructor summaries, without rounding.
- `median`: the median of each numeric field, including nested cohort metrics.
- `verification`: source filenames, raw CSV SHA-256 hashes, expected source
  totals, summary-match checks and missing final segments for each run.

Verification checks summary consistency and observed final sets; it is not
certification of hidden-test performance or application usability. Medians are
not sums across experiments. The T1 run-01 coverage dip and T5 run-03 missing
final remain in the per-run data even where the corresponding median is 100%.

For example, recompute an Ubuntu T3 run using its recorded source totals:

```bash
python analyse_results.py results/ubuntu/t3_run01.csv --expected-finals 76 --expected-updates 228 --fresh-threshold-ms 500
```

Keep raw data and generated summaries in the ignored results folder and a
separate evidence archive. Never commit the virtual environment or generated
experiment artifacts. Browser revision display, local recall, automatic
reconnect/recovery and late join must also be demonstrated separately.
