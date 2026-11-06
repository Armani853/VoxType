# Computer Use results carried forward

Instrument: `@oai/sky` through the available Windows Computer Use skill. It returned live window screenshots and accessibility trees; clicks in the Inno and VoxType windows visibly changed their state. The detailed per-test evidence is in `docs/FINAL_CUSTOMER_ACCEPTANCE/01_COMPUTER_USE_TEST_MATRIX.md` and `05_INSTALLER_ACCEPTANCE.md`.

On the installed `9104e3a` application, first-run GUI painted, RU → EN → RU selection visibly changed, and two tray-restoration activations painted. The exact installed EXE SHA-256 was `824eae37ac91905c4cb193bd5a4e7439fae839d385bc8a7edd547db6a90e16a9`. The isolated repaired uninstaller presented its running-app warning and then removed files after that process was stopped. These do not substitute for 100 launch/exit cycles, controlled speech, target-app insertion, full controls, tray exit, or soak.

The installed test copies `b598039` and `7d9762b` were removed through Computer Use. The `9104e3a` test EXE and uninstaller were removed; twelve empty subdirectories remain after Inno. The previously installed stability-audit copy was preserved. Automatic approval review rejected a separate recursive shell cleanup of the `9104e3a` residual directory, so no second shell deletion was attempted.
