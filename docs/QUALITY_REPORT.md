# VoxType 0.2.4 quality confidence report

Date: 2026-07-29
Source: `C:\C_C++_C#\Vacancies\Voice-recognizer`
Baseline commit: `f91adf1d920c2f79dcd88416845cedcdef8e8e08`
Release state: 0.2.4 changes and QA artifacts are prepared for the final
quality commit.

## Outcome

The hardening pass found and fixed three production risks:

1. duplicate GUI launches could construct multiple runtimes before hotkey
   conflict detection;
2. concurrent settings saves could race at `os.replace` on Windows;
3. a corrupt sessions database could abort startup.

User-facing background errors no longer expose Python exception names. Corrupt
settings/history recovery now gives a concise safety/recovery message.
Audio capture, VAD, segmentation, Whisper profiles and ASR parameters were not
changed.

The release is installed and operational, but the final verdict remains
`QUALITY GATE FAILED` because the required controlled insertion score was not
demonstrated.

## Automated tests

| AREA | RESULT |
|---|---|
| Collected | 252 |
| Normal developer gate | 247 passed, 5 deselected |
| Concurrency | 4 passed |
| Fault injection added | 6 passed |
| Pairwise settings | 2 passed |
| Bounded performance | 3 passed |
| Property suites | 6 passed |
| Stateful/model-based | 3 passed |
| Critical flaky loop | 20/20 suite runs, 0 failures |
| Ruff format/check | PASS |
| Mypy | PASS, 91 source files |

Hypothesis executed 425 configured examples across formatter, settings,
merger, journal, FIFO queue and vocabulary properties. The state model executed
10,000 actions with fixed seeds beginning at `20260729`, plus all 9,330
critical action sequences through length five. The bounded mutation runner
killed 13/13 critical mutants; survivors: 0.

## Coverage

Last measured full production coverage before the final five focused regression
assertions:

- line: 73.70% (7,413 / 10,058);
- branch: 54.45% (1,163 / 2,136);
- combined: 70%.

Important measured combined coverage includes TranscriptMerger 92%, segmenter
91%, target manager 86%, state machine 80%, audio spool 80%, settings
repository 80%, session repository 79%, injection 81% and single-instance 71%.
Native Win32 success/error paths, CLI/runtime construction and top-level Qt
orchestration remain the largest uncovered areas. The 90% desired whole-project
target was not reached; no meaningless tests were added to inflate it.

## Performance and resources

| METRIC | RESULT |
|---|---:|
| Installed Qt smoke including 750 ms close timer | 3.111 s baseline |
| History open p50 / p95 | 8.047 / 11.652 ms |
| Settings open p50 / p95 | 2.806 / 5.518 ms |
| Maximum measured event-loop stall | 122.896 ms |
| 10k history recent query | 21.425 ms |
| 10k history text search | 2.518 ms |
| DB integrity | `ok` |
| 200 window cycles | threads 19→20→20; handles 288→294→294 |

Hotkey, pause, resume and stop p50/p95 were not re-measured on a quiet physical
desktop in this final bounded pass. Existing controller tests still prove that
their commands return without blocking slow model/capture work.

## Insertion benchmark

| APP | ATTEMPTS | SUCCESS | FAILURES | LOST | DUPLICATES | CLIPBOARD | EXACT FINAL |
|---|---:|---:|---:|---:|---:|---|---|
| Notepad | 30 | 30 | 0 | 0 | 0 | restored | yes |
| VS Code | 30 | 28 | 2 | 2 | 0 | restored | no |
| Chrome | 30 | 30 | 0 | 0 | 0 | restored | yes, after CRLF normalization |
| Total | 90 | 88 | 2 | 2 | 0 | restored on completed runs | 97.78% |

The corrected unique-fixture reruns for VS Code and Chrome aborted at the
warm-up guard before any scored attempt because the active desktop session did
not retain the intended caret. They therefore neither improve nor reduce the
90-attempt score above. This points to foreground automation/verifier
instability, not a reproduced core delivery failure, but the VS Code loss
cannot honestly be waived.

## Installed release

- Installed executable:
  `C:\Users\Алла\AppData\Local\Programs\VoxType\VoxType.exe`
- File/product version: 0.2.4.0.
- Version, self-test, diagnostics, Qt smoke and inference smoke: exit 0.
- Diagnostics: 11/11 checks OK; eight input devices discovered.
- Clean isolated profile: UI smoke and self-test exit 0; a clean session DB and
  log were created; onboarding was not falsely persisted as complete.
- Single-instance chaos: 2, 5 and 10 simultaneous installed launches each
  produced exactly one runtime-ready transition; every process exited 0.
- Upgrade preservation: settings hash
  `5E752F28172946AD758B31365AE2D1D19DD4944D403CDB7F9CB582040110E9AC`
  and sessions hash
  `DCC403CA84BB416075AF01E04F9EFC8775EF63A255657D959176FD9E9E2DDA9B`
  were unchanged. Models `small`, `large-v3`, `large-v3-turbo` and
  `silero-vad` remain present.

## Release artifacts

- Portable directory:
  `C:\C_C++_C#\Vacancies\Voice-recognizer\dist\VoxType`
- Portable ZIP:
  `C:\C_C++_C#\Vacancies\Voice-recognizer\release\VoxType-0.2.4-win64-portable.zip`
- ZIP SHA-256:
  `4900A1786365E14CF76ACFF879ED7B674827720B1ECF2567C3BEA91716F97363`
- Installer:
  `C:\C_C++_C#\Vacancies\Voice-recognizer\release\VoxType-0.2.4-win64-setup.exe`
- Installer SHA-256:
  `D742537F71B0E7008A5B6605684430C306DA99807B97D2763EBB32CCD0517031`
- Installer compile, smoke install, installed self-test and smoke uninstall:
  PASS.

## Static, dependency and code audit

- `pip check`: no conflicts.
- `pip-audit`: no known vulnerabilities.
- Vulture high-confidence findings were only required `exc_type` protocol
  parameters on context-manager methods; none were removed.
- Radon average complexity: A (3.11). High-complexity orchestration and frozen
  guard/model functions were retained because a broad refactor would increase
  release risk.
- No production dependencies were removed; every pinned runtime dependency has
  direct production imports.
- No ASR research, 12-file benchmark or 60-minute soak was run.

## Bugs and limitations

| SEVERITY | BEFORE | AFTER / REMAINING |
|---|---:|---|
| BLOCKER | 0 | 0 reproduced |
| HIGH | 3 production + 1 acceptance risk | 0 in fixed production paths; 1 unresolved VS Code insertion acceptance risk |
| MEDIUM | 0 | 0 reproduced |

Remaining limitations:

- VS Code/Chrome automation needs a quiet interactive desktop where the test
  owns foreground focus. The current active session cannot provide a clean
  reproducible result.
- Physical live-voice quality, hotkey response timing and microphone behavior
  still require a human at the machine; deterministic and prior installed
  smokes do not replace that check.
- Whole-production branch coverage is below the desired target, primarily in
  platform/native and top-level orchestration branches.

## Final verdict

**QUALITY GATE FAILED**

All mandatory build, installed-EXE, data-integrity, state, fault, mutation and
static gates passed. Controlled insertion did not meet the required >=99% with
zero loss, so the release is not represented as fully accepted.
