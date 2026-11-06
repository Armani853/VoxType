# Defects carried into master completion

Snapshot 2026-11-06 MSK. The full original observations and steps are in `docs/FINAL_CUSTOMER_ACCEPTANCE/03_DEFECTS_AND_FIXES.md` and `docs/PRODUCT_REBUILD/01_DEFECT_REGISTER.md`; the rows below preserve status rather than resetting history.

| ID | Severity | Current evidence | Status | Next proof |
|---|---|---|---|---|
| FA-001 | Investigating / release gate | Native Qt performance test repeatedly emits Windows `0x8001010d`; pytest returns 0 | OPEN | Isolate native cause and compare packaged GUI behavior |
| FA-002 | P0 release gate | Historical user AppHang and blank windows; current 100-cycle installed series missing | OPEN | Current-build repeated paint/exit series |
| FA-007 | P1 | PortAudio overflow shown as generic ASR error on installed `b598039`; source wording fix exists | IMPLEMENTED_UNVERIFIED | Packaged Computer Use retry with induced overflow |
| FA-008 | P1 | One 2.236 s speech segment took 24.738 s inference, RTF 11.064 | OPEN | Backend/CPU trace and comparable repeat |
| FA-009 | P0 | White restored window reproduced on `7d9762b`; two duplicate activations painted on `9104e3a` | PARTIALLY VERIFIED | 100 current-version cycles and adjacent tray paths |
| FA-010 | P1 | Original uninstaller left files while app ran; isolated repaired Inno package blocked removal and then removed files | VERIFIED for isolated AppId | Production-AppId upgrade/uninstall in clean environment |

No defect is deleted when fixed. The current release remains **NOT READY**.
