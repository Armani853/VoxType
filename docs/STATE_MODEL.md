# VoxType deterministic state model

The production state set is the authoritative `AppState` enum. The QA model adds
user actions and expected safe no-op behavior without changing production logic.

| STATE | VALID OUTCOMES |
|---|---|
| STARTING | MODEL_LOADING, IDLE, FINISHING, ERROR_FATAL, SHUTTING_DOWN |
| MODEL_LOADING | IDLE, FINISHING, ERROR_RECOVERABLE, ERROR_FATAL, SHUTTING_DOWN |
| IDLE | STARTING, MODEL_LOADING, LISTENING, ERROR_RECOVERABLE, ERROR_FATAL, SHUTTING_DOWN |
| LISTENING / SPEECH_DETECTED / TRANSCRIBING | other active state, FINISHING, recoverable/fatal error, shutdown |
| PAUSED / PAUSED_TARGET_LOST | listening/target-lost, finishing, recoverable/fatal error, shutdown |
| FINISHING | COMPLETED, IDLE, recoverable/fatal error, shutdown |
| COMPLETED | STARTING, IDLE, ERROR_FATAL, SHUTTING_DOWN |
| ERROR_RECOVERABLE | IDLE, STARTING, LISTENING, FINISHING, ERROR_FATAL, SHUTTING_DOWN |
| ERROR_FATAL | SHUTTING_DOWN |
| SHUTTING_DOWN | terminal |

Actions modelled: START, STOP, PAUSE, RESUME, SWITCH_LANGUAGE, TARGET_LOST,
TARGET_RETURNED, NEW_AUDIO, ASR_RESULT, ASR_ERROR, INSERT_SUCCESS, INSERT_FAIL,
COPY, CLOSE_OVERLAY, OPEN_HISTORY, DELETE_SESSION, QUIT, MODEL_READY and
MODEL_ERROR.

Invariants checked after every generated action:

1. State is always a valid `AppState`.
2. Committed sequence IDs are strictly increasing and unique.
3. Delivered IDs are a subset of committed IDs and cannot be delivered twice.
4. Failed delivery retains the committed fragment.
5. Language is always explicit RU or EN.
6. No audio is accepted after FINISHING/COMPLETED/SHUTTING_DOWN.
7. SHUTTING_DOWN is terminal.
8. Invalid user actions are safe no-ops in the abstract user-action model.

The deterministic suite uses fixed seeds and executes at least 10,000 generated
actions plus every six-action user sequence through length five.
