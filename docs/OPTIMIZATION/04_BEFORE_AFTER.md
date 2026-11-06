# VoxType optimization measurements

Snapshots on 2026-11-05. Baseline was taken before the current audit changes with `tools/audit_workspace.py` and `tools/audit_modules.py`; final is the latest measured source snapshot. The existing portable and installer predate the latest controller/settings fixes, so their sizes are historical and no new release is claimed.

| Metric | Method / configuration / repeats | Baseline | Latest | Change |
|---|---|---:|---:|---:|
| Workspace bytes | Full hidden-inclusive traversal; one each, zero read errors | 3,906,902,417 | 3,907,093,655 | +191,238 (+0.0049%) |
| Workspace files | Same traversal | 23,972 | 23,985 | +13 |
| Production source bytes | `src` subtree, same traversal | 1,695,803 | 1,699,540 | +3,737 (+0.22%) |
| Python modules | AST inventory, `src/voxtype`, one each | 78 | 78 | 0 |
| Production Python physical lines | AST inventory, one each | 16,852 | 16,885 | +33 (+0.20%) |
| Existing portable directory | Full traversal, **unchanged old build** | 336,226,472 bytes | 336,226,472 bytes | 0; current source not packaged |
| Existing installer copy | Full traversal, **unchanged old build** | 81,013,414 bytes | 81,013,414 bytes | 0; current source not packaged |
| Selected automated tests | `scripts/test.ps1`, Windows/Python 3.12.7, one current run | 255 passed, 5 deselected on prior source | 259 passed, 5 deselected | +4 tests; not speed evidence |
| Source coverage | `coverage run -m pytest -q`, one current run | Not measured comparably | 76% of 10,103 measured statements | N/A |
| Capture-start GUI snapshot delay | Fault-injected 350 ms PortAudio start, one pre-fix reproduction | ~360 ms | Targeted test passes below 150 ms threshold | Exact final latency not recorded |
| Resume GUI snapshot delay | Fault-injected 350 ms PortAudio start, one pre-fix reproduction | ~343 ms | Targeted test passes below 150 ms threshold | Exact final latency not recorded |

Workspace growth is audit evidence and tests. **Zero bytes were freed.** The dry-run cleanup command was rejected by automatic command review before execution; see `02_CLEANUP_PLAN.md`. No decrease in source lines or workspace size is claimed. The current portable and installer are stale relative to the source changes.

Read-only update on 2026-11-06: full workspace is **7,649,181,109 bytes, 32,106 files, 2,979 directories**, zero scan errors (`build/master_workspace_current.json`). This is **3,742,278,692 bytes above** the table's 2026-11-05 baseline because multiple acceptance builds were retained. No cleanup has been executed; this is not a before/after optimization gain. The isolated production-AppId installer candidate is 81,017,015 bytes, but it is not an accepted commercial release.

Comparable final startup time, idle/peak RAM, CPU, thread/handle counts, model-load time, ASR RTF, live GUI responsiveness, crash/hang rates, and 60-minute soak have **not** been measured on a new packaged candidate. Earlier isolated measurements in `docs/STABILITY_AUDIT/CRITICAL_GUI_FREEZE_INVESTIGATION.md` are historical and cannot be used as a same-configuration before/after comparison. Final commercial readiness is **NOT APPROVED** while the intermittent blank-window report, packaged GUI retest, and installer acceptance remain open.
