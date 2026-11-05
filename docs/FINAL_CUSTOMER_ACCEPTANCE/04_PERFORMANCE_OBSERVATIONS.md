# Performance observations

Environment on 2026-11-05: Windows 11 Home Single Language build 26200; Acer Aspire A515-45; AMD Ryzen 5 5500U, 6 physical/12 logical cores; 25,075,970,048 bytes physical RAM. Candidate app version 0.2.4, source commit `401beb1`, EXE SHA-256 `0489d45f940469850a77bb11f80b2cb09c91865c8367537b017be836ea251e61`.

| Metric | Baseline | Candidate result | Method / repeat count | Timestamp and scenario |
|---|---|---|---|---|
| GUI response latency | Historical source test mixed; no comparable installed baseline | Pending | Computer Use timed actions | — |
| Qt event-loop stalls | Historical logs include stalls; no same-config baseline | Pending | Candidate technical log correlated with UI actions | — |
| CPU, RAM, threads, handles | No same-config installed baseline | Pending | Process counters | — |
| Startup and shutdown | Historical isolated series only | 2 installed cycles painted and exited; elapsed times not captured precisely | Computer Use UI observation plus exact-path process check, n=2 | 2026-11-05 ~18:40–18:50; onboarding and X |
| Model/ASR/insertion latency | No same-config installed baseline | Pending | Real microphone and target apps | — |

Automated test output contained Windows `0x8001010d` despite pytest exit 0. Do not classify it as harmless or as a confirmed product crash until reproduced and traced.

One ~7-second capture on the earlier installed candidate showed Listening → Processing → Finishing → Ready without a frozen paint. At Ready, the queue tile retained a stale 4.9-second value. Root cause and source fix are recorded as FA-005. No comparable CPU, RAM, thread, handle, event-loop, insertion, or ASR timing baseline was captured for that earlier candidate.

## Replacement installed candidate `b598039`

The first live capture failed with PortAudio input overflow at 19:12:38 MSK, about 0.55 s after model readiness. Retry began a second capture at 19:13:46. The technical log recorded these ASR segments without transcript text:

| Log time (MSK) | Audio duration | Inference duration | RTF | Queue at log point |
|---|---:|---:|---:|---:|
| 19:13:58 | 4.636 s | 4.733 s | 1.021 | 5.010 s |
| 19:14:23 | 2.236 s | 24.738 s | 11.064 | 3.600 s |
| 19:14:30 | 3.580 s | 5.539 s | 1.547 | 10.050 s |
| 19:14:34 | 0.924 s | 4.283 s | 4.636 | 4.860 s |
| 19:14:40 | 1.850 s | 5.495 s | 2.970 | 1.830 s |

The UI showed 16.1 s backlog after Stop and returned to Ready with 0.0 s at 19:14:41, verifying FA-005. A single process sample after completion showed 572,452,864 working-set bytes, 860,045,312 private bytes, 36 threads, 1,809 handles, and cumulative CPU time 282.5 s. These are one-point observations, not peak/idle comparisons. The 24.738 s inference outlier is FA-008 and remains unexplained; CPU and event-loop traces synchronized to it were not collected.
