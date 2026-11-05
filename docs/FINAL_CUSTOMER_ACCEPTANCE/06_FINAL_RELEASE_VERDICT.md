# Final release verdict

**NOT READY — acceptance incomplete.** Candidate source commit `401beb14e4c5068d4f213087978ddf4402ce82b1`; version 0.2.4; standalone EXE SHA-256 `0489d45f940469850a77bb11f80b2cb09c91865c8367537b017be836ea251e61`. Isolated acceptance installer SHA-256 `47fd08797a850241ed670018b30c4367df203e9b554f518c5e40655d01322162`.

The isolated installer and installed EXE were exercised through Computer Use. The first run painted; the microphone and local model were detected; Settings tabs opened; one real capture traversed Listening → Processing → Ready; and two exact-path installed launch/X-exit cycles ended without a lingering process. The first-run wizard's RU/EN phrase checks could not be completed without controlled voice input. Only 2/100 required cycles were run. The target-app insertion matrix, failure recovery, quantified performance, full UI inventory, production-AppId upgrade, and uninstall remain unverified. A stale queue display was found in the installed candidate and fixed in source (FA-005), so the tested EXE no longer represents the latest source.

Lint and 259 selected tests passed both before and after that source fix; a Windows `0x8001010d` diagnostic appeared during both runs. The historical blank-window/AppHang report remains open. The acceptance installer is isolated from the production AppId, so it cannot establish production upgrade safety. Inno compiler commercial-license status requires resolution. **Do not sell this candidate as a finished release.**

Commercial valuation (`07_COMMERCIAL_VALUATION.md`) is intentionally deferred until technical and customer acceptance pass, as explicitly requested by the user.
