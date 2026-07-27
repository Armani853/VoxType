# Changelog

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
