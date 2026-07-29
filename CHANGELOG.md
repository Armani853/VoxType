# Changelog

## 0.2.3

- Unified the light-first interface around semantic colors, typography,
  spacing, radii and explicit button roles while retaining the existing
  indigo VoxType identity and native Windows behavior.
- Polished hierarchy and long-text handling across Main, overlays, onboarding,
  calibration, History, Settings and Diagnostics; added consistent focus,
  hover, pressed, disabled, selection and empty states.
- Added multi-resolution runtime and packaged Windows icons for crisp shell,
  tray, taskbar and installer rendering from 16 through 256 px.
- Verified key layouts at 100%, 125% and 150% scaling without changing
  dictation behavior, audio, ASR, delivery or persistence.

## 0.2.2

- Moved session preparation, model loading, microphone startup and shutdown
  waits off the Qt thread; slow handlers and event-loop stalls now produce
  privacy-safe `UI_BLOCK` / `UI_STALL` diagnostics.
- Added immediate `STARTING`, two-phase `FINISHING`, frozen stop time and a
  persistent `COMPLETED` result with full-text Copy, safe retry and Close.
- Added an exact timestamp boundary for Pause/Resume so callbacks captured
  during pause cannot enter a later transcript while the loaded model is kept.
- Routed automatic insertion through one bounded FIFO delivery worker, added
  per-fragment delivery states and idempotency keys, and suppressed automatic
  retry after an uncertain partial `SendInput` result.
- Increased the effective clipboard-paste observation window to the measured
  350 ms before restoring the user's clipboard, while preserving target
  revalidation.
- Made first-run/settings microphone and model discovery asynchronous and added
  default-on full-result clipboard copy with default-off automatic result hide.

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
