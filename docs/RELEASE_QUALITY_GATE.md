# VoxType release quality gate

Final QA decision for the prepared and installed VoxType 0.2.4 build on
2026-07-29. A gate is marked PASS only from completed observable evidence.

| GATE | REQUIRED | ACTUAL | STATUS |
|---|---|---|---|
| BLOCKER / HIGH production defects | 0 / 0 | 0 / 0 reproducible after fixes | PASS |
| Lost / duplicate committed fragments | 0 / 0 | deterministic core/state/fault tests: 0 / 0 | PASS |
| Critical state model | safe valid/invalid actions | 10,000 fixed-seed actions plus 9,330 exhaustive sequences | PASS |
| Critical mutation gate | no critical survivors | 13 killed, 0 survived, 100% | PASS |
| Fault injection | critical deterministic cases safe | 6/6 new fault tests plus existing recovery/delivery faults | PASS |
| Flaky critical suite | 20 consecutive passes | 20/20, 0 failures | PASS |
| Single instance | one runtime owner for 2/5/10 launches | one ready transition in every installed-EXE run | PASS |
| Crash/data recovery | committed text retained; corrupt DB does not block start | journal tests PASS; corrupt DB quarantined and replaced | PASS |
| DB integrity/performance | integrity PASS; 10k responsive | `integrity=ok`; recent 21.425 ms; search 2.518 ms | PASS |
| Controlled insertion | >=99%; lost/duplicate 0 | 88/90 exact final fragments observed (97.78%); 2 VS Code fragments absent | **FAIL** |
| Clipboard restoration | original content restored | PASS for completed Notepad/VS Code/Chrome runs; aborted warm-ups unscored | PASS |
| UI responsiveness | no unexplained stall >500 ms | max measured stall 122.896 ms | PASS |
| Ruff / Mypy / Pytest | PASS | Ruff PASS; Mypy 91 files PASS; 247 passed, 5 deselected | PASS |
| Installed EXE | version/self-test/UI/diagnostics/inference | 0.2.4.0; all requested process exits 0 | PASS |
| Upgrade preservation | settings/history/models unchanged | both SHA-256 unchanged; four model directories retained | PASS |
| Release artifacts | portable/installer/checksums | both artifacts built and smoke-tested | PASS |

## Insertion verifier decision

- Notepad is complete evidence: 30 attempts, 30 successful, 0 failures,
  0 lost, 0 duplicates, clipboard restored, exact file verified.
- The first VS Code run reported 30 `INSERTED` operations, but the isolated
  saved file contained only 28 distinct expected fragments. This is scored as
  28 successful, 2 failures/lost and 0 duplicates.
- The first Chrome field contained all 30 expected fragments. Its original
  verifier rejected the text only because Windows CRLF was compared with LF.
- The corrected reruns used unique fixtures and safe warm-up confirmation.
  Both stopped before scored attempts because the active desktop session did
  not keep the intended editor caret. Placeholder failure counts emitted for
  those aborted runs are not insertion attempts and are not added to totals.
- The remaining VS Code discrepancy is not enough to identify a production
  delivery defect: the same run returned `INSERTED` and restored the clipboard,
  while the fixture did not prove stable editor focus. It is also not enough to
  claim PASS. A quiet interactive desktop rerun is required.

## Verdict

**QUALITY GATE FAILED**

Reason: the controlled insertion requirement of at least 99% with zero lost
fragments was not demonstrated. No production change was made for an
unconfirmed foreground/verifier failure.
