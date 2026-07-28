# VoxType post-release acceptance

| CHECK | STATUS | EVIDENCE | TIME |
|---|---|---|---|
| Current release audit | PASS | HEAD `3774da1`; installed and portable EXE report 0.2.0; 0.2.0 ZIP/setup present | 2026-07-28 18:00 MSK |
| Stale VoxType processes | FIXED | Four old installed-EXE processes identified by path/command line and stopped before acceptance | 2026-07-28 18:01 MSK |
| Clean first run (isolated source runtime) | PASS | Empty settings/session/history/models; onboarding visible; 8 microphones; CPU/RAM detected; RU + Balanced Local default; clean exit | 2026-07-28 20:12 MSK |
| ModelManager fresh profile | PASS | Existing cached `small` registered through production staging/marker/atomic rename; 486220136 bytes; marker present; verify/load PASS; no staging residue | 2026-07-28 20:13 MSK |
| Onboarding second launch | PASS | Completed flag persisted; onboarding not shown again | 2026-07-28 20:13 MSK |
| Hugging Face/Xet protection | PASS | Xet disabled; 15 s metadata and 30 s transfer bounds; progress callback; direct HTTPS fallback; targeted tests PASS | 2026-07-28 20:10 MSK |
| Physical acoustic SAPI → speakers → microphone | NOT AVAILABLE | No clipping, but RU separation -6.64 dB and EN 5.68 dB (< 6 dB acceptance); stopped without changing system volume | 2026-07-28 20:20 MSK |
| SAPI deterministic production pipeline | PASS | RU and EN exact phrases: capture dispatcher → raw → Silero VAD → 15/25/0.8 segmenter → small ASR → guard → merger; 1 segment each, dropped=0, queues empty | 2026-07-28 20:21 MSK |
| Five-minute continuous harness | PASS | 302.157 s; 300/300 chunks; 11/11 criteria; 19 segments, 6 forced; FIFO/journal/queue/spool PASS; dropped=0; duplicates=0; p50=1.078 s, p95=2.750 s | 2026-07-28 20:23 MSK |
| High Accuracy Online | SKIPPED (POLICY) | Existing Voice-assistant Groq code was inspected, but repository `AGENTS.md` explicitly prohibits cloud STT; no provider code or credential is shipped | 2026-07-28 20:26 MSK |
| Online failure fallback | NOT APPLICABLE | Online mode is not present; Local Balanced remains fully offline | 2026-07-28 20:26 MSK |
| Crash committed-text recovery | PASS | Hard-exit code 23 after 3 journal commits; new process recovered exact `Первый второй третий.` and finalized session | 2026-07-28 20:22 MSK |
| Autostart | PASS | OFF; user-level HKCU Run enable command verified, then disabled and value removed | 2026-07-28 20:22 MSK |
| Human-reference editor | PASS | Qt editor opened two SAPI WAV/reference pairs and rendered editable reference text | 2026-07-28 20:23 MSK |
| Current Windows native smoke | PARTIAL | 1 PASS; 3 Notepad checks skipped because this automation session could not activate the interactive Notepad window; retain 0.2.0 4/4 insertion evidence and recheck new installed EXE | 2026-07-28 20:21 MSK |
| Calibration 0.2.0 reproduction | PASS | Old RMS gate `0.003` rejected speech RMS `0.0006026` (-64.4 dBFS) despite 16.3 dB margin; failed UI reset completed progress to 0% | 2026-07-28 21:03 MSK |
| Calibration 0.2.1 regression | PASS | Same -80.7/-64.4 dBFS case is `QUIET_BUT_USABLE`; progress stays 100%; float32/int16/multichannel/silence/noise/clipping tests PASS | 2026-07-28 21:12 MSK |
| History stale detail / layout | PASS | EN filter selects EN detail; empty search clears detail; delete selects next; invariant asserted; horizontal scrollbar disabled with Qt resize policies | 2026-07-28 21:12 MSK |
| First-run / main blocking UX | PASS (AUTOMATED) | 740x540 wizard; `Шаг N из 8`; compact privacy copy; mode-first card; listening/processing labels; dBFS meter at 20 Hz | 2026-07-28 21:12 MSK |
| Final source checks | PASS | Ruff format/check PASS; Mypy 88 files PASS; Pytest 191 passed, 5 deselected; final calibration/onboarding targeted 13 passed | 2026-07-28 21:16 MSK |
| Portable 0.2.1 | PASS | Nuitka 503.8 s; packaged self-test, inference/VAD/model and Qt smoke PASS | 2026-07-28 21:25 MSK |
| Installer 0.2.1 | PASS | Inno 146.4 s; isolated install/self-test/uninstall PASS; real per-user upgrade exit 0 | 2026-07-28 21:30 MSK |
| Installed 0.2.1 | PASS | File/product 0.2.1.0; version/self-test/inference/Qt/diagnostics exit 0; 8 input devices; estimated startup ready 2.0 s | 2026-07-28 21:32 MSK |
| Upgrade data preservation | PASS | Settings and sessions SHA-256 unchanged; `small`, `large-v3-turbo`, `large-v3`, `silero-vad` retained | 2026-07-28 21:32 MSK |
| Release cleanup | PASS | No private audio/transcripts/logs/dumps/staging/screenshots; no exact private paths or credential assignments | 2026-07-28 21:34 MSK |
| Release SHA-256 | PASS | ZIP `467618769881A979F84A992A776518DC89744B9A301535474083C9512402238E`; setup `7DC4F2B127D4DDC364C9F5C08C0733962C8573A10E7B342E9AD4D83961EF9298` | 2026-07-28 21:34 MSK |
| Post-fix ordinary human voice | MANUAL CHECK | Controlled live human speech is unavailable to the agent; confirm GOOD/QUIET_BUT_USABLE and visible meter once | 2026-07-28 21:34 MSK |
| 0.2.2 immediate command states | PASS | Slow-preparation Start returned <50 ms in regression test; Pause/Stop state changes immediate; Stop timer frozen; repeated Start/Stop ignored | 2026-07-28 21:31 MSK |
| 0.2.2 pause boundary | PASS | Callback chunk timestamped after Pause was acknowledged without reaching segmenter/ASR; model remained loaded across Resume | 2026-07-28 21:31 MSK |
| 0.2.2 delivery/result state | PASS | Journal precedes bounded single-worker FIFO; fragment IDs suppress duplicates; uncertain SendInput is not auto-retried; full transcript persists in COMPLETED with Copy/Retry/Close | 2026-07-28 21:34 MSK |
| 0.2.2 five-minute continuous harness | PASS | 302.125 s; 300/300 chunks; 11/11 criteria; FIFO/journal/queue/spool PASS; dropped=0; duplicates=0; p50=1.078 s; p95=2.750 s | 2026-07-28 21:36 MSK |
| 0.2.2 Windows native insertion | PASS | Installed/debug interactive Notepad matrix 4/4: Locked Target, Follow Focus, clipboard restore, SendInput fallback; empirical clipboard settle floor 350 ms | 2026-07-28 22:12 MSK |
| 0.2.2 final source checks | PASS | Ruff format/check PASS; Mypy 88 files PASS; Pytest 199 passed, 5 deselected | 2026-07-28 22:20 MSK |
| Portable 0.2.2 | PASS | One final Nuitka build; packaged smoke PASS; ZIP published without replacing 0.2.0/0.2.1 | 2026-07-28 22:41 MSK |
| Installer 0.2.2 | PASS | Inno build with existing portable; per-user smoke-install PASS; real upgrade exit 0 | 2026-07-28 22:45 MSK |
| Installed 0.2.2 | PASS | File/product 0.2.2.0; version, self-test, Qt, diagnostics/model discovery, VAD/inference all exit 0; 8 audio inputs | 2026-07-28 22:47 MSK |
| 0.2.2 upgrade data preservation | PASS | Settings `5E752F28...E9AC` and sessions `DCC403CA...DA9B` unchanged; four user model directories retained | 2026-07-28 22:47 MSK |
| 0.2.2 release cleanup | PASS | No OGG/WAV/private transcripts/.env/logs/dumps/staging/screenshots/calibration files or embedded private absolute paths found | 2026-07-28 22:48 MSK |
| 0.2.2 release SHA-256 | PASS | ZIP `04095CB8EBAD209C298D60190D6A78A0DF0CC62DED82971FF1129776CE7867B9`; setup `29D255EB5279C56234238DB6D026055A0EE1ADE89278679589299CDDB9D6D4FA` | 2026-07-28 22:48 MSK |

## Human ground truth procedure

Для объективного WER выберите 3–5 минут реальной русской речи, откройте
`tools/review_references.py`, вручную исправьте каждое слово и отметьте reference
как human-reviewed. Только этот текст используйте как ground truth. Текущий
русский reference от другого Whisper-сервиса остаётся pseudo-reference, поэтому
его метрика называется только pseudo-WER.
