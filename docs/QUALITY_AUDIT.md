# VoxType quality audit

Baseline: VoxType 0.2.3, commit `f91adf1d920c2f79dcd88416845cedcdef8e8e08`.

| WHAT | HOW TESTED | EXPECTED | ACTUAL | STATUS | EVIDENCE |
|---|---|---|---|---|---|
| Git baseline | status/log/diff | clean release tree | clean; release commit present | PASS | `git status --short`, `git log -5` |
| Source/installed version | Python metadata and PE metadata | same patch version | 0.2.3 / 0.2.3.0 | PASS | source, portable and installed EXE |
| Release artifacts | filesystem inspection | portable ZIP and installer exist | both present | PASS | `release/VoxType-0.2.3-*` |
| Test inventory | pytest collection | all tests discoverable | 210 collected; 205 normal, 5 deselected | PASS | collection and baseline run |
| Baseline suite | deterministic non-interactive run | no failures | 205 passed, 5 deselected | PASS | 13.39 s under coverage |
| Line/branch coverage | coverage.py with branch tracing | honest baseline | 69% combined | GAP | temporary coverage data, no generated report committed |
| Existing installed EXE | version/self-test/diagnostics/UI smoke | runnable package | 0.2.3; 11/11 health; Qt smoke exit 0 | PASS | installed EXE acceptance |
| ASR configuration | diff/audit | unchanged | frozen production configuration | PASS | no ASR tuning in QA scope |

## Functional inventory

| AREA | EXISTING USER-FACING CAPABILITIES |
|---|---|
| Application | launch, single instance, tray show/hide, graceful quit, per-user autostart, portable, installer, upgrade/uninstall |
| First run | privacy, microphone discovery/refresh, calibration, hardware recommendation, model discovery/install, RU/EN phrase and insertion checks, completion persistence |
| Dictation | button/hotkey start-stop, pause/resume, explicit RU/EN, active language switch, Locked Target, optional Follow Focus, queue/backlog, tail drain, completed result |
| Audio | default/selected WASAPI input, float/int PCM normalization, mono conversion, level meter, silence/quiet/noise/clipping classification, callback overflow escalation |
| ASR | Fast/Balanced/Maximum local profiles, lazy model load, missing/corrupt model detection, guarded retry and measured backlog behavior |
| Transcript | ordered commit, overlap removal, formatting, punctuation/Unicode/Cyrillic/English, bounded vocabulary, optional whitelisted commands |
| Delivery | clipboard paste with restore, Unicode SendInput fallback, UIPI safe copy, locked-target queue, FIFO retry, duplicate fragment prevention, Copy All/manual Insert |
| Overlay | starting/listening/processing/paused/finishing/completed/warning/error, result preview, copy/insert/close and persistent final result |
| History | list/select/filter/search/empty state, copy/export/recover/delete/refresh, stale-detail prevention |
| Settings | all schema fields, defaults, migration, atomic save/load, restart persistence, hotkey validation, model/audio/privacy controls |
| Diagnostics | system/audio/model/library checks, self-test, log/data folder, export, versions |
| Recovery | journal-before-injection, idempotent committed fragments, unfinished sessions, disk spool replay, corrupt payload detection, pending delivery |
| Privacy | local STT, no transcript/PCM in technical log, bounded retention, no secrets/private evaluation data in release |

## Baseline risk classification

| SEVERITY | OPEN AT BASELINE | NOTE |
|---|---:|---|
| BLOCKER | 0 | no reproduced crash, deadlock or data loss |
| HIGH | 0 | established FIFO/journal/target guarantees remain green |
| MEDIUM | 4 | coverage gaps, incomplete systematic fault matrix, no 10k state model, incomplete measured UX/resource matrix |
| LOW | 2 | dev QA dependencies were absent; marker taxonomy is coarse |
