# VoxType — rebuild iterations

## 2026-11-05: first manual GUI cycle

- Baseline: `main` at `ca978c7ebb4f5c2b4519a8cf607ae900935b5109` with pre-existing uncommitted stability changes. No existing edits were discarded.
- Computer Use on the older test-installed EXE exposed `VX-P1-002`: PortAudio index `0` looked like an absent microphone and blocked step 2 of onboarding.
- The source fix distinguishes `None` from `0`. A targeted Qt test passed. `scripts/lint.ps1` and `scripts/test.ps1` passed with 254 tests after formatting earlier uncommitted edits.
- Portable EXE SHA-256 `99B7B513255F43DC660A4D393E268CBA3496B0BF93CDBFD3C79C24C83595B19B` passed build smoke and a Computer Use retest through onboarding steps 1–6 (step 6 viewed only). Calibration captured silence and correctly reported no speech; installed `small` passed the wizard's model check.
- The same EXE exposed `VX-P1-003`: audio settings overflow horizontally. The source fix and a Qt layout test at 900/760 px passed; packaged GUI retest is pending.
- History open latency benchmark exceeded 300 ms in two runs, passed in another; `VX-P2-001` remains triaged.
- After the audio layout change, `scripts/lint.ps1` passed and `scripts/test.ps1` passed 255 tests, 5 deselected. A new portable build is in progress.

Release status remains **NOT APPROVED**. The original intermittent blank-window defect, live speech, full GUI acceptance, and clean-machine acceptance remain open.
