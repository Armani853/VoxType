# VoxType codebase audit

Snapshot 2026-11-05, `main` at `ca978c7` with uncommitted hardening. Inventory command: `.venv\Scripts\python.exe tools\audit_modules.py src\voxtype build\optimization\module_inventory.json`. **78 Python modules, 16,852 physical lines** under `src/voxtype`. Static references below are only leads for human review: Qt signals, lazy imports, and Nuitka hidden imports can make live code appear unused. No module is marked dead from this table.

## Architecture and confirmed issues

- `runtime.py` composes local audio, ASR, VAD, persistence, and Windows services. `app.py` owns Qt orchestration; `core/controller.py` owns session state and worker lifetime. The boundaries are useful, but both orchestration modules exceed 1,400 lines and contain GUI-sensitive lifecycle paths.
- Confirmed: `controller.py` held its lock while starting PortAudio. A fault-injection test measured about 360 ms `snapshot()` delay with a 350 ms blocked capture. Opening capture outside the lock and checking cancellation afterwards removed this delay in the targeted test.
- Confirmed: onboarding treated valid PortAudio index `0` as missing; rebuilt EXE passed a Computer Use retest. Confirmed: audio settings overflowed horizontally for a long device name; source layout test passes, packaged retest pending.
- Independent source review identified unresolved risks: native inference worker can outlive its watchdog and block backend unload; resume may start a second capture after a timed-out stop; settings save can synchronously enumerate PortAudio on the GUI thread; overlay repolishes a button on every poll; injection tracks fragment IDs for the service lifetime. These are audit findings, not measured production failures yet.
- No source module, test, compatibility layer, or dependency has been deleted. Further profiling and runtime checks are required before simplification.
- Confirmed on this pass: saving settings called `list_input_devices()` synchronously on the Qt thread after persisting settings. It now reuses the completed device scan, and the scan callback refreshes the cache. A focused test fails if this path queries PortAudio; packaged GUI retest is pending.

## Static analysis and coverage

`scripts/lint.ps1` passes Ruff formatting, Ruff checks, and strict Mypy (97 checked files). Vulture 2.16 at 80% confidence reported only six unused `exc_type` parameters in `__exit__` methods; these are protocol parameters, not dead features. No automatic deletion was warranted. `coverage run -m pytest -q` passed 259 tests, skipped four interactive Windows tests, and deselected one soak test. Coverage of measured `src/voxtype` statements was 76% (10,103 statements); `app.py` measured 16%, `runtime.py` 41%, and `controller.py` 77%. These numbers describe exercised source lines, not end-user readiness. Qt signals and packaged behavior need independent runtime evidence.

The environment has Vulture, Radon, and Mutmut installed as extra dev tools; the runtime dependency list is in `pyproject.toml`. PySide6 includes large WebEngine files in the development environment, but the portable inventory must be inspected separately before claiming they are shipped. `ctranslate2`, `av`, `onnxruntime`, and Qt native DLLs are required by local ASR, VAD, and UI paths; name/size alone cannot justify exclusion.

Computer Use on the existing portable EXE verified the audio-settings layout. With the existing enabled “minimize to tray” preference, clicking the main-window X hid the window while PID 26900 remained alive. This is the configured behavior and was not an observed AppHang. It does not explain the separately recorded historical Windows AppHang events.

## Module inventory

| Module | Lines | Declared purpose | Static importers | Test files naming module |
|---|---:|---|---:|---:|
| `voxtype.__init__` | 5 | VoxType: local-first continuous dictation for Windows. | 0 | 0 |
| `voxtype.__main__` | 130 | VoxType command-line and desktop entry point. | 0 | 0 |
| `voxtype.app` | 1411 | PySide6 desktop application shell for VoxType. |  | 0 |
| `voxtype.asr.__init__` | 50 | Backend-neutral local speech recognition for VoxType. | 0 | 0 |
| `voxtype.asr.base` | 206 | Backend-neutral ASR contracts and immutable transcription models. | 7 | 2 |
| `voxtype.asr.benchmark` | 129 | Deterministic local ASR benchmark and profile selection by measured RTF. | 2 | 1 |
| `voxtype.asr.faster_whisper_backend` | 190 | Lazy faster-whisper/CTranslate2 implementation of the ASR protocol. | 3 | 1 |
| `voxtype.asr.hallucination_guard` | 280 | Explainable hallucination checks and a single bounded retry policy. | 2 | 1 |
| `voxtype.asr.model_manager` | 470 | Explicit, atomic local model installation, verification and removal. | 4 | 2 |
| `voxtype.asr.profiles` | 68 | Local Whisper model/decoding profiles and backlog degradation policy. | 7 | 3 |
| `voxtype.audio.__init__` | 36 | Audio capture and bounded buffering for VoxType. | 0 | 0 |
| `voxtype.audio.calibration` | 227 | Microphone calibration using the same normalized PCM contract as capture. | 2 | 2 |
| `voxtype.audio.capture` | 423 | Real-time-safe microphone ingress with background DSP and disk spillover. | 4 | 3 |
| `voxtype.audio.devices` | 95 | Lazy sounddevice discovery that is safe to import without PortAudio. | 3 | 1 |
| `voxtype.audio.levels` | 89 | Microphone level, clipping and slowly varying noise-floor analysis. | 2 | 1 |
| `voxtype.audio.models` | 81 | Typed audio data shared by capture, buffering, VAD and ASR. | 13 | 3 |
| `voxtype.audio.preprocessing` | 174 | Conservative, stateful speech preprocessing profiles. | 4 | 1 |
| `voxtype.audio.resampler` | 127 | Offline and streaming mono PCM resampling. | 3 | 1 |
| `voxtype.audio.ring_buffer` | 78 | Fixed-capacity mono audio ring buffer. |  | 1 |
| `voxtype.audio.spool` | 488 | Crash-recoverable FIFO PCM spool and a bounded-memory audio queue. | 5 | 5 |
| `voxtype.constants` | 12 | Stable application constants. | 6 | 0 |
| `voxtype.core.__init__` | 6 | Core state and typed contracts. | 0 | 0 |
| `voxtype.core.controller` | 1413 | Lifecycle controller joining capture, segmentation, ASR, journal and injection. |  | 1 |
| `voxtype.core.events` | 57 | Bounded in-process event bus used to decouple workers from the UI. | 0 | 0 |
| `voxtype.core.exceptions` | 33 | Domain exceptions shown as human-readable failures at the UI boundary. |  | 2 |
| `voxtype.core.models` | 135 | Shared immutable domain models without UI or backend dependencies. | 8 | 9 |
| `voxtype.core.state_machine` | 206 | Thread-safe finite state machine for the application lifecycle. | 2 | 4 |
| `voxtype.monitoring.__init__` | 5 | Metrics, logging, and runtime health. | 0 | 0 |
| `voxtype.monitoring.hardware` | 122 | Runtime hardware and PortAudio capability discovery for diagnostics. | 2 | 0 |
| `voxtype.monitoring.health` | 60 | Small deterministic health checks used by diagnostics and packaging smoke tests. | 2 | 0 |
| `voxtype.monitoring.logging` | 32 | Privacy-safe rotating technical logging configuration. | 2 | 0 |
| `voxtype.monitoring.metrics` | 176 | Bounded performance telemetry retained only in memory unless exported. | 2 | 1 |
| `voxtype.runtime` | 371 | Composition root for the local VoxType runtime. | 2 | 1 |
| `voxtype.sessions.__init__` | 6 | Durable local session journal and recovery. | 0 | 0 |
| `voxtype.sessions.journal` | 33 | Ordered session journal facade used by the controller. | 2 | 3 |
| `voxtype.sessions.recovery` | 11 | Crash recovery queries with no mutation until the user finishes recovery. | 0 | 1 |
| `voxtype.sessions.repository` | 376 | SQLite session storage with WAL, transactions, and no technical-log text leakage. | 6 | 7 |
| `voxtype.sessions.retention` | 44 | Retention policy for completed sessions and temporary audio directories. |  | 1 |
| `voxtype.settings.__init__` | 6 | Versioned application settings. | 0 | 0 |
| `voxtype.settings.defaults` | 12 | Environment-aware but deterministic settings defaults. | 2 | 0 |
| `voxtype.settings.migration` | 26 | Pure migrations for persisted settings dictionaries. |  | 1 |
| `voxtype.settings.repository` | 105 | Atomic JSON settings persistence with corrupt-file quarantine. | 3 | 6 |
| `voxtype.settings.schema` | 99 | Dataclass settings schema with explicit enum conversion. | 5 | 6 |
| `voxtype.transcript.__init__` | 6 | Transcript merging, formatting, voice commands, and vocabulary. | 0 | 0 |
| `voxtype.transcript.commands` | 58 | Explicit, disableable punctuation-only voice commands. |  | 1 |
| `voxtype.transcript.formatter` | 73 | Language-neutral spacing and optional sentence capitalization. | 3 | 2 |
| `voxtype.transcript.merger` | 206 | Time-aware overlap merger for independently transcribed audio chunks. | 2 | 2 |
| `voxtype.transcript.vocabulary` | 72 | Bounded local vocabulary used only as a soft ASR initial prompt. | 2 | 2 |
| `voxtype.ui.__init__` | 58 | PySide6 user interface for VoxType. | 0 | 0 |
| `voxtype.ui._optional` | 17 | Optional PySide6 support shared by all UI modules. | 10 | 0 |
| `voxtype.ui.diagnostics_window` | 233 | Human-readable hardware, runtime and self-test diagnostics. |  | 1 |
| `voxtype.ui.history_window` | 437 | Local dictation history browser. |  | 2 |
| `voxtype.ui.i18n` | 77 | Small built-in translation catalogue for UI chrome and status text. |  | 0 |
| `voxtype.ui.main_window` | 559 | The primary VoxType desktop window. |  | 1 |
| `voxtype.ui.messages` | 38 | Human-facing failure messages without implementation or traceback details. |  | 1 |
| `voxtype.ui.models` | 308 | Typed, Qt-independent view models used by the VoxType user interface. | 6 | 5 |
| `voxtype.ui.onboarding` | 587 | Eight-step first-run wizard for local dictation setup. |  | 1 |
| `voxtype.ui.overlay` | 472 | Non-activating, draggable dictation overlay for Windows. |  | 3 |
| `voxtype.ui.settings_window` | 834 | Complete settings editor for VoxType. |  | 2 |
| `voxtype.ui.theme` | 585 | VoxType visual system built on native Qt widgets. | 9 | 1 |
| `voxtype.ui.tray` | 197 | System tray presentation and command routing. |  | 1 |
| `voxtype.ui.widgets` | 237 | Reusable presentation-only Qt widgets. | 5 | 2 |
| `voxtype.vad.__init__` | 17 | Voice activity detection and infinite-stream segmentation. | 0 | 0 |
| `voxtype.vad.base` | 83 | Voice-activity interfaces and a dependency-free adaptive energy fallback. | 3 | 1 |
| `voxtype.vad.segmenter` | 395 | Bounded streaming speech segmentation with padding and overlap. | 4 | 3 |
| `voxtype.vad.silero` | 197 | Lazy, strictly local Silero VAD adapter with an energy fallback. | 4 | 1 |
| `voxtype.version` | 3 | Application version kept separate for packaging metadata. | 4 | 0 |
| `voxtype.windows.__init__` | 46 | Windows integration services for VoxType. | 0 | 0 |
| `voxtype.windows._win32` | 55 | Small, import-safe helpers shared by the native Windows adapters. | 6 | 0 |
| `voxtype.windows.clipboard` | 545 | Best-effort multi-format clipboard preservation for short paste leases. | 2 | 3 |
| `voxtype.windows.foreground` | 295 | Capture stable metadata about foreground and target windows. | 3 | 5 |
| `voxtype.windows.hotkeys` | 318 | Native global hotkeys with conflict reporting and autorepeat suppression. | 2 | 1 |
| `voxtype.windows.injection` | 691 | Ordered, target-safe text delivery with clipboard and Unicode fallbacks. | 3 | 3 |
| `voxtype.windows.power_events` | 192 | Decode WM_POWERBROADCAST without taking a dependency on a UI toolkit. | 2 | 1 |
| `voxtype.windows.send_input` | 297 | Layout-independent Unicode and shortcut input through Win32 SendInput. | 3 | 3 |
| `voxtype.windows.single_instance` | 116 | One-owner process guard acquired before constructing the desktop runtime. |  | 1 |
| `voxtype.windows.startup` | 154 | Per-user Windows startup registration, disabled until explicitly enabled. | 2 | 1 |
| `voxtype.windows.target_window` | 291 | Target-window policy for delayed real-time transcription results. | 5 | 4 |

`Static importers = 0` or `test files = 0` is not proof of dead code or missing behavioral coverage. It highlights modules to inspect through the entrypoints, lazy loader paths, Qt signals, packaged smoke, and dynamic call sites before any removal.
