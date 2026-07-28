# Changelog

## 0.2.1

- Fixed microphone calibration rejecting quiet but clearly separated speech:
  calibration now reports explicit result states, keeps completed progress at
  100%, and uses the same float32 PCM/device/sample-rate contract as capture.
- Fixed History filters leaving a hidden session in the detail pane, added a
  strict visible-selection invariant and made the base table fit without a
  horizontal scrollbar.
- Made first-run pages more compact with an exact step indicator, simplified
  privacy text, human-friendly mode names and useful listening/processing text.
- Made low but usable microphone levels visible on the main, settings and
  overlay meters through a dBFS display mapping without changing ASR PCM.
- Made Balanced Local the first-run default and added an isolated data/cache
  override used to verify a truly clean profile without touching user data.
- Hardened model installation against Hugging Face/Xet stalls with bounded HTTP,
  progress reporting and a direct HTTPS fallback inside the existing atomic
  marker/rename state machine.
- Verified the daily-use pipeline with a 5-minute 300-chunk run, deterministic
  SAPI RU/EN, crash recovery, hotkey/target/clipboard regression tests and an
  installer install/self-test/uninstall smoke.

## 0.2.0

- Fixed stateful Silero VAD input context so real microphone speech reaches ASR.
- Added bounded confirmed context, adaptive one-pass model cascade, and richer
  runtime latency/RTF/retry diagnostics.
- Integrated the measured 15/25-second segmentation profile and reproducible
  private pseudo-WER evaluation tooling.
- Froze Windows production profiles: Fast=small, Balanced=small with a lazy
  suspicious-segment turbo retry, Maximum Accuracy=large-v3-turbo, raw audio,
  0.8-second overlap and explicit RU/EN.
- Recorded honest pseudo-reference limitations: Russian pseudo-WER 28.402%
  overall and 25.552% held-out; no 90–95% accuracy claim.

## 0.1.0

- Первый локальный Windows-релиз VoxType.
- Непрерывный bounded audio pipeline с disk spool и forced segmentation.
- faster-whisper CPU INT8, явные RU/EN, committed transcript merge.
- Locked target, clipboard/Unicode/safe-copy стратегии и глобальные hotkeys.
- PySide6 main window, overlay, tray, onboarding, history и diagnostics.
- SQLite recovery journal, rotating privacy-safe logs и 60-minute soak harness.
- Inference watchdog, custom vocabulary и безопасное восстановление clipboard.
- Реальная Windows-матрица вставки Notepad/Word/Chrome/VS Code/Telegram.
