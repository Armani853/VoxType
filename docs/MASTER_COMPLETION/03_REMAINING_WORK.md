# Remaining work by risk and dependency

Snapshot 2026-11-06 MSK. Current source/package commit `4167c4e`; application EXE payload originates from `9104e3a`. See `01_REQUIREMENTS_MATRIX.md` and `02_COMPLETION_SCORECARD.md` for preliminary row counts. Do not promote historical evidence to current-build PASS.

## P0: user-visible correctness and lifecycle

1. Reproduce and resolve any remaining blank-window or shutdown failure. FA-009 was fixed and visually retested only twice; perform the required 100 installed start/exit cycles with paint and process checks on a **new isolated** package.
2. Check that committed text is never lost or inserted into the wrong window, including clipboard conflict, focus change and target disappearance. Use temporary target documents and a controlled capture-path speech source.
3. Test close during actual model loading, live inference, error and heavy load; correlate GUI stall traces with process state.

## P1: dictation and commercial customer path

1. Resolve FA-008, the measured 24.738 s inference for 2.236 s of audio. Profile backend/CPU contention and retest a comparable segment.
2. Investigate FA-001 Windows `0x8001010d` in the native Qt benchmark. The test script exits 0, but the diagnostic is not explained.
3. Retest FA-007 overflow wording and recovery on a packaged EXE. The source-level regression alone does not prove the customer message.
4. Prepare a safe, reversible test audio route through PortAudio. Complete RU/EN 5, 20, 40, 60, 90-second scenarios with reference, actual text, WER/CER, latency, FIFO/backlog and insertion evidence. Mark physical human-voice testing separately from TTS.
5. Complete onboarding, all settings, hotkeys, tray/overlay, app matrix, failure recovery, isolated install and uninstall. The production-AppId installer has only been compiled, not installed; preserve the existing user installation.
6. Run current-version 60-minute soak and full GUI stress. Historical results on other binaries are context only.

## P2: latency and UX

1. Record cold/warm first content paint separately from ASR readiness. The one visible packaged smoke paint of 1,033.5 ms misses the 700 ms target; collect a distribution before changing architecture.
2. Measure click-to-paint p50/p95/p99, Settings open, language/model changes, idle shutdown, event-loop stalls, RAM, CPU, handles and threads on the installed candidate. Profile confirmed bottlenecks before editing.
3. Audit all interactive controls and visual states through Computer Use, including small width, high DPI and themes.

## P3: build and repository efficiency

1. The next necessary Nuitka build now requests an XML compilation report and prints total wall time. Use it for stage and clcache hit/miss analysis, then measure a repeat. The previous isolated Inno rebuild took 103.063 s and production-AppId Inno rebuild 101.782 s; these are installer timings, not Nuitka timings.
2. Establish documented DEV, QA, RELEASE commands without using a full Nuitka build for every source edit. Preserve hidden imports, models and DLLs.
3. Refresh disk/module/dependency audit and compare workspace, source, portable and installer separately. Existing report explicitly says zero bytes freed; no broad deletion of unknown files.

## Last stage

Only after the technical and customer gates pass, research current market comparables and create the requested five-format RUB valuation. Inno Setup 6.7.3 currently identifies itself as “Non-commercial use only”; resolve commercial licensing before sale.

Immediate next engineering iteration: isolate FA-001 and measure FA-008, then implement a controlled speech route. Any runtime change requires a new EXE and relevant Computer Use retest. The current verdict is **NOT READY**.
