# Release checklist

- [x] Version/changelog updated.
- [x] No `TODO`, `pass`, `NotImplementedError` in production runtime.
- [x] No transcript/PCM/cloud telemetry in technical logging.
- [x] `ruff format --check`, `ruff check`, `mypy`, `pytest` pass.
- [x] Integration WAV/spool/worker-recovery tests pass.
- [x] Real Notepad/clipboard/target smoke passes in desktop session.
- [x] Real Word/Chrome/VS Code/Telegram insertion matrix passes 4/4.
- [x] Real model benchmark records model, compute type, RTF and RAM.
- [x] 60-minute wall-clock soak passes and report is attached.
- [x] Nuitka one-folder build and packaged self/inference/UI smoke pass.
- [!] Portable ZIP/checksum exists; launch on a physically separate clean PC not checked.
- [x] Inno Setup installer/checksum and install/self-test/uninstall smoke pass.
- [x] No fabricated signature; unsigned limitation documented.
- [x] Independent race/privacy/packaging review performed and P0 findings resolved.
- [x] Final Git tree clean and release commit recorded.
