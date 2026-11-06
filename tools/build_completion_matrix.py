from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

root = Path(__file__).resolve().parents[1]
out = root / "docs" / "MASTER_COMPLETION"
out.mkdir(parents=True, exist_ok=True)
source = root / "docs" / "TZ_COMPLIANCE_MATRIX.md"
lines = source.read_text(encoding="utf-8").splitlines()

requirements: list[dict[str, str]] = []
section = ""
current: dict[str, str] | None = None


def add(
    prompt: int,
    description: str,
    priority: str,
    criterion: str,
    status: str = "IMPLEMENTED_UNVERIFIED",
    evidence: str = "",
    version: str = "",
    code: str = "—",
    test: str = "—",
    next_action: str = "Revalidate on current build",
) -> None:
    number = 1 + sum(item["prompt"] == f"PROMPT-{prompt:02}" for item in requirements)
    requirements.append(
        dict(
            id=f"P{prompt:02}-{number:03}",
            prompt=f"PROMPT-{prompt:02}",
            description=description,
            priority=priority,
            criterion=criterion,
            status=status,
            evidence=evidence or "Historical claim only; no current-version proof mapped",
            version=version or "historical / current unverified",
            code=code,
            test=test,
            next_action=next_action,
        )
    )


for line in lines:
    match = re.match(r"^##\s+(\d+)\.\s+(.+)", line)
    if match:
        section = f"§{match.group(1)} {match.group(2)}"
        current = None
        continue
    match = re.match(r"^\s*- \[([x! ])\]\s+(.+)", line)
    if match:
        status = "BLOCKED" if match.group(1) == "!" else "IMPLEMENTED_UNVERIFIED"
        add(
            1,
            f"{section}: {match.group(2)}",
            "P1",
            "Requirement works in relevant current-version test",
            status=status,
            evidence=f"docs/TZ_COMPLIANCE_MATRIX.md ({section}); dated 2026-07-24",
            code="See historical matrix",
            test="Historical evidence requires mapping",
            next_action="Map to code, current test and installed EXE evidence",
        )
        current = requirements[-1]
    elif current and line.startswith("  ") and line.strip() and not line.lstrip().startswith("-"):
        current["description"] += " " + line.strip()
    elif line == "":
        current = None

# Additional prompts are represented by distinct observable gates. Their detailed
# originals remain the authoritative source; this is a conservative starter audit.
groups: dict[int, list[tuple[str, str, str, str, str]]] = {
    2: [
        (
            "Audit complete product against TZ",
            "P1",
            "IMPLEMENTED_UNVERIFIED",
            "docs/PRODUCT_REBUILD/00_BASELINE_AUDIT.md",
            "Recheck all TZ links",
        ),
        (
            "Missing-model state explains unavailable dictation",
            "P1",
            "IMPLEMENTED_UNVERIFIED",
            "docs/PRODUCT_REBUILD/09_REMAINING_RISKS.md",
            "Clean-profile installed GUI retest",
        ),
        (
            "Audio settings fit at 900 and 760 px",
            "P1",
            "IMPLEMENTED_UNVERIFIED",
            "docs/PRODUCT_REBUILD/09_REMAINING_RISKS.md",
            "Packaged Computer Use retest",
        ),
        (
            "Onboarding completes with real RU and EN speech",
            "P1",
            "BLOCKED",
            "FA-004",
            "Controlled audio capture path",
        ),
        (
            "No known critical product defect",
            "P0",
            "FAILED",
            "FA-008 remains open",
            "Resolve ASR latency",
        ),
        (
            "Professional GUI UX accepted by customer simulation",
            "P1",
            "BLOCKED",
            "Final acceptance matrix incomplete",
            "Full GUI journey",
        ),
    ],
    3: [
        (
            "No unexplained app hang or blank window",
            "P0",
            "IMPLEMENTED_UNVERIFIED",
            "FA-009 fixed for two activations",
            "100 installed cycles",
        ),
        (
            "No Windows fatal diagnostic in test process",
            "P1",
            "FAILED",
            "FA-001: 0x8001010d recurs",
            "Investigate Qt native cause",
        ),
        (
            "Fault injection and concurrency tests pass",
            "P1",
            "VERIFIED",
            "scripts/test.ps1: 261 passed on 2026-11-06",
            "Retest after runtime changes",
        ),
        (
            "60-minute soak on current installed build",
            "P1",
            "BLOCKED",
            "Historical soak is a different binary",
            "Run current packaged soak",
        ),
        (
            "No loss of committed transcript on injection failure",
            "P0",
            "IMPLEMENTED_UNVERIFIED",
            "Historical tests only",
            "Map current fault test to behavior",
        ),
        (
            "Audio overflow is recoverable and accurately explained",
            "P1",
            "IMPLEMENTED_UNVERIFIED",
            "FA-007 source fix, packaged retest missing",
            "Computer Use live retry",
        ),
    ],
    4: [
        (
            "First window paints correctly",
            "P0",
            "VERIFIED",
            "Installed 9104e3a first launch painted in Computer Use",
            "Repeat at required volume",
        ),
        (
            "Tray hidden window restores on duplicate activation",
            "P0",
            "VERIFIED",
            "FA-009: two installed activations painted",
            "Extend to 100 cycles",
        ),
        (
            "100 consecutive installed launch and exit cycles",
            "P0",
            "BLOCKED",
            "Only two current candidate activations",
            "Run full series",
        ),
        (
            "Idle close exits without orphan process",
            "P0",
            "IMPLEMENTED_UNVERIFIED",
            "Earlier candidate two X exits",
            "Retest current installed EXE",
        ),
        (
            "No black/white frames under repeated stress",
            "P0",
            "IMPLEMENTED_UNVERIFIED",
            "Historical test on different binary",
            "Current build stress",
        ),
    ],
    5: [
        (
            "Complete disk inventory and top-50 files",
            "P3",
            "IMPLEMENTED_UNVERIFIED",
            "docs/OPTIMIZATION/01_DISK_USAGE_AUDIT.md",
            "Refresh after builds",
        ),
        (
            "Dry-run cleanup plan classifies unknown files",
            "P3",
            "IMPLEMENTED_UNVERIFIED",
            "docs/OPTIMIZATION/02_CLEANUP_PLAN.md",
            "Review classifications",
        ),
        (
            "Safe cleanup produces measured reduction",
            "P3",
            "FAILED",
            "Historical report says zero bytes freed",
            "Clean only approved regenerated artifacts",
        ),
        (
            "Module audit and dependency graph complete",
            "P3",
            "IMPLEMENTED_UNVERIFIED",
            "docs/OPTIMIZATION/03_CODEBASE_AUDIT.md",
            "Map every module and dependency",
        ),
        (
            "Before/after runtime and package metrics comparable",
            "P2",
            "BLOCKED",
            "docs/OPTIMIZATION/04_BEFORE_AFTER.md lacks final packaged metrics",
            "Measure current candidate",
        ),
    ],
    6: [
        (
            "Build stages timed separately",
            "P2",
            "NOT_STARTED",
            "No build report found",
            "Instrument next necessary build",
        ),
        (
            "Nuitka compilation report and cache rate measured",
            "P2",
            "NOT_STARTED",
            "No report found",
            "Generate report on next build",
        ),
        (
            "DEV/QA/RELEASE commands distinct",
            "P2",
            "IMPLEMENTED_UNVERIFIED",
            "scripts/run.ps1, lint.ps1, test.ps1, build scripts",
            "Document and benchmark modes",
        ),
        (
            "First content paint target <=700 ms assessed",
            "P2",
            "FAILED",
            "Visible packaged smoke measured 1033.5 ms once",
            "Profile repeated first paints",
        ),
        (
            "UI p95 <=50 ms and settings <=150 ms assessed",
            "P2",
            "BLOCKED",
            "No current installed distribution",
            "Computer Use latency series",
        ),
        (
            "Idle shutdown <=1 s assessed",
            "P2",
            "BLOCKED",
            "No precise current measurement",
            "Measure installed close",
        ),
        (
            "ASR outlier root cause measured",
            "P1",
            "FAILED",
            "FA-008: 24.738 s for 2.236 s audio",
            "Profile CPU/backend",
        ),
    ],
    7: [
        (
            "Real Computer Use can inspect and click Windows GUI",
            "P1",
            "VERIFIED",
            "sky UI wizard/click/screenshots on 2026-11-06",
            "Preserve evidence",
        ),
        (
            "All visible controls tested twice with backend effect",
            "P1",
            "BLOCKED",
            "Partial Settings and language UI only",
            "Build control inventory",
        ),
        (
            "Hotkeys, tray and overlay accepted",
            "P1",
            "BLOCKED",
            "Only tray restoration partial",
            "Computer Use matrix",
        ),
        (
            "Windows insertion app matrix accepted",
            "P1",
            "BLOCKED",
            "No controlled speech to target apps",
            "End-to-end corpus",
        ),
        (
            "Installer and uninstall GUI path accepted",
            "P1",
            "IMPLEMENTED_UNVERIFIED",
            "Isolated installer verified; production AppId untested",
            "Safe production upgrade test",
        ),
    ],
    8: [
        *(
            (
                f"{lang} {duration}s speech through normal capture path",
                "P1",
                "BLOCKED",
                "No controlled speech corpus or capture route",
                "Generate audio and route to PortAudio",
            )
            for lang in ("RU", "EN")
            for duration in (5, 20, 40, 60, 90)
        ),
        (
            "Reference and recognized text with WER/CER for all 10",
            "P1",
            "BLOCKED",
            "No ten-scenario corpus",
            "Run full corpus",
        ),
        (
            "Insertion and clipboard restoration after every scenario",
            "P1",
            "BLOCKED",
            "No controlled test",
            "Capture target field and clipboard",
        ),
    ],
    9: [
        (
            "Release freeze links commit, EXE and checksum",
            "P1",
            "VERIFIED",
            "9104e3a EXE SHA recorded; 4167c4e installer checksum",
            "Repeat after runtime change",
        ),
        (
            "Clean isolated install and first GUI launch",
            "P1",
            "VERIFIED",
            "Installed 9104e3a via Computer Use",
            "Repeat final release",
        ),
        (
            "100 start and exit cycles",
            "P0",
            "BLOCKED",
            "Two activations only on current installed EXE",
            "Run 100",
        ),
        (
            "RU/EN dictation and external-app insertion",
            "P1",
            "BLOCKED",
            "No controlled voice",
            "Run corpus",
        ),
        (
            "Failure recovery and all GUI controls",
            "P1",
            "BLOCKED",
            "Partial observed",
            "Complete test matrix",
        ),
        (
            "Installer running-app guard works",
            "P1",
            "VERIFIED",
            "FA-010 isolated package Computer Use retest",
            "Production AppId test",
        ),
        (
            "Production-AppId upgrade and uninstall",
            "P1",
            "BLOCKED",
            "Would touch existing installation",
            "Isolate clean environment",
        ),
        (
            "Final commercial verdict supported by all gates",
            "P0",
            "FAILED",
            "NOT READY in 06_FINAL_RELEASE_VERDICT.md",
            "Close gates",
        ),
    ],
    10: [
        (
            "Market research uses current comparable sources",
            "P3",
            "NOT_STARTED",
            "Explicitly deferred until acceptance",
            "Research after approval",
        ),
        (
            "Five sale-rights formats priced in RUB",
            "P3",
            "NOT_STARTED",
            "Explicitly deferred until acceptance",
            "Price after approval",
        ),
        (
            "Customer recommendation, floor and risks documented",
            "P3",
            "NOT_STARTED",
            "Explicitly deferred until acceptance",
            "Write valuation last",
        ),
    ],
}
for prompt, items in groups.items():
    for description, priority, status, evidence, action in items:
        add(
            prompt,
            description,
            priority,
            "Current-version observable outcome",
            status,
            evidence,
            "9104e3a / 4167c4e where stated",
            next_action=action,
        )


def esc(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").strip()


matrix = [
    "# VoxType master requirements matrix",
    "",
    "Snapshot: 2026-11-06 (Europe/Moscow). This is a **conservative preliminary audit**. The 421 historical TZ checkbox entries are transcribed as individual rows; the original 40-section TZ text was not found, so its completeness cannot be asserted. Historical `[x]` is not current-build verification. Additional prompt rows are observable gates recovered from available user requests and attached originals, with detailed decomposition still required.",
    "",
    "Statuses: NOT_STARTED, IN_PROGRESS, IMPLEMENTED_UNVERIFIED, VERIFIED, FAILED, BLOCKED, NOT_APPLICABLE. A VERIFIED row names specific evidence and scope; it is not a blanket product approval.",
    "",
    "| ID | Source | Requirement | Priority | Done when | Related code | Related test | Evidence | Verified version | Status | Reason / next action |",
    "|---|---|---|---|---|---|---|---|---|---|---|",
]
for r in requirements:
    reason = "—" if r["status"] == "VERIFIED" else r["next_action"]
    matrix.append(
        "| "
        + " | ".join(
            esc(r[key])
            for key in (
                "id",
                "prompt",
                "description",
                "priority",
                "criterion",
                "code",
                "test",
                "evidence",
                "version",
                "status",
            )
        )
        + f" | {esc(reason)} |"
    )
(out / "01_REQUIREMENTS_MATRIX.md").write_text("\n".join(matrix) + "\n", encoding="utf-8")

score = [
    "# Master completion scorecard",
    "",
    "Snapshot 2026-11-06. Formula: 100 × VERIFIED / applicable rows. This is a **lower-bound preliminary score for mapped requirements**, not a precise percentage of the unavailable original TZ or every paragraph of the long prompts. Each row in `01_REQUIREMENTS_MATRIX.md` contributes once. FAILED and BLOCKED count in the denominator. No NOT_APPLICABLE exclusions were made.",
    "",
    "| Prompt | Verified | Applicable | Completion | Implemented unverified | Failed | Blocked | Not started | In progress | Not applicable | Blocking issue |",
    "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
]
blockers = {
    1: "Current installed GUI and original TZ source not fully mapped",
    2: "Onboarding, model state and UX",
    3: "FA-001, FA-008, current soak",
    4: "100 current cycles",
    5: "No measured cleanup or full runtime comparison",
    6: "Startup/ASR targets and build report",
    7: "Full controls and insertion",
    8: "All ten capture-path speech tests",
    9: "End-to-end buyer journey",
    10: "Deferred until acceptance",
}
for prompt in range(1, 11):
    subset = [r for r in requirements if r["prompt"] == f"PROMPT-{prompt:02}"]
    c = Counter(r["status"] for r in subset)
    applicable = len(subset) - c["NOT_APPLICABLE"]
    pct = 100 * c["VERIFIED"] / applicable if applicable else 0
    score.append(
        f"| PROMPT-{prompt:02} | {c['VERIFIED']} | {applicable} | {pct:.1f}% | {c['IMPLEMENTED_UNVERIFIED']} | {c['FAILED']} | {c['BLOCKED']} | {c['NOT_STARTED']} | {c['IN_PROGRESS']} | {c['NOT_APPLICABLE']} | {blockers[prompt]} |"
    )
score += [
    "",
    "The low score for PROMPT-01 reflects strict remapping of historical claims to current-version evidence, not proof that 421 features are absent. P0/P1 blockers override any aggregate percentage. The next iteration must add the missing detailed prompt clauses and link each applicable TZ entry to a specific current test or GUI result.",
]
(out / "02_COMPLETION_SCORECARD.md").write_text("\n".join(score) + "\n", encoding="utf-8")
print(f"Wrote {len(requirements)} rows: {dict(Counter(r['prompt'] for r in requirements))}")
