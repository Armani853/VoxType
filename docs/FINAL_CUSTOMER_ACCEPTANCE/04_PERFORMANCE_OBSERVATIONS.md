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

One ~7-second installed capture showed Listening → Processing → Finishing → Ready without a frozen paint. At Ready, the queue tile retained a stale 4.9-second value. Root cause and a source fix are recorded as FA-005; the fix has not been measured in a new installed build. CPU, RAM, thread, handle, event-loop, insertion, and ASR timing data were **not collected**, so no improvement claim is made.
