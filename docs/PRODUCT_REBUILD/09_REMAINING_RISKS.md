# VoxType — remaining risks

Updated 2026-11-05. Release remains **NOT APPROVED**.

1. `VX-P0-001`: intermittent user-observed blank/white GUI and AppHang. Prior repeat series did not reproduce it. Root cause and current real-world frequency unknown.
2. `VX-P1-001`: missing-model recovery has source changes but no clean-profile acceptance on a rebuilt EXE yet.
3. `VX-P1-003`: audio settings layout source fix awaits packaged Computer Use retest.
4. `VX-P2-001`: history open p95 crossed the benchmark threshold in two of three measurements; controlled profiling is needed before judging typical latency.
5. Live microphone speech, RU/EN recognition, text insertion in target apps, tray, overlay, hotkeys, focus changes, and DPI have not been accepted on the current code.
6. Current installer, upgrade/uninstall, and a physically clean Windows PC have not been accepted. No signed release is claimed.
7. `scripts/test.ps1` emitted a Windows `0x8001010d` diagnostic during GUI benchmark execution even when pytest completed with exit 0. Its relationship to product behavior is unknown and must not be treated as a proven application crash or dismissed as harmless without investigation.

Source tests, portable smoke, and one GUI path provide useful evidence for the fixes listed in `06_TEST_MATRIX.md`; they do not close these risks.
