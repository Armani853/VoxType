# Repository guidance

- Production package lives in `src/voxtype`; heavy dependencies are lazy at the
  UI/controller boundary where practical.
- Never perform VAD, ASR, UI updates, or filesystem I/O inside the PortAudio
  callback.
- Every queue must be bounded or explicitly disk-backed and preserve FIFO order.
- Committed transcript is journaled before injection; injection failure must not
  lose text.
- Do not log transcript text or PCM in technical logs.
- Run `scripts/lint.ps1` and `scripts/test.ps1` before packaging.
- Do not add cloud STT, telemetry, local HTTP servers, or executable voice
  commands.
