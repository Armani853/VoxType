# VoxType — release acceptance, 2026-11-05

Decision: **NOT APPROVED**. This is the current gate record, not a final product report.

| Gate | Status | Evidence / missing work |
|---|---|---|
| A — specification | OPEN | Historical `docs/TZ_COMPLIANCE_MATRIX.md` not revalidated against the 40-section source specification. |
| B — functional | OPEN | Onboarding blocked by `VX-P1-002` on old EXE and repaired on first rebuilt EXE; live dictation/insertion not checked in this cycle. |
| C — UX/UI | OPEN | Computer Use covered onboarding steps 1–6 partially, main window, general/audio settings; `VX-P1-003` pending packaged retest; remaining screens untested. |
| D — stability | OPEN | Prior 100-run render/shutdown series exists; intermittent user blank-window symptom `VX-P0-001` has no proven root cause; current source lacks new full soak. |
| E — Windows integration | OPEN | No current cross-application insertion, tray/overlay/hotkey acceptance on final candidate. |
| F — security/privacy | OPEN | Existing privacy architecture and tests do not replace a current independent audit. |
| G — installer | OPEN | Current portable candidate is being rebuilt; installer, upgrade/uninstall, clean machine untested. |
| H — regression | PARTIAL | Current source: lint/mypy passed, 255 tests passed, 5 deselected. History latency test has shown variation above threshold. |
| I — documentation | OPEN | Baseline, defect register, UX audit, test matrix, iteration record updated; user guide/spec matrix and remaining audits require review. |
| J — external acceptance | OPEN | Physical clean Windows machine and live RU/EN dictation unverified. |

No gate is inferred from a self-test, successful compilation, or absence of recent crash logs.
