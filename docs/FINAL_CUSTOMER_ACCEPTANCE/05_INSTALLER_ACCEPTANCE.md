# Installer acceptance

Production installer source uses AppId `{5A4A7C70-50ED-4F41-90A6-48C69682B1A7}`. This machine already contains a registered VoxType 0.2.4 test installation with that AppId at `build/stability-audit/installed-final-earlywatch-20261105`. Running a same-AppId install/uninstall could alter that installation. The acceptance installer therefore changed **only packaging identity and paths** in a generated script under ignored `build/final-acceptance`; application binaries came from source commit `401beb1` and were not changed. It uses AppId `{9C02AE4F-4C98-4BDE-9072-53919401BEB1}` and an isolated default install directory and shortcuts.

| Artifact | Bytes | SHA-256 | Status |
|---|---:|---|---|
| `build/final-acceptance/nuitka2/__main__.dist/VoxType.exe` | 49,165,824 | `0489d45f940469850a77bb11f80b2cb09c91865c8367537b017be836ea251e61` | Built; self-test exit 0 |
| `build/final-acceptance/VoxType-0.2.4-401beb1-acceptance-setup.exe` | 81,011,610 | `47fd08797a850241ed670018b30c4367df203e9b554f518c5e40655d01322162` | Built and installed through Computer Use; now superseded by source-only FA-005 fix |

The compiler identified itself as **Inno Setup 6.7.3, “Non-commercial use only”**. [Inno Setup's commercial license page](https://jrsoftware.org/isorder.php) asks commercial users to purchase a license; its [terms](https://jrsoftware.org/isorder-terms.php) say purchase is not expected before installers are ready for production. Commercial licensing must be resolved before sale. An isolated install can test wizard usability and executable behavior; it cannot prove same-AppId production upgrade/uninstall behavior.

Computer Use completed the isolated installer on 2026-11-05. It selected the desktop shortcut and clicked Install and Finish. The installed EXE at `C:\Users\Алла\AppData\Local\Programs\VoxType-Acceptance-401beb1\VoxType.exe` was 49,165,824 bytes and SHA-256 `0489d45f940469850a77bb11f80b2cb09c91865c8367537b017be836ea251e61`, exactly matching the candidate. The desktop shortcut `VoxType Acceptance 401beb1.lnk` was present. Installer pages were mostly English with some Russian task labels (FA-003). Start menu shortcut, uninstall, and production AppId upgrade remain unverified.

After action-time user confirmation, Computer Use launched `unins000.exe` from the isolated installation, observed the Inno uninstall confirmation, and chose Yes. The old acceptance install directory, its desktop shortcut, and its HKCU Uninstall AppId entry were absent afterward; the previous stability-audit EXE still existed. The UI state refresh raced with the uninstall window closing, so completion was verified through the exact file and registry paths. This uninstall applies only to the `401beb1` isolated test copy.

## Replacement candidate `b598039`

Source/packaging commit `b598039f10c32f00fa1e1c86c86fe91c1e12798e` contains FA-005 and the Russian installer language. `scripts/lint.ps1` passed, `scripts/test.ps1` reported 259 passed/5 deselected with the open Windows `0x8001010d` diagnostic, and the new packaged EXE passed self-test, inference smoke, and UI smoke (all exit 0). The replacement candidate is in the ignored acceptance build directory:

| Artifact | Bytes | SHA-256 |
|---|---:|---|
| `build/final-acceptance/nuitka3/__main__.dist/VoxType.exe` | 49,168,896 | `e6ec8c868bb006e3e0a1ac2eac9ec09c3c1573c51326a86dd80f578ff4644faf` |
| `build/final-acceptance/VoxType-0.2.4-b598039-acceptance-setup.exe` | 81,011,269 | `5238250873a28ccdcd51b2fbee0f6ce4511b0786560a17f81ece2bf28510f849` |

The replacement script uses AppId `{F46B56BA-7CFD-4E4B-91C8-B59803900024}` and an isolated install directory. After separate action-time user confirmation, Computer Use traversed the Russian destination, tasks, summary, progress, and finish screens and installed it at `C:\Users\Алла\AppData\Local\Programs\VoxType-Acceptance-b598039`. The installed EXE SHA-256 matched `e6ec8c868bb006e3e0a1ac2eac9ec09c3c1573c51326a86dd80f578ff4644faf`. The first-run GUI painted. This test copy was subsequently uninstalled through Computer Use; its directory and shortcut were absent. The installed binary predates the subsequent FA-007 source change; it is not the latest release candidate.

## `9104e3a` and running-app removal defect

The installed `9104e3a` EXE SHA-256 was `824eae37ac91905c4cb193bd5a4e7439fae839d385bc8a7edd547db6a90e16a9`; its original isolated setup SHA-256 was `1030dfbac3dde51f427c037cc098b3aaf3e21717d240b373db2c3250e8abb944`. Computer Use installed and launched this candidate. While its process was running, the original UI uninstaller continued and reported partial removal. The EXE and DLLs remained (FA-010).

The production Inno script now has `AppMutex=Local\VoxType.Desktop.Singleton.v1` and a Russian message directing the user to exit through the tray. A temporary acceptance script used the **same isolated AppId**, directory and `9104e3a` payload to repair that partial installation. Its compiled setup was 81,017,033 bytes, SHA-256 `4961243f8cc4f757eb07680114c00749777b9770fe14adbf93f72b599766c059`.

Computer Use reinstalled the test package in the same folder, launched the installed app, and confirmed uninstall. The new uninstaller stopped with the specified running-app warning before file removal. After the exact test process was stopped, clicking OK produced Inno's complete-removal message. The EXE and `unins000.exe` were absent afterward. Twelve **empty** dependency subdirectories remain under `VoxType-Acceptance-9104e3a`; no installed files remain there. Automatic approval review rejected a separate recursive shell removal of the old residual directory, so that command was not retried. This result verifies the guard in the isolated test AppId; it does not establish production-AppId upgrade or removal behavior.

Computer Use also removed the older `7d9762b` isolated installation while its app was not running. Inno reported complete removal; its exact install directory is absent. The previous stability-audit installation was untouched.
