# UX benchmark

Baseline values are recorded only when measured. Runtime ASR latency is not
re-measured unless the frozen audio/ASR pipeline changes.

| TASK | METHOD | TARGET | BASELINE | STATUS |
|---|---|---:|---:|---|
| Installed process to Qt smoke close | installed `--ui-smoke-test` | responsive | 3.111 s including startup and 750 ms timer | PASS |
| Hotkey visual response | 20 deterministic dispatches | p95 < 200 ms release gate | pending | GAP |
| Pause / resume / stop state response | controller/UI timestamps, 20 each | p95 < 200 ms | pending | GAP |
| History open | populated test DB, 20 opens | <300 ms desirable | pending | GAP |
| Settings open | 20 opens | <300 ms desirable | pending | GAP |
| Clipboard copy | 20 in-memory/native operations | p95 < 200 ms | pending | GAP |
| UI event-loop stall | Qt heartbeat | no unexplained >500 ms | pending | GAP |
| Insertion reliability | 90 controlled attempts | >=99%, lost/duplicate 0 | pending | GAP |
| DB history operations | 100/1k/10k sessions | no UI-blocking regression | pending | GAP |
| Resource cycling | 100 session/history/settings/overlay cycles | no linear thread/handle/RAM growth | pending | GAP |

Task-step baseline:

| USER TASK | MINIMUM ACTIONS |
|---|---:|
| Ready → start/stop | one hotkey for each transition |
| Switch RU/EN | one hotkey or one segmented-control action |
| Pause/resume | one hotkey per transition |
| Failed insert → safe copy | one visible Copy action |
| Find history record | open History, filter/search, select |
| Change microphone | open Settings, choose device, Save |
