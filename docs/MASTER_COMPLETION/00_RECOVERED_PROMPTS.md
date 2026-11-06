# Recovered VoxType instructions and evidence scope

Snapshot: 2026-11-06 MSK. Current Git HEAD at recovery: `4167c4eae5dd9c2e61841cac8ba4298573ec8ba6`. The subsequent changes to two final-acceptance reports are this audit's own work. No VoxType process was running when this recovery began. The historical PID 26900 was not treated as current.

| Prompt | Available source | Recovery limit |
|---|---|---|
| 01 Original TZ | `docs/TZ_COMPLIANCE_MATRIX.md`: 40 numbered sections and 421 checkbox entries, dated 2026-07-24; `TASKS.md`, `PLAN.md` | Original standalone TZ document was not located. Historical matrix is a derivative and its `[x]` marks are not proof on the current EXE. |
| 02 Product Rebuild | Attached `Вставленный текст.txt` under attachment `e995ba8c-7888-427f-8def-80cf4b6e7abe`; duplicate under `aff347dd-e42b-43ab-b359-3183a6a853bd`; `docs/PRODUCT_REBUILD/` | Attached originals present; matrix currently contains only high-priority gates, so its denominator is preliminary. |
| 03 Extreme Stability | Attached `Вставленный текст.txt` under `249c0053-35fb-4573-be96-54573004b2f9`; `docs/STABILITY_AUDIT/` | Historical 100-cycle and soak claims concern earlier binaries. |
| 04 Emergency GUI Fix | User requests in conversation; `docs/STABILITY_AUDIT/CRITICAL_GUI_FREEZE_INVESTIGATION.md`; FA-009 | Full original prompt text may be in earlier conversation context not saved as one project file. |
| 05 Codebase Optimization | User request in this conversation; `docs/OPTIMIZATION/` | Existing reports document zero bytes freed and incomplete packaged comparison. |
| 06 Build & Runtime Performance | User requests in this conversation; `scripts/` | No dedicated build-stage report yet. |
| 07 Full Computer Use | User requests in this conversation; `docs/FINAL_CUSTOMER_ACCEPTANCE/` | Real Computer Use was available via `@oai/sky`; only a subset of UI paths was exercised. |
| 08 Real-World Dictation | User requests in this conversation | Ten controlled capture-path scenarios and human-voice diversity are not documented. |
| 09 Final Customer Acceptance | User requests in this conversation; `docs/FINAL_CUSTOMER_ACCEPTANCE/` | The installed `9104e3a` candidate has partial UI and isolated installer results. |
| 10 Commercial Valuation | User requests in this conversation | Explicitly reserved for the last stage after technical and customer acceptance. |

Other requirements recovered from the user: preserve all functionality and models, do not delete unknown/user data, use bounded FIFO audio queues and journal text before insertion, do not log PCM/transcripts, do not add cloud STT/telemetry/local HTTP/voice commands, run lint/test before packaging, and make logical commits. `AGENTS.md` is authoritative for repository work.

The source inventory also includes `README.md`, `README_RU.md`, `docs/TZ_COMPLIANCE_MATRIX.md`, `docs/PRODUCT_REBUILD/`, `docs/STABILITY_AUDIT/`, `docs/OPTIMIZATION/`, `docs/FINAL_CUSTOMER_ACCEPTANCE/`, Git history and build scripts. `docs/PERFORMANCE_OPTIMIZATION/`, `docs/COMPUTER_USE_ACCEPTANCE/` and `docs/REAL_WORLD_ACCEPTANCE/` were absent at this snapshot. Their absence is an open deliverable, not a completed audit.
