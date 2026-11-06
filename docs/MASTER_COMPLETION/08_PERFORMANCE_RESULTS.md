# Current performance evidence

Hardware: Acer Aspire A515-45, Ryzen 5 5500U, 24 GB RAM, Windows 11 build 26200; NVMe SSD per user. Detailed historical observations are in `docs/FINAL_CUSTOMER_ACCEPTANCE/04_PERFORMANCE_OBSERVATIONS.md`.

| Metric / target | Measured value | Method and repeats | Status |
|---|---:|---|---|
| First content paint ≤700 ms | 1,033.5 ms | Packaged visible UI smoke, one run; hidden-window capture excluded | Target not met in this one run; distribution needed |
| Ordinary UI p95 ≤50 ms | — | No installed repeat series | BLOCKED |
| Settings open ≤150 ms | — | Settings visibly opened; no precise installed timing | BLOCKED |
| Idle shutdown ≤1 s | — | Earlier candidate exited twice; no measured current latency | BLOCKED |
| No unexplained GUI stall >200 ms | — | FA-009 white window >5 s on earlier binary; current only two restores | BLOCKED |
| ASR | 24.738 s for 2.236 s audio, RTF 11.064 | Installed `b598039`, one outlier | FAIL / FA-008 |
| Idle source ASR probe | 2.804–2.843 s default vs 2.751–2.846 s with 6 CPU threads, each n=3 | Same local `small` model and 2.2 s SAPI RU snippet; `build/fa008_asr_probe.json` | Outlier not reproduced; no defensible CPU-thread optimization |
| Post-capture process sample | Working set 572,452,864 B; private 860,045,312 B; 36 threads; 1,809 handles | `b598039`, one sample | No before/after conclusion |
| Isolated Inno rebuild | 103.063 s | `9104e3a` repair package, one run | Measured installer stage only |
| Production-AppId Inno rebuild | 101.782 s | `4167c4e`, one run | Measured installer stage only |
| Source history/settings open | history p95 99.787 ms; Settings p95 71.121 ms | Native Qt test, 20 iterations; isolated run | Source baseline only, not installed-app PASS |
| Full pytest | 55.04 s; later 31.44 s | Same selected 261 tests, two runs | Variability and intermittent Qt diagnostic need investigation |
| Nuitka cold/repeat build and cache hit rate | — | No stage report | NOT_STARTED |

No coefficient of runtime speedup is claimed. The white-window fix improved two observed restore actions, but the user targets require repeated measurements under comparable load.
