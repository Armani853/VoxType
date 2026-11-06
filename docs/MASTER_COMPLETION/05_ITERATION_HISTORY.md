# Master completion iteration history

| Date (MSK) | Source/package | Action | Evidence | Result |
|---|---|---|---|---|
| 2026-11-05 | `7d9762b` installed | Computer Use hid window to tray and relaunched exact EXE | Same PID showed white window >5 s | FA-009 reproduced |
| 2026-11-05 | `9104e3a` installed | Qt-side restore fix and two duplicate activations | Main GUI painted twice; Settings opened after second | Partial FA-009 verification |
| 2026-11-06 | `9104e3a` installed, old installer | Computer Use uninstalled while app ran | Inno partial-removal message; files left | FA-010 reproduced |
| 2026-11-06 | `4167c4e` packaging, same EXE | Added `AppMutex`; reinstalled isolated package, attempted uninstall while app ran, then stopped test PID | Inno warned and waited; afterward EXE/uninstaller absent, twelve empty folders remain | FA-010 verified in isolated AppId |
| 2026-11-06 | `4167c4e` | Ruff, mypy and pytest | 261 passed, 5 deselected; recurring `0x8001010d` diagnostic | Automated gates pass, FA-001 open |
| 2026-11-06 | `4167c4e` | Produced production-AppId installer candidate | 81,017,015 bytes; SHA-256 `c254540e176af815c4539f9ae20750bca3d4e753a5eb2119c58c23a64248ceef` | Built, not installed or accepted |
| 2026-11-06 | Current audit | Tried local SAPI → Realtek output → Stereo Mixer/physical mic route | WDM-KS mixer produced zero callback blocks; mic RMS 0.000512 at 40% playback amplitude | Controlled speech route unproven; no dictation PASS |
| 2026-11-06 | Current source tests | Isolated native Qt benchmark, full performance module, prefix suite, then full `scripts/test.ps1` | Targeted 1 passed; performance module 3 passed; prefix 44 passed; full 261 passed/5 deselected in 31.44 s. No `0x8001010d` in these logs | FA-001 intermittent rather than every run; cause open |
| 2026-11-06 | Current source ASR, local `small` model | Transcribed the same 2.2 s SAPI-generated RU snippet three times each with CTranslate2 `cpu_threads=0` and `6` | Default: 2.839/2.804/2.843 s; 6 threads: 2.816/2.846/2.751 s; model load 1.353/1.109 s | FA-008 24.738 s outlier not reproduced in idle backend probe; no thread-setting change justified |

Next iteration: investigate FA-001/FA-008 and establish a capture-path audio route, then rebuild/retest relevant user scenarios. No full Nuitka rebuild was launched for this documentation/audit iteration.
