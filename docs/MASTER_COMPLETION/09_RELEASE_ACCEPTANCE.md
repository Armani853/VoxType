# Release acceptance state

## Candidate `a9bc57c`, 2026-11-21

Build commit `a9bc57c988c2dd80a50d85e4582bbf4c5ca63eab`. Packaged self-test, local inference smoke and visible UI smoke exited successfully. The smoke log also records first content paint at 1934.8 ms from main-window construction and watchdog gaps of 1390 ms and 2015 ms. These observations are above the product targets. The stack sample includes logging flush, which alone does not establish the cause of the entire stall.

| Artifact | SHA-256 |
|---|---|
| `dist/VoxType/VoxType.exe` | `056d704a559f67549bd6ede415e9d28051907d18bb995d3586325283dfe7ac52` |
| `release/VoxType-0.2.4-a9bc57c-win64-portable.zip` | `1a657a59c93a34abf1450c98288e30ee8ec5e07695a86cda3c75f9fa8330155b` |
| `release/VoxType-0.2.4-a9bc57c-win64-setup.exe` | `6e73a2e2ce9e147cb79526a1eddff56350280bd1d9640d7ef1d405dfd8f38475` |

Nuitka wall time 637.544 s. The timestamp log puts C generation/composer at 41.993 s, Scons startup/compile up to the linker announcement at 401.567 s, and linking plus final standalone work at 7.100 s combined. clcache reports 582 hits and 16 misses. Smoke took 38.514 s, ZIP 30.050 s and Inno 95.428 s. Archive and installer hashes were recomputed against their sidecars.

Installer execution was skipped on the working profile to preserve the current installation. Installed hotkeys, insertion and uninstall remain open. The source speech results apply to Stereo Mix with synthetic reference audio. The compiler displayed “Non-commercial use only”. The candidate is not approved for production or sale.

## Pending interactive release check

Use a clean Windows profile or VM for the production installer. The current Windows profile already has VoxType registered under the production AppId. Run the installer and launch the installed executable. Open an empty Notepad document, place the caret in it, and copy a distinct clipboard marker. With insertion enabled and automatic full-result copying disabled, start and stop dictation with the configured hotkey (default Ctrl+Alt+Space). Hold the shortcut through the recording start to check that one press does not immediately stop the recording. Dictate a short phrase, then verify that the exact text appears once in Notepad and the clipboard marker is restored. Repeat with a new Notepad document and a different phrase. Close the original target before submitting another result and verify that VoxType retains the text and leaves other windows untouched.

Repeat with a long RU and EN input, then cancel processing in the overlay and start a fresh dictation after native processing releases the model. Confirm that no cancelled tail is pasted. Close and relaunch VoxType, verify the shortcuts still register, then uninstall the candidate in the clean environment. Record the installer and installed EXE hashes with the results.

## Candidate `8681888`, 2026-11-15 — INSTALLED, NOT COMMERCIALLY APPROVED

Source commit `868188869a2ec95f7611a292c456f406d7fa1ef7` includes the Nuitka autostart fix. Ruff/mypy and `scripts/test.ps1` passed (281 passed, 5 deselected). One Nuitka build: 505.085 s; packaged self/inference/UI smoke: 31.417 s; ZIP: 26.839 s; Inno compile: 100.811 s. Installed program EXE SHA-256 `7E6488B6E74A50557437D79488EEB9BBAD2772A826B1E83217B41CA9F61850A2`; portable ZIP `8DF50171E6191153B491B0B4192A15C99147D241F3C7BC00CE5E777A7EE3FB4A`; installer `6BFCC7BC883A659D22358C38FC11BE52384302BB55BF570E4E4C3A2A2492365B`.

The installer upgraded the working per-user program in `C:\Users\Алла\AppData\Local\Programs\VoxType` (exit 0); installed `--ui-smoke-test` returned 0. In a separate isolated profile with autostart enabled, the packaged binary registered its own `VoxType.exe`, not `python.exe -m voxtype`. After the real-profile launch (PID 27244), HKCU Run still pointed to the installed `VoxType.exe`; settings retained `start_with_windows=true` and the SQLite file remained present. The working installer upgrade was authorized by the user's explicit update request. **Not tested:** real hotkey press, RU/EN capture and insertion, complete GUI/customer journey, installer uninstall of this working copy. Inno still reports “Non-commercial use only”; resolve licensing before sale. No 100% or production approval is claimed.

## Candidate `53ed6bb`, 2026-11-15 — NOT READY

Computer Use acceptance was stopped at the user's request on 2026-11-15 before old test uninstall or new installer test. The old test process and registration remain. Do not infer installed-EXE acceptance from the packaged smoke.

Portable source commit `53ed6bbdc9e0f24033717f7e8842e8bc8dd8f8e4` matches `dist/VoxType/build-commit.txt`. `scripts/lint.ps1` passed; `scripts/test.ps1` passed **280 tests, 5 deselected**. One Nuitka build completed in **520.095 s**; packaged self/inference/UI smoke completed in **31.477 s**; ZIP took **26.482 s**. `dist/VoxType/VoxType.exe` SHA-256 `B61D08538F40409E60B529AAF9C29B83488D77DBA14A37A40445369758C277BE`; `release/VoxType-0.2.4-53ed6bb-win64-portable.zip` SHA-256 `C52ADD3D1D01978316DDFD6D78282D963128FC24A9F3625E675466DC908CFDA4`.

Inno compilation succeeded in **96.999 s**, producing `release/VoxType-0.2.4-53ed6bb-win64-setup.exe`, SHA-256 `40FD0D9D564404660201585B8AA83834501D5FE8ED24AC6612D3CDE21EF7E0C7`. The old isolated `e7726e0` installation was explicitly authorized for removal and uninstalled; its process, folder and uninstall registration were confirmed absent. The new installer was then installed in separate `build/qa-installed-53ed6bb` (exit 0); installed `--self-test` and visible `--ui-smoke-test` returned 0; uninstall returned 0 and removed its folder and registry entry. A first UI smoke launched with `-WindowStyle Hidden` returned 3 because no visible paint could occur; that invocation is invalid evidence and was corrected by a visible run. The working AppData installation was not modified. Computer Use customer journey, RU/EN capture and insertion, hotkeys, tray and overlay remain **unverified** for this candidate. The Inno compiler reports **“Non-commercial use only”**; commercial licensing must be resolved before sale. No commercial release approval or valuation follows from this build.

## Candidate `e7726e0`, 2026-11-07

Application source/packaging commit `e7726e0`, clean at build start. Ruff/mypy passed; **268 tests passed, 5 deselected**. One full Nuitka + Inno build completed; packaged self-test, inference smoke, visible UI smoke, isolated installer install/self-test/uninstall succeeded. Portable EXE SHA-256 `29EA16F79762272684FFDB8721E9298ADFF9474195A88480A14165B136C1EB34`; portable ZIP SHA-256 `DDF115657DAE67B6C7CABECCBB6ADFE788E8379DE6A34C8A161DE0FCE5CF5F36`; installer SHA-256 `3311FF9E661F5F1FEF9F452924F233A2C892A31F3DCBD72A5299EDCC0DC3FCCB`. Names include `e7726e0` and old release files were preserved.

**Still NOT READY.** The packaged GUI smoke logged a roughly 1.08 s Qt event-loop stall during first-run onboarding. The binary has not had an interactive installed-EXE customer journey, ten RU/EN capture-path tests, application insertion matrix, 100 start/exit cycles or 60-minute soak. The local Inno compiler again printed “Non-commercial use only”; check its official commercial licensing before sale. Automated install/uninstall is not a substitute for Computer Use acceptance.

## Historical candidate, 2026-11-06

**NOT READY** on 2026-11-06. Latest source and packaging commit: `4167c4eae5dd9c2e61841cac8ba4298573ec8ba6`; application EXE payload built from `9104e3a`, SHA-256 `824eae37ac91905c4cb193bd5a4e7439fae839d385bc8a7edd547db6a90e16a9`. Production-AppId installer candidate: `build/final-acceptance/VoxType-0.2.4-4167c4e-release-candidate-setup.exe`, 81,017,015 bytes, SHA-256 `c254540e176af815c4539f9ae20750bca3d4e753a5eb2119c58c23a64248ceef`. Its production-AppId installation was **not** tested, to preserve the existing installation.

Ruff, mypy and 261 selected tests passed before this candidate was built; five tests were deselected. Native Qt emitted `0x8001010d` despite test process exit 0. The isolated package guarded against uninstall while app ran and then removed installed files. The current production package has not completed the buyer journey, ten speech scenarios, insertion app matrix, 100 start/exit cycles, 60-minute soak or production upgrade/uninstall. The Inno compiler printed “Non-commercial use only”; sale licensing is unresolved.
