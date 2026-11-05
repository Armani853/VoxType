# VoxType — current test matrix

Recorded 2026-11-05. `PASS` describes the stated configuration only; `OPEN` means no current acceptance evidence.

| Scenario | Method / evidence | Result |
|---|---|---|
| PortAudio index `0` in onboarding | Computer Use on pre-fix EXE; Qt regression; Computer Use on portable EXE SHA-256 `99B7B513255F43DC660A4D393E268CBA3496B0BF93CDBFD3C79C24C83595B19B` | PASS on that EXE; advanced to calibration |
| Calibration starts on chosen device | Computer Use on same EXE; index `0` selected | PASS for starting and completing capture; no speech supplied, so result correctly reported no speech |
| Installed `small` model check | Computer Use on same EXE, onboarding step 5 | PASS: UI reported installed and verified; no live inference claim |
| Audio settings horizontal fit | Qt test at 900 and 760 px on current source | PASS in source; new EXE retest OPEN |
| Static analysis | `scripts/lint.ps1` on current source | PASS: Ruff and mypy |
| Automated regression | `scripts/test.ps1` on current source | PASS: 255 passed, 5 deselected; an earlier run failed the history latency benchmark |
| History open latency | Existing 20-cycle benchmark, 250 entries | MIXED: p95 341.714/380.892/233.851 ms; investigation open |
| Current portable EXE smoke | First rebuilt EXE self-test, inference-smoke, UI-smoke | PASS; second rebuild pending |
| Original blank/white window | Instrumented prior 100-run source/EXE series in `docs/STABILITY_AUDIT/CRITICAL_GUI_FREEZE_INVESTIGATION.md` | Not reproduced; root cause OPEN |
| Live RU/EN dictation and insertion | Physical microphone and target application | OPEN |
| Tray, overlay, hotkeys, DPI 100–200% | Computer Use on current EXE | OPEN |
| Installer, upgrade/uninstall, clean Windows | Installation acceptance | OPEN |

The matrix is intentionally narrower than the historical 421-item specification matrix. It records new evidence for this rebuild cycle and must expand before release approval.
