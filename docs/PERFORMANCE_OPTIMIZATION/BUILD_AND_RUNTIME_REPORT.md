# VoxType build and runtime performance: measured checkpoint

Date 2026-11-06 MSK; Ryzen 5 5500U, 24 GB RAM, NVMe SSD, Windows 11 build 26200. This report distinguishes source/test, Nuitka, installer and installed runtime. No claim of final optimization is made without matched before/after data.

| Stage | Measurement | Repeats / method | Limit |
|---|---:|---|---|
| Source edit | — | No timed representative edit | Unmeasured |
| Ruff format/check and mypy | 2.16 s total | `scripts/lint.ps1`, 2026-11-06, n=1 | Combined time only |
| Full selected pytest | 55.04 s; later 31.44 s | `scripts/test.ps1`, n=2, 261 pass/5 deselected each | Variation needs investigation; first emitted Windows `0x8001010d`, second did not |
| Native Qt performance module | 12.82 s | Three tests, n=1 | Source test, not installed GUI |
| Native Qt history/settings open | history p50/p95 68.801/99.787 ms; settings 44.651/71.121 ms | 20 show/processEvents loops, isolated targeted test | No p99 or installed-app measurement |
| Nuitka code generation, MSVC, link, copy | — | Current build lacks stage report | Cannot attribute build bottleneck |
| clcache hit/miss, preserved cache | — | No cache statistics collected | Unmeasured |
| Portable ZIP | — | No timed current build | Unmeasured |
| Isolated Inno Setup | 103.063 s | `9104e3a` payload, one compile | Installer stage only |
| Production-AppId Inno Setup | 101.782 s | Same payload, `4167c4e` packaging, one compile | Installer stage only |
| Packaged visible first paint | 1,033.5 ms | `9104e3a` smoke, n=1 | Exceeds 700 ms target in this run; not cold/warm distribution |
| Time to ASR ready | — | No controlled installed measurements | Unmeasured |
| UI p50/p95/p99; shutdown | — | No current installed series | Unmeasured |
| ASR latency | 24.738 s for 2.236 s audio (RTF 11.064) | `b598039` live ambient capture, one outlier | FA-008; root cause unknown |
| Source ASR on known signal | Default threads 2.839/2.804/2.843 s; 6 threads 2.816/2.846/2.751 s | Local `small` model, same 2.2 s SAPI RU snippet, n=3 each | 24.738 s outlier not reproduced; changing thread count has no proven benefit |
| Post-capture memory | 572,452,864 B working set; 860,045,312 B private | `b598039` process, one point | Not peak/idle comparison |
| Installer size | 81,017,015 B | `4167c4e` production-AppId candidate | No size optimization claim |

The development path already has `scripts/run.ps1`, `scripts/lint.ps1` and `scripts/test.ps1`; only release packaging needs Nuitka. `scripts/build_portable.ps1` currently removes its build root before compiling and does not request a Nuitka compilation report. This can discard incremental outputs. A safe optimization needs an isolated measured warm-build experiment and a report before changing the release script; hidden imports/native DLLs must remain.

Python coordinates UI, audio queues, state transitions and formatting, while PySide6/Qt paint, PortAudio capture, ONNX VAD and CTranslate2 inference run substantially in native libraries. The precise CPU time split is **not measured**. No Rust/C++ module or PySide6 migration is justified by current evidence. A Qt rewrite would increase packaging and regression scope without a demonstrated user benefit. Measure a specific CPU-bound Python hot path before any native prototype.

Targets remain: first visible content ≤700 ms; ordinary UI p95 ≤50 ms; Settings ≤150 ms; idle close ≤1 s; no unexplained GUI stall >200 ms; responsive UI under maximum ASR. No target is marked PASS solely from a source test or one smoke measurement. Matched installed-EXE before/after and Nuitka compilation report remain required.
