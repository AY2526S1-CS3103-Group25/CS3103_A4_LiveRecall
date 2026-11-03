# Team Contributions

<!-- TODO(team): fill in student IDs and agree the percentages before the
     Canvas submission. Percentages must total 100%. Responsibilities below
     were drafted from git history and issue assignments; adjust if they do
     not reflect the agreed split. -->

| Member | Main responsibilities | Experiments/report sections | Agreed contribution % |
|---|---|---|---:|
| Leong Wei Zhi (@LeongWZ) | Server: `CAPTION_UPDATE` ingestion/validation and revision dedup (issues #1, #2); backpressure checkpoints and recovery pacing; reconnect backoff fix (#41); cleanup audits; repo/process and submission docs (#16) | Design notes; cleanup/before-after audit write-ups | 25 |
| Tan Yong Quan (@yongqqqq) | Server: bounded history and resume (#6), per-client delivery isolation and semantic overload handling (#9, #10); server regression suites | Slow-reader (T3) analysis and delivery trade-offs | 25 |
| Boon Kai Ming (@CloudKai) | Browser client: live caption display and validation (#3), local recall windows (#7); client regression suite; AI-suggestion evaluation for the client | Client design notes; demo walkthrough | 25 |
| Ng Zhan Wen (@panomia01) | Experiments and evaluation: public tests T1-T5 on Ubuntu (#11), netem impairment runs (#12), controlled before/after comparison (#13); evidence verification | Public test results and evidence archive | 25 |

Total contribution should be 100%.

Shared/integration work: PR reviews were done across the team (every PR was
reviewed by at least one other member plus the automated reviewer), and the
server/client protocol boundary (`CLIENT_HELLO` resume semantics, `HISTORY`
batch merging) was designed jointly across #6, #7 and #8. Git history is
supporting evidence, not a substitute for this statement.
