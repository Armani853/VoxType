# VoxType final product acceptance

Black-box/release-candidate evidence collected on 2026-07-29. The attachment
listed 0.2.3 as its baseline, but the repository and installed application had
already advanced to the tested patch release 0.2.4; no rollback was performed.

| CHECK | RESULT | EVIDENCE | DURATION |
|---|---|---|---:|
| Current version | PASS | source 0.2.4; installed file/product 0.2.4.0 | <1 s |
| Single instance | PASS | installed chaos 2/5/10: one runtime-ready transition each, all duplicate launches exit 0 | 11.2 s |
| Taskbar icon | MANUAL CHECK | packaged multi-resolution icon and Qt tests PASS; final physical shell inspection not repeated | — |
| Tray icon/menu | MANUAL CHECK | tray routing/UI tests PASS; final hover/menu visual inspection requires desktop user | — |
| First run | PASS | isolated installed UI smoke/self-test exit 0; clean DB/log created without touching real profile | 4.0 s |
| Calibration | PASS | normal/quiet/silence/noise/clipping/device-error paths automated; progress/result UI tests PASS | automated |
| Main meter | MANUAL CHECK | PCM/dBFS mapping tests PASS; final physical microphone movement requires live input | — |
| Hotkey latency | MANUAL CHECK | no valid installed min/median/p95/max measurement in the final active desktop session | — |
| Pause latency | MANUAL CHECK | immediate controller transition regression PASS; installed timing not measured | — |
| Resume latency | MANUAL CHECK | immediate controller transition regression PASS; installed timing not measured | — |
| Stop latency | MANUAL CHECK | FINISHING/timer-freeze regression PASS; installed timing not measured | — |
| Action spam | PASS | 10,000 fixed-seed actions, 9,330 exhaustive sequences and repeated 20× actions | 4 s/run |
| RU | PASS | established installed/local pipeline smoke; frozen ASR unchanged in 0.2.4 | inherited |
| EN | PASS | established installed/local pipeline smoke; frozen ASR unchanged in 0.2.4 | inherited |
| Language switch | PASS | ordered RU/EN integration test; no cross-language context or lost sequence | automated |
| Notepad insertion | PASS | 30/30 exact Unicode fragments; clipboard restored | 23 s |
| VS Code insertion | FAIL | 30 delivery accepts, only 28 exact fragments in isolated saved file | 24 s |
| Browser insertion | PASS | 30/30 actual field fragments; CRLF normalized; clipboard restored | 24 s |
| Insertion attempts | FAIL | 88/90 exact final fragments = 97.78%, below gate | 90 attempts |
| Insertion success | FAIL | required >=99%; actual exact-final score 97.78% | — |
| Lost fragments | FAIL | two expected VS Code fragments absent from saved fixture | 2 |
| Duplicates | PASS | zero in all exact final outputs | 0 |
| Clipboard restore | PASS | completed Notepad/VS Code/Chrome runs restored prior clipboard | automated |
| Locked Target | PASS | native Notepad queue/return smoke and deterministic identity tests | automated |
| Closed target recovery | PASS | target-closed/reused-HWND tests keep text queued/copyable | automated |
| Backlog Stop | PASS | immediate FINISHING, one drain, timer frozen, transcript retained | automated |
| Completed overlay 30 s | MANUAL CHECK | persistence/actions covered by UI tests; physical 30-second observation not run | — |
| Copy All | PASS | completed-result UI/controller routing tests | automated |
| Manual Insert | PASS | retained FIFO retry and target safety tests | automated |
| New session after Completed | PASS | stateful model preserves old commit and creates independent new state | automated |
| Microphone failure | PASS | enumeration, permission, sample-rate and overflow fault tests | automated |
| Model failure | PASS | missing/tampered/checksum/load/watchdog paths | automated |
| Offline mode | PASS | installed inference smoke exit 0 using existing local model | 38 s |
| Crash recovery | PASS | journal-before-delivery, unfinished-session and corrupt-DB quarantine tests | automated |
| History | PASS | filters/empty/detail invariant/delete/copy tests; 10k query benchmark | automated |
| Settings | PASS | atomic load/save/migration/corruption/pairwise/concurrency tests | automated |
| Diagnostics | PASS | installed diagnostics 11/11, eight audio inputs, no stderr | 3 s |
| Keyboard-only | MANUAL CHECK | focus styles and signal routing automated; final full tab-order walk not performed | — |
| UI max stall | PASS | maximum bounded event-loop stall 122.896 ms (<500 ms) | 200 cycles |
| 5-minute test | PASS | 302.125 s established pipeline run: 300/300, dropped/lost/duplicates 0, FIFO/drain PASS; pipeline unchanged | 302.125 s |
| Resource cycling | KNOWN LIMITATION | 200 window cycles plateaued threads 19→20→20 and handles 288→294→294; requested 50 full installed sessions not repeated | 1.0 s |
| DB integrity | PASS | installed `sessions.sqlite3`: `PRAGMA integrity_check=ok`, 14 sessions | <1 s |
| Ruff | PASS | format/check PASS after production changes | — |
| Mypy | PASS | 91 source files | — |
| Pytest | PASS | 247 passed, 5 deselected; no 60-minute soak | 21.17 s |
| Installed EXE | PASS | version, self-test, Qt smoke, diagnostics and inference smoke exit 0 | — |
| Upgrade preservation | PASS | settings/session hashes unchanged; small, large-v3, turbo and Silero retained | — |

## Release decision

**QUALITY GATE FAILED**

The concrete unresolved HIGH acceptance risk is VS Code delivery verification:

1. open an isolated VS Code profile and text document;
2. place the caret in the editor;
3. submit 30 numbered Unicode fragments through production delivery;
4. all 30 submissions report `INSERTED`;
5. save and read the isolated document;
6. only 28 distinct expected fragments are present.

Later foreground-verifier attempts correctly aborted at their warm-up guard
before scored insertions, so they neither reproduce nor clear the discrepancy.
No production change was made for an unconfirmed foreground/caret automation
failure. Release acceptance requires one quiet-desktop rerun with a verifier
that proves the editor caret before counting attempts and obtains 15/15 with
zero loss and duplicates.
