# Master completion scorecard

Snapshot 2026-11-06. Formula: 100 × VERIFIED / applicable rows. This is a **lower-bound preliminary score for mapped requirements**, not a precise percentage of the unavailable original TZ or every paragraph of the long prompts. Each row in `01_REQUIREMENTS_MATRIX.md` contributes once. FAILED and BLOCKED count in the denominator. No NOT_APPLICABLE exclusions were made.

| Prompt | Verified | Applicable | Completion | Implemented unverified | Failed | Blocked | Not started | In progress | Not applicable | Blocking issue |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| PROMPT-01 | 0 | 421 | 0.0% | 419 | 0 | 2 | 0 | 0 | 0 | Current installed GUI and original TZ source not fully mapped |
| PROMPT-02 | 0 | 6 | 0.0% | 3 | 1 | 2 | 0 | 0 | 0 | Onboarding, model state and UX |
| PROMPT-03 | 1 | 6 | 16.7% | 3 | 1 | 1 | 0 | 0 | 0 | FA-001, FA-008, current soak |
| PROMPT-04 | 2 | 5 | 40.0% | 2 | 0 | 1 | 0 | 0 | 0 | 100 current cycles |
| PROMPT-05 | 0 | 5 | 0.0% | 3 | 1 | 1 | 0 | 0 | 0 | No measured cleanup or full runtime comparison |
| PROMPT-06 | 0 | 7 | 0.0% | 1 | 2 | 2 | 1 | 1 | 0 | Startup/ASR targets and build report |
| PROMPT-07 | 1 | 5 | 20.0% | 1 | 0 | 3 | 0 | 0 | 0 | Full controls and insertion |
| PROMPT-08 | 0 | 12 | 0.0% | 0 | 0 | 12 | 0 | 0 | 0 | All ten capture-path speech tests |
| PROMPT-09 | 3 | 8 | 37.5% | 0 | 1 | 4 | 0 | 0 | 0 | End-to-end buyer journey |
| PROMPT-10 | 0 | 3 | 0.0% | 0 | 0 | 0 | 3 | 0 | 0 | Deferred until acceptance |

The low score for PROMPT-01 reflects strict remapping of historical claims to current-version evidence, not proof that 421 features are absent. P0/P1 blockers override any aggregate percentage. The next iteration must add the missing detailed prompt clauses and link each applicable TZ entry to a specific current test or GUI result.
