# Release acceptance state

## Candidate `53ed6bb`, 2026-11-15 — NOT READY

Computer Use acceptance was stopped at the user's request on 2026-11-15 before old test uninstall or new installer test. The old test process and registration remain. Do not infer installed-EXE acceptance from the packaged smoke.

Portable source commit `53ed6bbdc9e0f24033717f7e8842e8bc8dd8f8e4` matches `dist/VoxType/build-commit.txt`. `scripts/lint.ps1` passed; `scripts/test.ps1` passed **280 tests, 5 deselected**. One Nuitka build completed in **520.095 s**; packaged self/inference/UI smoke completed in **31.477 s**; ZIP took **26.482 s**. `dist/VoxType/VoxType.exe` SHA-256 `B61D08538F40409E60B529AAF9C29B83488D77DBA14A37A40445369758C277BE`; `release/VoxType-0.2.4-53ed6bb-win64-portable.zip` SHA-256 `C52ADD3D1D01978316DDFD6D78282D963128FC24A9F3625E675466DC908CFDA4`.

Inno compilation succeeded in **96.999 s**, producing `release/VoxType-0.2.4-53ed6bb-win64-setup.exe`, SHA-256 `40FD0D9D564404660201585B8AA83834501D5FE8ED24AC6612D3CDE21EF7E0C7`. Installer smoke was explicitly skipped: the host already has a VoxType uninstall registry entry for the same production AppId, and an older VoxType test process is running. The installer, installed EXE, end-to-end RU/EN capture and insertion, hotkeys, uninstall, and customer journey are **not verified** for this candidate. The Inno compiler reports **“Non-commercial use only”**; commercial licensing must be resolved before sale. No commercial release approval or valuation follows from this build.

## Candidate `e7726e0`, 2026-11-07

Application source/packaging commit `e7726e0`, clean at build start. Ruff/mypy passed; **268 tests passed, 5 deselected**. One full Nuitka + Inno build completed; packaged self-test, inference smoke, visible UI smoke, isolated installer install/self-test/uninstall succeeded. Portable EXE SHA-256 `29EA16F79762272684FFDB8721E9298ADFF9474195A88480A14165B136C1EB34`; portable ZIP SHA-256 `DDF115657DAE67B6C7CABECCBB6ADFE788E8379DE6A34C8A161DE0FCE5CF5F36`; installer SHA-256 `3311FF9E661F5F1FEF9F452924F233A2C892A31F3DCBD72A5299EDCC0DC3FCCB`. Names include `e7726e0` and old release files were preserved.

**Still NOT READY.** The packaged GUI smoke logged a roughly 1.08 s Qt event-loop stall during first-run onboarding. The binary has not had an interactive installed-EXE customer journey, ten RU/EN capture-path tests, application insertion matrix, 100 start/exit cycles or 60-minute soak. The local Inno compiler again printed “Non-commercial use only”; check its official commercial licensing before sale. Automated install/uninstall is not a substitute for Computer Use acceptance.

## Historical candidate, 2026-11-06

**NOT READY** on 2026-11-06. Latest source and packaging commit: `4167c4eae5dd9c2e61841cac8ba4298573ec8ba6`; application EXE payload built from `9104e3a`, SHA-256 `824eae37ac91905c4cb193bd5a4e7439fae839d385bc8a7edd547db6a90e16a9`. Production-AppId installer candidate: `build/final-acceptance/VoxType-0.2.4-4167c4e-release-candidate-setup.exe`, 81,017,015 bytes, SHA-256 `c254540e176af815c4539f9ae20750bca3d4e753a5eb2119c58c23a64248ceef`. Its production-AppId installation was **not** tested, to preserve the existing installation.

Ruff, mypy and 261 selected tests passed before this candidate was built; five tests were deselected. Native Qt emitted `0x8001010d` despite test process exit 0. The isolated package guarded against uninstall while app ran and then removed installed files. The current production package has not completed the buyer journey, ten speech scenarios, insertion app matrix, 100 start/exit cycles, 60-minute soak or production upgrade/uninstall. The Inno compiler printed “Non-commercial use only”; sale licensing is unresolved.
