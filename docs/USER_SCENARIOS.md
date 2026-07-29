# VoxType user scenario catalog

Status is evidence-based: `PASS` has a current observable test, `GAP` is
scheduled in this QA pass, and `MANUAL` requires a live user/device/application.

| ID | CATEGORY | PRECONDITION / ACTIONS | EXPECTED RESULT | MODE / TEST | STATUS |
|---:|---|---|---|---|---|
| 001 | NEW USER | isolated profile → launch | first-run opens; no traceback | E2E clean-profile | GAP |
| 002 | NEW USER | privacy page → Next | local/no-cloud message and progress remain clear | UI onboarding pages | PASS |
| 003 | NEW USER | refresh microphones | list updates without blocking UI | integration device fault | GAP |
| 004 | NEW USER | select long microphone name | full value retained; layout intact | UI elision/onboarding | PASS |
| 005 | NEW USER | normal calibration | progress 100%; GOOD/usable | calibration tests | PASS |
| 006 | NEW USER | quiet distinct speech | QUIET_BUT_USABLE; can continue | Realtek regression | PASS |
| 007 | NEW USER | silence calibration | NO_SPEECH; retry path | calibration silence | PASS |
| 008 | NEW USER | model absent | explicit install requirement; no implicit ASR download | backend guard | PASS |
| 009 | NEW USER | cached model install | atomic marker/manifest and ready state | ModelManager fixture | PASS |
| 010 | NEW USER | finish wizard → relaunch | onboarding completion persists | isolated relaunch | GAP |
| 011 | DAILY | external caret → hotkey Start | target locks; LISTENING feedback | target/controller tests | PASS |
| 012 | DAILY | Start button while VoxType focused | last valid external target retained | target regression | PASS |
| 013 | DAILY | speak → natural pause | final segment committed progressively | WAV/controller pipeline | PASS |
| 014 | DAILY | Stop with queued tail | queue drains before Completed | controller drain | PASS |
| 015 | DAILY | Stop twice | second action safe; no duplicate session | state-model planned | GAP |
| 016 | DAILY | Completed → Start | clean new session | state/UI tests | PASS |
| 017 | DAILY | Pause → audio arrives | no committed text while paused | controller pause | PASS |
| 018 | DAILY | Pause → Resume | listening resumes once | controller/state model | PASS |
| 019 | DAILY | switch RU→EN active | current chunk finalized; context reset | language integration | PASS |
| 020 | DAILY | EN→RU→EN lifetime | explicit language and ordered text | language/state model | PASS |
| 021 | DAILY | target loses focus | delayed text stays queued | locked-target tests | PASS |
| 022 | DAILY | original target returns | queued fragments flush FIFO | native target smoke | PASS |
| 023 | DAILY | target closes | transcript retained; no retarget | target identity tests | PASS |
| 024 | DAILY | elevated target | safe block/copy path; no loss | UIPI injection test | PASS |
| 025 | DAILY | completion Copy All | full committed result copied | overlay/controller UI | PASS |
| 026 | FAST ACTIONS | 20 Start actions | one worker/model load/session | action-spam planned | GAP |
| 027 | FAST ACTIONS | 20 Stop actions | no crash/duplicate completion | state-model planned | GAP |
| 028 | FAST ACTIONS | 20 Pause/Resume | valid stable state; no extra workers | action-spam planned | GAP |
| 029 | FAST ACTIONS | 20 RU/EN toggles | explicit final language; ordered context resets | randomized model | GAP |
| 030 | FAST ACTIONS | Start→Stop→Start | first tail durable; second session independent | exhaustive model | GAP |
| 031 | FAST ACTIONS | Stop→Stop→Pause→Quit | invalid actions become safe no-ops | exhaustive model | GAP |
| 032 | FAST ACTIONS | Start→Quit while loading | one bounded shutdown | controller/model load | PASS |
| 033 | FAST ACTIONS | quit while PAUSED | durable committed text; normal close | controller fault planned | GAP |
| 034 | FAST ACTIONS | quit while FINISHING | queue drain or explicit recoverable result | controller test | PASS |
| 035 | FAST ACTIONS | open/close History 100× | no thread/handle growth | resource benchmark | GAP |
| 036 | WINDOW | Notepad locked target | Unicode insertion and clipboard restore | native Windows smoke | PASS |
| 037 | WINDOW | VS Code target | deterministic insertion succeeds | app matrix | MANUAL |
| 038 | WINDOW | browser text field | deterministic insertion succeeds | app matrix | MANUAL |
| 039 | WINDOW | Word target | deterministic insertion succeeds | app matrix | MANUAL |
| 040 | WINDOW | Telegram target | deterministic insertion succeeds | app matrix | MANUAL |
| 041 | WINDOW | overlay appears during Notepad use | overlay never steals focus | native Windows smoke | PASS |
| 042 | WINDOW | Follow Focus enabled | active safe external window becomes target | target test | PASS |
| 043 | WINDOW | reused HWND after app close | old identity rejected | target test | PASS |
| 044 | WINDOW | unknown integrity target | conservatively blocked | target test | PASS |
| 045 | WINDOW | delayed result after new foreground | never retarget to new window | injection test | PASS |
| 046 | LANGUAGE/TEXT | Cyrillic punctuation | spacing/capitalization valid | transcript tests | PASS |
| 047 | LANGUAGE/TEXT | English punctuation | no doubled spaces | formatter property planned | GAP |
| 048 | LANGUAGE/TEXT | mixed Cyrillic/Latin/numbers | characters preserved and ordered | merger test | PASS |
| 049 | LANGUAGE/TEXT | emoji/supplementary Unicode | surrogate pair not split | SendInput tests | PASS |
| 050 | LANGUAGE/TEXT | combining characters | no encoding corruption | property test planned | GAP |
| 051 | LANGUAGE/TEXT | multiline transcript | newlines preserved through queue/injection | injection test | PASS |
| 052 | LANGUAGE/TEXT | empty ASR chunk | next text not lost | merger test | PASS |
| 053 | LANGUAGE/TEXT | 100k-character transcript | bounded usable copy/export | large-text benchmark | GAP |
| 054 | LANGUAGE/TEXT | overlapping forced chunks | overlap removed once | merger/segmenter | PASS |
| 055 | LANGUAGE/TEXT | real repeated phrase without overlap | repetition preserved | merger test | PASS |
| 056 | HISTORY | open populated history | newest rows and detail correspond | history UI test | PASS |
| 057 | HISTORY | filter EN from RU selection | detail switches to visible EN row | stale-detail regression | PASS |
| 058 | HISTORY | filter returns no rows | empty states; stale transcript cleared | history UI test | PASS |
| 059 | HISTORY | status filter | all visible rows match status | history property planned | GAP |
| 060 | HISTORY | search Unicode text | matching rows only | repository benchmark planned | GAP |
| 061 | HISTORY | refresh removes selection | first visible selected or detail clear | history regression | PASS |
| 062 | HISTORY | delete selected row | confirmation; next row selected | history regression | PASS |
| 063 | HISTORY | delete final visible row | both panes show empty state | UI extension planned | GAP |
| 064 | HISTORY | copy selected transcript | exact Unicode clipboard content | UI test | PASS |
| 065 | HISTORY | export selected transcript | safe chosen path and exact contents | export/path test planned | GAP |
| 066 | SETTINGS | load defaults | safe Balanced/local/history defaults | settings tests | PASS |
| 067 | SETTINGS | change every visible field → Save | normalized settings emitted | dialog test | PASS |
| 068 | SETTINGS | restart after Save | settings round-trip equality | repository test | PASS |
| 069 | SETTINGS | invalid JSON | corrupt copy retained; defaults load | settings corruption | PASS |
| 070 | SETTINGS | legacy schema | migration preserves known values | migration test | PASS |
| 071 | SETTINGS | unknown future field | known fields safe; no crash | property/fault planned | GAP |
| 072 | SETTINGS | autostart enable/disable | only named HKCU value changes | startup tests | PASS |
| 073 | SETTINGS | occupied hotkey | clear conflict warning; button remains usable | hotkey conflict | PASS |
| 074 | SETTINGS | model profile change | required local model status displayed | UI/profile tests | PASS |
| 075 | SETTINGS | delete local data | explicit confirmation; scope respected | installed/manual | MANUAL |
| 076 | DIAGNOSTICS | open Diagnostics | system/audio/model/checks grouped | UI diagnostics | PASS |
| 077 | DIAGNOSTICS | self-test | 11 health checks and clear status | installed EXE | PASS |
| 078 | DIAGNOSTICS | copy report | values copied; no traceback | UI diagnostics | PASS |
| 079 | DIAGNOSTICS | export report path unavailable | actionable failure; app continues | fault planned | GAP |
| 080 | DIAGNOSTICS | open log/data folder | correct existing user path | installed/manual | MANUAL |
| 081 | ERROR/RECOVERY | ASR throws | PCM retained; retry/tail recovery | controller integration | PASS |
| 082 | ERROR/RECOVERY | ASR hangs | watchdog ends in recoverable/fatal session | controller watchdog | PASS |
| 083 | ERROR/RECOVERY | capture overflow | non-zero dropped count and explicit error | queue/capture test | PASS |
| 084 | ERROR/RECOVERY | disk spool corrupt | deterministic corruption error | spool test | PASS |
| 085 | ERROR/RECOVERY | failed insertion | transcript/journal retained; Copy offered | injection/UI tests | PASS |
| 086 | ERROR/RECOVERY | clipboard changes mid-paste | user clipboard not overwritten | injection test | PASS |
| 087 | ERROR/RECOVERY | crash after commit before delivery | journal recovers committed text | recovery test | PASS |
| 088 | ERROR/RECOVERY | unfinished session on restart | visible recovery action | installed crash smoke | MANUAL |
| 089 | ERROR/RECOVERY | database locked | no silent corruption; retry/actionable error | DB fault planned | GAP |
| 090 | ERROR/RECOVERY | model checksum failure | old installed model/staging safety | model test | PASS |
| 091 | INSTALL/UPDATE | portable run | version/self-test no install | packaged smoke | PASS |
| 092 | INSTALL/UPDATE | fresh per-user install | executable/self-test created | installer smoke | PASS |
| 093 | INSTALL/UPDATE | upgrade 0.2.2→0.2.3 | settings/history/models unchanged | hash comparison | PASS |
| 094 | INSTALL/UPDATE | installer run twice | repair succeeds; data unchanged | planned installed matrix | GAP |
| 095 | INSTALL/UPDATE | uninstall | application removed; user data policy preserved | installer smoke/manual data check | PASS |
| 096 | PRIVACY | normal technical logs | no transcript or PCM fields | log audit | PASS |
| 097 | PRIVACY | release scan | no audio/env/secrets/private paths/data | release cleanup | PASS |
| 098 | PRIVACY | clipboard temporary injection | original formats restored | clipboard tests | PASS |
| 099 | LONG/EDGE | synthetic 2-hour text equivalent | storage/search/export remain usable | large-data benchmark | GAP |
| 100 | LONG/EDGE | 100 session cycles | no linear RAM/thread/handle growth | resource cycle benchmark | GAP |
| 101 | LONG/EDGE | 10 simultaneous launches | exactly one owner/tray/hotkey/DB writer | installed chaos | GAP |
| 102 | LONG/EDGE | PC suspend during paused session | pause remains coherent after resume | power/controller test | PASS |
| 103 | LONG/EDGE | 10k history rows | search/filter/select/delete remain responsive | DB benchmark | GAP |
| 104 | LONG/EDGE | 200% DPI at 1366×768 | no inaccessible primary controls | DPI matrix | GAP |
