# Installer acceptance

Production installer source uses AppId `{5A4A7C70-50ED-4F41-90A6-48C69682B1A7}`. This machine already contains a registered VoxType 0.2.4 test installation with that AppId at `build/stability-audit/installed-final-earlywatch-20261105`. Running a same-AppId install/uninstall could alter that installation. The acceptance installer therefore changed **only packaging identity and paths** in a generated script under ignored `build/final-acceptance`; application binaries came from source commit `401beb1` and were not changed. It uses AppId `{9C02AE4F-4C98-4BDE-9072-53919401BEB1}` and an isolated default install directory and shortcuts.

| Artifact | Bytes | SHA-256 | Status |
|---|---:|---|---|
| `build/final-acceptance/nuitka2/__main__.dist/VoxType.exe` | 49,165,824 | `0489d45f940469850a77bb11f80b2cb09c91865c8367537b017be836ea251e61` | Built; self-test exit 0 |
| `build/final-acceptance/VoxType-0.2.4-401beb1-acceptance-setup.exe` | 81,011,610 | `47fd08797a850241ed670018b30c4367df203e9b554f518c5e40655d01322162` | Built and installed through Computer Use; now superseded by source-only FA-005 fix |

The compiler identified itself as **Inno Setup 6.7.3, “Non-commercial use only”**. [Inno Setup's commercial license page](https://jrsoftware.org/isorder.php) asks commercial users to purchase a license; its [terms](https://jrsoftware.org/isorder-terms.php) say purchase is not expected before installers are ready for production. Commercial licensing must be resolved before sale. An isolated install can test wizard usability and executable behavior; it cannot prove same-AppId production upgrade/uninstall behavior.

Computer Use completed the isolated installer on 2026-11-05. It selected the desktop shortcut and clicked Install and Finish. The installed EXE at `C:\Users\Алла\AppData\Local\Programs\VoxType-Acceptance-401beb1\VoxType.exe` was 49,165,824 bytes and SHA-256 `0489d45f940469850a77bb11f80b2cb09c91865c8367537b017be836ea251e61`, exactly matching the candidate. The desktop shortcut `VoxType Acceptance 401beb1.lnk` was present. Installer pages were mostly English with some Russian task labels (FA-003). Start menu shortcut, uninstall, and production AppId upgrade remain unverified.

The installed test copy remains on this machine. Its removal through the Windows UI is pending; the Computer Use confirmation policy requires separate action-time approval before that uninstall. No uninstall has been claimed. Any replacement installer must be rebuilt from the source fix and retested before certification.
