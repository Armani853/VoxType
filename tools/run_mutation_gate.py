"""Run a bounded, deterministic mutation gate for VoxType critical core logic.

The runner works from an isolated temporary copy.  It is intentionally small
and curated because the supported mutmut release does not run natively on
Windows and mutating the Qt/UI surface would exceed the release timebox.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Mutation:
    name: str
    module: str
    original: str
    replacement: str
    tests: tuple[str, ...]
    occurrence: int = 1


CORE_TESTS = (
    "tests/unit/test_core_state_machine.py",
    "tests/unit/test_transcript.py",
    "tests/unit/test_settings_sessions_metrics.py",
    "tests/unit/test_windows_injection.py",
    "tests/property/test_core_properties.py",
    "tests/concurrency/test_concurrency_races.py",
)

MUTATIONS = (
    Mutation(
        "state_accept_same_state",
        "src/voxtype/core/state_machine.py",
        "if target == previous:",
        "if target != previous:",
        ("tests/unit/test_core_state_machine.py", "tests/stateful/test_user_action_model.py"),
    ),
    Mutation(
        "state_invert_transition_guard",
        "src/voxtype/core/state_machine.py",
        "if target not in _ALLOWED[previous]:",
        "if target in _ALLOWED[previous]:",
        ("tests/unit/test_core_state_machine.py", "tests/stateful/test_user_action_model.py"),
    ),
    Mutation(
        "state_invert_atomic_expected_guard",
        "src/voxtype/core/state_machine.py",
        "if self._state not in expected:",
        "if self._state in expected:",
        ("tests/unit/test_core_state_machine.py", "tests/concurrency/test_concurrency_races.py"),
    ),
    Mutation(
        "merger_accept_replayed_sequence",
        "src/voxtype/transcript/merger.py",
        "if fragment.sequence <= self._last_sequence:",
        "if fragment.sequence < self._last_sequence:",
        ("tests/unit/test_transcript.py",),
    ),
    Mutation(
        "merger_marks_every_result_duplicate",
        "src/voxtype/transcript/merger.py",
        "overlap_count > 0)",
        "overlap_count >= 0)",
        ("tests/unit/test_transcript.py",),
    ),
    Mutation(
        "merger_deduplicates_nonoverlap",
        "src/voxtype/transcript/merger.py",
        "if chunk_start_s >= self._last_chunk_end_s - 0.02:",
        "if chunk_start_s < self._last_chunk_end_s - 0.02:",
        ("tests/unit/test_transcript.py",),
    ),
    Mutation(
        "journal_skips_sequence",
        "src/voxtype/sessions/journal.py",
        "self._sequence += 1",
        "self._sequence += 2",
        (
            "tests/unit/test_settings_sessions_metrics.py",
            "tests/property/test_core_properties.py",
            "tests/concurrency/test_concurrency_races.py",
        ),
    ),
    Mutation(
        "journal_rejects_nonempty_text",
        "src/voxtype/sessions/journal.py",
        "if not text:",
        "if text:",
        (
            "tests/unit/test_settings_sessions_metrics.py",
            "tests/property/test_core_properties.py",
        ),
    ),
    Mutation(
        "settings_invert_missing_file",
        "src/voxtype/settings/repository.py",
        "if not self.path.exists():",
        "if self.path.exists():",
        (
            "tests/unit/test_settings_sessions_metrics.py",
            "tests/property/test_core_properties.py",
        ),
    ),
    Mutation(
        "settings_drop_unknown_field_filter",
        "src/voxtype/settings/repository.py",
        "if item.name not in values:",
        "if item.name in values:",
        (
            "tests/unit/test_settings_sessions_metrics.py",
            "tests/property/test_core_properties.py",
        ),
    ),
    Mutation(
        "settings_reverse_atomic_replace",
        "src/voxtype/settings/repository.py",
        "os.replace(temporary, self.path)",
        "os.replace(self.path, temporary)",
        (
            "tests/unit/test_settings_sessions_metrics.py",
            "tests/fault/test_fault_injection.py",
        ),
    ),
    Mutation(
        "delivery_replays_first_record",
        "src/voxtype/windows/injection.py",
        "self._read_offset = self._stream.tell()",
        "self._read_offset = 0",
        ("tests/unit/test_windows_injection.py",),
    ),
    Mutation(
        "delivery_does_not_decrement_count",
        "src/voxtype/windows/injection.py",
        "self._count -= 1",
        "self._count += 1",
        ("tests/unit/test_windows_injection.py",),
    ),
)


def _replace_occurrence(text: str, mutation: Mutation) -> str:
    start = -1
    for _ in range(mutation.occurrence):
        start = text.find(mutation.original, start + 1)
        if start < 0:
            raise RuntimeError(
                f"{mutation.name}: occurrence {mutation.occurrence} not found in {mutation.module}"
            )
    return text[:start] + mutation.replacement + text[start + len(mutation.original) :]


def _run(
    workspace: Path, tests: tuple[str, ...], timeout: float
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", *tests, "-q", "--disable-warnings"],
        cwd=workspace,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=45.0)
    arguments = parser.parse_args()
    source_root = Path(__file__).resolve().parents[1]
    killed: list[str] = []
    survived: list[str] = []
    timed_out: list[str] = []

    with tempfile.TemporaryDirectory(prefix="voxtype-mutation-") as temporary:
        workspace = Path(temporary)
        shutil.copytree(source_root / "src", workspace / "src")
        shutil.copytree(source_root / "tests", workspace / "tests")
        shutil.copy2(source_root / "pyproject.toml", workspace / "pyproject.toml")
        baseline = _run(workspace, CORE_TESTS, arguments.timeout)
        if baseline.returncode:
            print("MUTATION_BASELINE_FAILED")
            print(baseline.stdout[-4000:])
            return 2

        for mutation in MUTATIONS:
            module = workspace / mutation.module
            original_text = module.read_text(encoding="utf-8")
            module.write_text(
                _replace_occurrence(original_text, mutation),
                encoding="utf-8",
                newline="\n",
            )
            try:
                result = _run(workspace, mutation.tests, arguments.timeout)
            except subprocess.TimeoutExpired:
                timed_out.append(mutation.name)
                status = "TIMEOUT"
            else:
                if result.returncode:
                    killed.append(mutation.name)
                    status = "KILLED"
                else:
                    survived.append(mutation.name)
                    status = "SURVIVED"
            finally:
                module.write_text(original_text, encoding="utf-8", newline="\n")
            print(f"MUTANT {mutation.name} {status}")

    total = len(MUTATIONS)
    score = 100.0 * len(killed) / total
    print(
        f"MUTATION_SUMMARY total={total} killed={len(killed)} "
        f"survived={len(survived)} timeout={len(timed_out)} score={score:.2f}%"
    )
    if survived:
        print(f"SURVIVORS {','.join(survived)}")
    if timed_out:
        print(f"TIMEOUTS {','.join(timed_out)}")
    return 1 if survived or timed_out else 0


if __name__ == "__main__":
    raise SystemExit(main())
