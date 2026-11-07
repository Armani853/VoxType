# Defects carried into master completion

Snapshot 2026-11-06 MSK. The full original observations and steps are in `docs/FINAL_CUSTOMER_ACCEPTANCE/03_DEFECTS_AND_FIXES.md` and `docs/PRODUCT_REBUILD/01_DEFECT_REGISTER.md`; the rows below preserve status rather than resetting history.

| ID | Severity | Current evidence | Status | Next proof |
|---|---|---|---|---|
| FA-001 | Investigating / release gate | Native Qt performance test repeatedly emits Windows `0x8001010d`; pytest returns 0 | OPEN | Isolate native cause and compare packaged GUI behavior |
| FA-002 | P0 release gate | Historical user AppHang and blank windows; current 100-cycle installed series missing | OPEN | Current-build repeated paint/exit series |
| FA-007 | P1 | PortAudio overflow shown as generic ASR error on installed `b598039`; source wording fix exists | IMPLEMENTED_UNVERIFIED | Packaged Computer Use retry with induced overflow |
| FA-008 | P1 | One 2.236 s speech segment took 24.738 s inference, RTF 11.064. A separate idle 2.2 s SAPI snippet took 2.75–2.85 s over six repeats, so the outlier was not reproduced under that load | OPEN | Installed backend/CPU trace and comparable capture-path repeat |
| FA-009 | P0 | White restored window reproduced on `7d9762b`; two duplicate activations painted on `9104e3a` | PARTIALLY VERIFIED | 100 current-version cycles and adjacent tray paths |
| FA-010 | P1 | Original uninstaller left files while app ran; isolated repaired Inno package blocked removal and then removed files | VERIFIED for isolated AppId | Production-AppId upgrade/uninstall in clean environment |
| FA-011 | P1, older installed EXE | On 2026-11-06, installed SHA `669C5607…` with an isolated profile saved `minimize_to_tray: false`, then Computer Use clicked X; window disappeared but PID 43360 persisted >3 s. Exact-path test process was stopped. Portable `6b8a02e` SHA `B1924743…` exited on the same one-cycle path | OPEN for installed release; portable point retest passed | Find old/new shutdown change, measure exact latency and retest installed final EXE repeatedly |
| FA-012 | P1 UX, older installed EXE | With empty isolated model cache, installed SHA `669C5607…` displayed «Готово» and «Начать диктовку». Portable `6b8a02e` instead displayed «Требуется модель» and «Настроить модель» through Computer Use | IMPLEMENTED_UNVERIFIED for installed release | Install final candidate and verify no-model first launch and recovery |

| FA-013 | P2 performance | `e7726e0` packaged UI smoke: first-run onboarding construction 993.6 ms and `UI_STALL` 1078.0 ms after main content paint. cProfile localized most one-time cost to native QWizard style/options initialization | OPEN | Find a responsive first-run presentation or document an explicit first-run SLO and retest installed GUI |
| FA-014 | P2 functional | Programmatic onboarding device refresh emitted `microphone_selected` and started an unsolicited two-second mic test; timed source exit produced `RuntimeError: Signal source has been deleted` in that worker | FIXED IN SOURCE / PACKAGED SMOKE | `e7726e0` regression test checks refresh does not emit and real selection does; packaged smoke log has no audio-test failure; inspect installed first-run path before VERIFIED |

No defect is deleted when fixed. The current release remains **NOT READY**.
