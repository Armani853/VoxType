# Feature-to-test traceability

`PASS` means the referenced observable behavior test passed in the current
baseline. `GAP` is work remaining in this QA pass. `MANUAL` requires a real
desktop/hardware/application.

| FEATURE | IMPLEMENTATION | UNIT | INTEGRATION/UI | E2E/FAILURE | STATUS |
|---|---|---|---|---|---|
| Launch/CLI/self-test | `__main__`, runtime | packaging contracts | installed self-test | installed diagnostics | PASS |
| Single instance | runtime guard | runtime tests pending | installed chaos planned | 2/5/10 launches | GAP |
| Tray show/hide/quit | tray/app | overlay-tray tests | Qt routing | installed smoke | PASS |
| Autostart off/on/off | startup | startup tests | settings binding | registry isolation | PASS |
| First-run privacy | onboarding | onboarding tests | UI page state | clean profile | PASS |
| Microphone discovery/refresh | devices/app | device faults planned | onboarding signal routing | physical device | GAP |
| Calibration states | calibration | 10 calibration tests | wizard progress/result | real voice | PASS |
| Hardware detection | monitoring | hardware tests planned | diagnostics | installed health | GAP |
| Model discovery/install | ModelManager | atomic/tamper/Xet tests | app worker | cached fixture | PASS |
| Onboarding completion persistence | settings/onboarding | settings round trip | wizard choices | isolated relaunch | PASS |
| Start button/hotkey | app/controller/hotkeys | hotkey tests | controller pipeline | installed/manual | PASS |
| Stop/tail drain | controller | state tests | drain/journal integration | installed/manual | PASS |
| Pause/resume | controller | state tests | no text while paused | installed/manual | PASS |
| RU/EN and active switch | controller/models | language tests | ordered bilingual commit | installed/manual | PASS |
| Locked Target | target manager | 9 target tests | controller/injection | native Notepad smoke | PASS |
| Follow Focus | target manager | explicit follow test | injection target checks | desktop smoke | PASS |
| Queue/backlog/spool | audio spool | 12 queue/spool tests | accelerated soak | corruption/recovery | PASS |
| Forced segment/tail | segmenter | 12 segmenter tests | WAV pipeline | deterministic audio | PASS |
| Model loading single future | controller | model state | double-start integration | load error/watchdog | PASS |
| Fast/Balanced/Maximum profiles | profiles | profile tests | benchmark selection | real model not repeated | PASS |
| Transcript overlap/order | merger | 14 merger tests | controller FIFO | replay/duplicate | PASS |
| Formatting/Unicode/multiline | formatter/injection | formatter/send-input tests | injection service | native Unicode smoke | PASS |
| Vocabulary bounded/atomic | vocabulary | transcript test | settings binding | fuzz planned | GAP |
| Clipboard restore | clipboard | 6 clipboard tests | injection tests | native clipboard smoke | PASS |
| SendInput fallback | send_input/injection | 25 delivery tests | service fallback | native Notepad | PASS |
| UIPI/elevated target | foreground/target | conservative block tests | safe-copy result | elevated manual | PASS |
| Duplicate prevention | injection/journal | fragment-id test | journal-before-inject | retry failure | PASS |
| Completed Copy All/Insert | overlay/app | overlay tests | app signal routing | installed/manual | PASS |
| Overlay state matrix | overlay/models | overlay matrix | Qt smoke | installed visual | PASS |
| History filters/search/empty | history window/repository | history UI tests | session adapter | 1k/10k benchmark planned | GAP |
| History export/recover/delete | app/repository | repository tests | UI signal tests | filesystem fault planned | GAP |
| Settings defaults/load/save | schema/repository | settings tests | dialog round trip | corrupt/legacy | PASS |
| Every schema field | settings window/schema | pairwise planned | submit adapter | restart persistence | GAP |
| Hotkey conflict | hotkeys | conflict/rollback tests | app error UX | native conflict planned | PASS |
| Diagnostics/self-test/export | monitoring/app/UI | diagnostics UI | installed health | export fault planned | GAP |
| Journal/recovery | journal/repository | idempotence test | controller commit order | crash smoke established | PASS |
| Retention | retention | completed/orphan tests | startup maintenance | locked-file planned | GAP |
| Privacy logs | logging/audit tool | sensitive-log tests | release cleanup | artifact scan | PASS |
| Portable/installer/upgrade | scripts/Inno | packaging contracts | package self-test | upgrade preservation | PASS |
