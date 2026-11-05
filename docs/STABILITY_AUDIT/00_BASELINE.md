# VoxType stability audit: baseline

Recorded 2026-11-05 (Europe/Moscow). This baseline describes the machine and source state used for the current audit; it is not a release approval.

| Item | Observed value |
|---|---|
| Repository / branch | `C:\Dev\Projects\AI\Voice-recognizer`, `main` |
| HEAD | `ca978c7ebb4f5c2b4519a8cf607ae900935b5109` |
| Working tree | Existing uncommitted stability fixes in 14 tracked files and new audit/tools files; preserved. This iteration also changes `src/voxtype/ui/onboarding.py` and its unit test. |
| Windows | `Get-ComputerInfo`: Windows 10 Home Single Language, build 26200 (the product name reported by Windows; build identifies the actual platform more precisely) |
| CPU / RAM | AMD Ryzen 5 5500U with Radeon Graphics; 25,075,970,048 bytes physical RAM |
| Python / Qt | Python 3.12.7; PySide6 6.11.0 |
| Audio / ASR packages | sounddevice 0.5.5; faster-whisper 1.2.1 |
| Last test-installed EXE | `build/stability-audit/installed-final-earlywatch-20261105/VoxType.exe`; SHA-256 `534454064F3019694CC14526B4C091EF9D6A0408437E3BC08F28187444ECCEEA` |
| EXE/source relationship | The installed EXE predates current uncommitted source edits, including the onboarding fix; use it only as a pre-fix GUI baseline. |
| Audio device observed in GUI | `Переназначение звуковых устр. - Input (MME)`, PortAudio index `0`; actual live signal and hardware integrity not yet confirmed. |
| ASR profile observed in GUI | `Баланс`, model label `small`; installed model integrity not confirmed by this GUI observation. |

Computer Use observation: installed EXE opened a real Windows main window and eight-step onboarding. On step 2, a microphone was listed but the wizard displayed `Устройства записи не найдены` and disabled Next. The exact `0`-index cause was reproduced in a Qt test and fixed in source. The current source has not yet been retested as a packaged EXE.

Earlier startup, render, and shutdown measurements are recorded in `CRITICAL_GUI_FREEZE_INVESTIGATION.md`. They do not establish that the user's intermittent blank-window report has been resolved. Physical microphone dictation, current EXE acceptance, DPI, and clean-machine installation remain open.
