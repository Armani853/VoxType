# VoxType customer acceptance matrix

Date: 2026-11-05 (Europe/Moscow). Source commit: `401beb14e4c5068d4f213087978ddf4402ce82b1` (clean Git status before build). Application version: 0.2.4. Fresh standalone EXE: `build/final-acceptance/nuitka2/__main__.dist/VoxType.exe`, SHA-256 `0489d45f940469850a77bb11f80b2cb09c91865c8367537b017be836ea251e61`. The isolated acceptance installer is byte-distinct from the production installer because it uses a separate AppId to preserve the existing installation; see `05_INSTALLER_ACCEPTANCE.md`.

Statuses are `PASS`, `FAIL`, `BLOCKED`, or `PENDING`. `PASS` requires an action on the installed candidate and observed result. Prior test runs do not count.

| ID | Customer scenario | Computer Use action | Expected | Actual | Status | Time (MSK) | Evidence | Defect |
|---|---|---|---|---|---|---|---|---|
| A01 | Installer UI and installation | Computer Use opened isolated setup; selected desktop shortcut; reviewed summary; clicked Install/Finish | Clear steps, isolated install succeeds | Install completed, app and shortcut appeared. Wizard pages were mostly English while option labels were Russian | PASS (installation); UX issue | 2026-11-05 ~18:35–18:40 | Installed EXE SHA matches candidate; Computer Use installer screens | FA-003 |
| B01 | First launch and onboarding | Computer Use advanced steps 1–6, selected microphone index 0, ran calibration, verified installed `small` model, attempted RU/EN phrase capture | Painted, responsive, usable; complete wizard with actual speech | First paint and controls worked. Calibration warned of noise and offered recovery. Model verified. Without supplied voice, both phrase attempts returned empty and Next stayed disabled | BLOCKED for complete onboarding; partial steps PASS | 2026-11-05 ~18:40–18:43 | Computer Use screenshots and accessibility trees of each step | VX-P0-001, FA-004 |
| B02 | Window close and process exit | Disabled "Свернуть в системный трей" in Settings; clicked X on the installed window; checked exact EXE process path; repeated on second launch | Process ends normally | Both observed close actions removed the installed process without Task Manager | PASS for 2 cycles | 2026-11-05 ~18:45–18:50 | Computer Use window state; `Win32_Process` showed no VoxType process after each X | VX-P0-001 remains open for stress volume |
| B03 | 100 sequential launches/exits | Launched installed EXE twice, checked painted UI, dismissed unfinished onboarding, clicked X | 100 successful cycles | 2/100 cycles; both painted and exited. The remaining 98 were not run | BLOCKED for full acceptance | 2026-11-05 ~18:40–18:50 | Computer Use states and exact process-path checks | VX-P0-001 |
| C01 | RU and EN live dictation | Speak through real microphone in both languages | Accurate committed text | No controlled speech provided to the selected microphone; empty phrase results cannot evaluate accuracy | BLOCKED | 2026-11-05 ~18:43 | Installed onboarding step 6 | FA-004 |
| C02 | Insertion to target windows | Dictate to temporary Notepad/Word/Chrome/VS Code/Telegram fields | Safe ordered insertion | Not run | PENDING | — | — | — |
| D01 | Daily use, pause/resume/language/window switching | Started and stopped one real capture on installed EXE; observed processing and return to Ready | Reliable state and saved text | State sequence worked. Ambient, uncontrolled speech reached ASR; its content and accuracy are excluded. Target was VoxType itself, so insertion was intentionally withheld. Pause/resume and target switching remain untested | BLOCKED for full scenario | 2026-11-05 ~18:44–18:46 | Computer Use status/overlay sequence | FA-004, FA-005 |
| E01 | Every interactive GUI element | Opened Settings and each of seven tabs; changed tray-close option and saved | Visible and functional | Tabs painted and reacted; tray-close setting affected subsequent X behavior. All controls twice, backend effects, and restore remain untested | BLOCKED for full inventory | 2026-11-05 ~18:44–18:50 | Computer Use settings screens and second exit | — |
| F01 | Adversarial customer actions | Rapid clicks, device/focus/model faults, recovery | No hang or silent loss | Not run | PENDING | — | — | — |
| G01 | Installer removal | Opened old isolated install's `unins000.exe` through Explorer UI; confirmed Yes after separate user approval; checked exact paths | Program and shortcuts removed | Old `401beb1` acceptance directory and desktop shortcut absent; previous test installation still present. New candidate uninstall not run | PASS for old isolated installer only | 2026-11-05 ~19:07 | Computer Use uninstall prompt and exact-path checks | — |

Automated prerequisite: `scripts/lint.ps1` passed; `scripts/test.ps1` reported 259 passed, 5 deselected, but emitted Windows `0x8001010d` during the GUI benchmark despite exit code 0. This remains an investigation item and is not a GUI acceptance PASS.

After this installed-candidate test, source was changed to refresh audio-backlog statistics after pipeline drain (FA-005). Lint and 259 tests again passed, with the same Windows diagnostic. **The installed candidate above predates this fix. Its acceptance is invalid for the modified source until a new release candidate is built and retested.**

## Replacement candidate `b598039` (2026-11-05 ~19:10–19:15 MSK)

The replacement isolated setup SHA-256 was `5238250873a28ccdcd51b2fbee0f6ce4511b0786560a17f81ece2bf28510f849`; its installed EXE matched standalone SHA-256 `e6ec8c868bb006e3e0a1ac2eac9ec09c3c1573c51326a86dd80f578ff4644faf`.

| ID | Scenario and Computer Use actions | Actual evidence | Status | Related defect |
|---|---|---|---|---|
| R-A01 | Opened installer, reviewed destination/tasks/summary, chose shortcut, installed, clicked Finish | All wizard pages displayed in Russian; installed EXE hash matched compiled candidate; first-run window painted | PASS for isolated installation | FA-003 VERIFIED |
| R-B01 | Cancelled incomplete onboarding and started dictation on installed EXE | UI soon entered recoverable error. Technical log identified PortAudio input overflow, while UI incorrectly named local recognition | FAIL | FA-007 |
| R-B02 | Clicked Retry, observed Listening, then Stop, Finishing, Ready | Recovery worked; queue showed 16.1 s during processing and 0.0 s at Ready. Exact recognized content intentionally excluded | PASS for recovery and FA-005 display; overall live dictation BLOCKED | FA-005 VERIFIED; FA-008 OPEN |
| R-C01 | RU/EN controlled speech and insertion into external apps | No controlled speech supplied; only ambient audio captured with self-window target | BLOCKED | FA-004 |
| R-G01 | New candidate uninstall | Not run | PENDING | — |

After this Computer Use run, the error-message source was changed for FA-007 and a regression test added. Lint and 260 selected tests passed, with the same open Windows diagnostic. **The current source is newer than the installed `b598039` EXE, so final acceptance must restart on another build.**

## Later installed candidates, 2026-11-05 to 2026-11-06 MSK

| ID | Version / artifact | Computer Use action and independent observation | Status | Evidence / defect |
|---|---|---|---|---|
| N-B01 | Installed `7d9762b`, version 0.2.4 | Closed main window to tray, relaunched the exact EXE from Explorer. The same PID exposed a white main window for over five seconds and could not be used | FAIL | FA-009; Computer Use screen, exact PID and process response check |
| N-B02 | Installed `9104e3a`, EXE SHA-256 `824eae37ac91905c4cb193bd5a4e7439fae839d385bc8a7edd547db6a90e16a9` | Installed through the Russian wizard; first GUI painted. Closed to tray and requested exact-path duplicate activation twice. Computer Use showed a painted main window both times and Settings opened after the second. The duplicate activation command was `Start-Process`, because Computer Use app resolution was ambiguous | PASS for two window-restoration repeats; 100 full cycles BLOCKED | FA-009 partially verified; same installed PID 27708 |
| N-E01 | Installed `9104e3a` | Switched main-window language RU → EN → RU and observed the visible selection change. ASR result and persistence were not checked | PASS for visible control only; functional test BLOCKED | Computer Use screenshots; FA-004 |
| N-G01 | Installed `9104e3a`, original installer SHA-256 `1030dfbac3dde51f427c037cc098b3aaf3e21717d240b373db2c3250e8abb944` | While app PID 27708 ran, confirmed uninstall through Computer Use. Inno said some elements could not be removed; EXE and DLLs remained | FAIL | FA-010; UI completion message and exact-path file/process checks |
| N-G02 | Same installed EXE, repaired isolated installer SHA-256 `4961243f8cc4f757eb07680114c00749777b9770fe14adbf93f72b599766c059` | Reinstalled in the same test folder through Computer Use, launched app, confirmed uninstall. Inno presented a Russian stop-app warning and held removal. After targeted stop of the exact test PID, clicked OK; Inno reported complete removal | PASS for tested stop-app gate; production installer still untested | FA-010; UI dialog and post-removal exact-path check: EXE and uninstaller absent, 12 empty directories remain |
| N-G03 | Old isolated `7d9762b` test copy | Launched its UI uninstaller while app was stopped, confirmed removal and dismissed completion dialog | PASS for isolated uninstall | Inno complete-removal dialog; exact install folder absent |

No controlled RU/EN speech, external-app insertion matrix, 100 complete launch/exit cycles, 60-minute soak, or full GUI control inventory was performed on the latest package. These are **BLOCKED**, not PASS.
