# Fault injection matrix

| FAULT | OBSERVABLE EXPECTATION | EVIDENCE | STATUS |
|---|---|---|---|
| no microphone / enumeration failure | no crash, actionable device message | planned device test | GAP |
| microphone busy/permission denied | capture stops safely, state recoverable | planned capture fake | GAP |
| microphone unplug/callback exception | fatal capture error surfaced; no silent loss | capture callback tests | PASS |
| PortAudio overflow | dropped-frame counter cannot remain zero | queue/spool test | PASS |
| missing model | no implicit download; user sees required model | backend guard | PASS |
| corrupt/tampered model | verification rejects it | model-manager test | PASS |
| model checksum mismatch | staging is not published | model-manager test | PASS |
| Xet/download stall | watchdog/fallback path | ModelManager fallback test | PASS |
| model load hang | one queued start; watchdog error | controller integration | PASS |
| ASR worker exception | audio retained and tail recovered | controller integration | PASS |
| ASR out-of-order/replay | monotonic commit; replay rejected | merger/controller tests | PASS |
| disk spool corruption | explicit corruption error, no unsafe deserialize | spool test | PASS |
| disk full/rename failure | existing durable data remains | planned fault test | GAP |
| settings invalid JSON | backup and defaults | settings test | PASS |
| settings interrupted replace | previous file survives | planned replace fault | GAP |
| database locked | transaction fails without silent corruption | planned repository test | GAP |
| malformed history row | bad data cannot leak stale detail | planned repository/UI test | GAP |
| clipboard temporarily locked | bounded retries | clipboard backend tests | PASS |
| clipboard externally changed | user content is not overwritten | clipboard/injection tests | PASS |
| empty/huge/Unicode clipboard | restore and no encoding loss | property/native tests planned | GAP |
| target inactive | locked FIFO queue | injection test | PASS |
| target closed/reused HWND | no retargeting | target tests | PASS |
| target elevated/unknown integrity | safe block/copy | target/injection tests | PASS |
| partial native paste | no duplicate fallback | injection test | PASS |
| physical modifier held | queue instead of unsafe fallback | injection test | PASS |
| power suspend/resume | lifecycle callbacks ordered | power tests | PASS |
| callback/listener exception | other listeners continue | power/state tests | PASS |
| quit during worker activity | bounded shutdown and durable audio | controller tests; more planned | GAP |
| corrupt journal/session | committed rows remain queryable | recovery test planned | GAP |
| export path/filename abuse | cannot escape chosen destination | path property test planned | GAP |
